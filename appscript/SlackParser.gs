/**
 * SlackParser.gs — read Slack, append to raw_messages. READ ONLY.
 *
 * ── IT CANNOT WRITE TO SLACK ────────────────────────────────────────────────────────────────
 * Not by discipline — by token. The app is installed with eight scopes, all of them :history or
 * :read, and no chat:write of any kind, so there is no write API reachable with this credential.
 * There is no send() in this file and there is no code path that could add one by accident.
 *
 * ── THE THREE FIELDS THAT ARE NOT WHAT SLACK SAYS ───────────────────────────────────────────
 *   · thread_ref is NULL on a parent, and the parent's ts on replies. Slack puts thread_ts == ts
 *     on the parent; that is normalised away here because grouping uses "thread_ref is empty"
 *     to mean top-level, and a parent that points at itself would make every thread its own
 *     orphan.
 *   · ts_iso is IST +05:30, never UTC Z, and must agree with message_id to the second.
 *   · permalink is CONSTRUCTED, not fetched. chat.getPermalink per record is unaffordable at
 *     this volume and the form is deterministic.
 *
 * ── WHY RE-FETCHING IS SAFE ─────────────────────────────────────────────────────────────────
 * The watermark is passed to Slack as `oldest`, which is INCLUSIVE — so the last message of the
 * previous run comes back every time. That is deliberate: it is cheaper to re-read one message
 * than to reason about an exclusive boundary. The tail-window key check below drops it, and
 * even if one ever escaped, every id downstream is derived from (channel_id, message_id), so a
 * duplicated row produces the same issue_id and the same ticket rather than a second one. The
 * one thing it would distort is reply_count, which is why the check exists at all.
 */

var RAW_HEADER = [
  'key', 'source_system', 'channel_id', 'channel_name', 'message_id', 'ts_epoch', 'ts_iso',
  'author_id', 'author_name', 'author_email', 'author_is_bot', 'text', 'subtype', 'thread_ref',
  'reply_count', 'has_media', 'permalink',
  // ── written back by Pipeline.gs, never by the parser ──
  'gated', 'gate_rule', 'informational', 'evidence_decision',
  'issue_id', 'assign_rule', 'confidence', 'assign_reason'
];
var RAW_COL = {};
for (var _i = 0; _i < RAW_HEADER.length; _i++) RAW_COL[RAW_HEADER[_i]] = _i;

var SLACK_API = 'https://slack.com/api/';

/** One GET. Every Slack call in this project goes through here, so "is there a write path"
 *  is answerable by reading one function. */
function slackGet_(method, params) {
  var url = SLACK_API + method;
  var qs = [];
  Object.keys(params || {}).forEach(function (k) {
    if (params[k] !== null && params[k] !== undefined && params[k] !== '') {
      qs.push(encodeURIComponent(k) + '=' + encodeURIComponent(params[k]));
    }
  });
  if (qs.length) url += '?' + qs.join('&');

  var res = UrlFetchApp.fetch(url, {
    method: 'get',
    headers: { Authorization: 'Bearer ' + slackToken_() },
    muteHttpExceptions: true
  });
  var d;
  try { d = JSON.parse(res.getContentText()); }
  catch (e) { throw new Error(method + ': non-JSON response (HTTP ' + res.getResponseCode() + ')'); }

  if (!d.ok) {
    var hint = {
      not_in_channel:    'invite the bot: /invite @<app> in that channel',
      channel_not_found: 'a PRIVATE channel is invisible until the bot is invited',
      invalid_auth:      'the token in Script Property SLACK_BOT_TOKEN is wrong or revoked',
      missing_scope:     'needs ' + d.needed + ', has ' + d.provided
    }[d.error] || '';
    throw new Error(method + ': ' + d.error + (hint ? ' — ' + hint : ''));
  }
  Utilities.sleep(CFG().slack.pauseMs);
  return d;
}

/** users.info, cached for the execution. A deleted user or a bot_id leaves the name null
 *  rather than failing the whole poll for one message. */
function slackUser_(uid) {
  if (!uid) return {};
  if (!_cache.users) _cache.users = {};
  if (!_cache.users.hasOwnProperty(uid)) {
    try { _cache.users[uid] = slackGet_('users.info', { user: uid }).user || {}; }
    catch (e) { _cache.users[uid] = {}; }
  }
  return _cache.users[uid];
}

/** The workspace host, for building permalinks. One call per execution. */
function slackHost_() {
  if (!_cache.host) {
    var a = slackGet_('auth.test');
    _cache.host = String(a.url).split('//')[1].replace(/\/+$/, '');
    _cache.teamId = a.team_id;
  }
  return _cache.host;
}

/** One Slack message as a raw_messages row. */
function toRow_(msg, channelId, channelName) {
  var ts = String(msg.ts);                       // STRING, verbatim. Never float-cast: the
  var threadTs = msg.thread_ts || null;          // trailing digits are significant.
  var text = msg.text || '';
  var uid = msg.user || msg.bot_id || '';
  var u = msg.user ? slackUser_(msg.user) : {};
  var prof = u.profile || {};
  var files = msg.files || [];

  var row = new Array(RAW_HEADER.length).fill('');
  row[RAW_COL.key]           = channelId + '|' + ts;
  row[RAW_COL.source_system] = 'slack';
  row[RAW_COL.channel_id]    = channelId;
  row[RAW_COL.channel_name]  = channelName || '';
  row[RAW_COL.message_id]    = ts;
  row[RAW_COL.ts_epoch]      = parseFloat(ts);
  row[RAW_COL.ts_iso]        = istStamp(parseFloat(ts));
  row[RAW_COL.author_id]     = uid;
  row[RAW_COL.author_name]   = u.real_name || prof.display_name || '';
  row[RAW_COL.author_email]  = prof.email || '';
  row[RAW_COL.author_is_bot] = !!(u.is_bot || msg.bot_id || msg.subtype === 'bot_message');
  row[RAW_COL.text]          = text;
  row[RAW_COL.subtype]       = msg.subtype || '';
  // NULL on the parent — the deliberate normalisation away from Slack's raw semantics.
  row[RAW_COL.thread_ref]    = (threadTs === ts) ? '' : (threadTs || '');
  row[RAW_COL.reply_count]   = Number(msg.reply_count || 0);
  row[RAW_COL.has_media]     = files.length > 0;
  row[RAW_COL.permalink]     = 'https://' + slackHost_() + '/archives/' + channelId +
                               '/p' + ts.replace('.', '');
  return row;
}

/**
 * The keys of the most recent rows, for dropping re-fetched messages.
 *
 * A tail window rather than the whole tab: at 400,000 rows a full key read is not affordable on
 * every poll. The window must comfortably exceed the number of rows the reply sweep can reach
 * back over — see CFG().slack.dedupeTailRows, and the note there about raising it if your
 * volume grows.
 */
function recentKeys_() {
  var sh = ss_().getSheetByName(CFG().tabs.raw);
  if (!sh) return new Set();
  var last = sh.getLastRow();
  if (last < 2) return new Set();
  var want = CFG().slack.dedupeTailRows;
  var from = Math.max(2, last - want + 1);
  var vals = sh.getRange(from, RAW_COL.key + 1, last - from + 1, 1).getValues();
  var s = new Set();
  for (var i = 0; i < vals.length; i++) s.add(String(vals[i][0]));
  return s;
}

/**
 * Poll every configured channel once. This is the 1-minute trigger's entry point.
 *
 * tryLock(0), never a wait: if the previous run is still going, this one exits immediately.
 * Queueing would stack executions against the 6-minute cap and the daily runtime budget, and a
 * poll that is one minute late is worth nothing compared to a poll that never finishes.
 */
function pollSlack() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(0)) { return { skipped: 'another run holds the lock' }; }
  try {
    var channels = channelIds_();
    if (!channels.length) throw new Error('Script Property CHANNELS is empty — nothing to poll.');

    var seen = recentKeys_();
    var newRows = [], chanStats = [];
    var coldStart = (Date.now() / 1000) - CFG().slack.coldStartDays * 86400;

    for (var c = 0; c < channels.length; c++) {
      var ch = channels[c];
      var stat = { channel_id: ch, name: '', fetched: 0, error: '' };
      try {
        var name = slackGet_('conversations.info', { channel: ch }).channel.name || '';
        stat.name = name;
        var mark = Number(stateGet_('ts:' + ch, 0)) || 0;
        var oldest = Math.max(mark, coldStart).toFixed(6);

        var raw = [], cursor = null, pages = 0;
        while (pages < CFG().slack.maxPages) {
          var d = slackGet_('conversations.history',
                            { channel: ch, limit: CFG().slack.pageLimit, oldest: oldest,
                              cursor: cursor });
          raw = raw.concat(d.messages || []);
          cursor = (d.response_metadata || {}).next_cursor;
          pages++;
          if (!cursor) break;
        }

        // Thread replies for parents in this window. conversations.replies returns the parent
        // first; it is dropped by ts, because it is already in `raw` and a duplicate would
        // break the no-op guarantee the composite key gives us.
        var parents = raw.filter(function (m) {
          return m.thread_ts === m.ts && Number(m.reply_count || 0) > 0;
        });
        for (var p = 0; p < parents.length; p++) {
          var rd = slackGet_('conversations.replies',
                             { channel: ch, ts: parents[p].ts, limit: 200 });
          (rd.messages || []).forEach(function (r) {
            if (r.ts !== parents[p].ts) raw.push(r);
          });
        }

        var maxTs = mark;
        for (var i = 0; i < raw.length; i++) {
          var row = toRow_(raw[i], ch, name);
          if (seen.has(row[RAW_COL.key])) continue;
          seen.add(row[RAW_COL.key]);
          newRows.push(row);
          // The watermark tracks TOP-LEVEL messages only. Advancing it past a reply's ts would
          // skip the parents between, and those parents are where issues get anchored.
          if (!row[RAW_COL.thread_ref]) maxTs = Math.max(maxTs, row[RAW_COL.ts_epoch]);
          stat.fetched++;
        }
        if (maxTs > mark) stateSet_('ts:' + ch, maxTs);
      } catch (e) {
        stat.error = String(e.message || e);
      }
      chanStats.push(stat);
    }

    newRows.sort(function (a, b) { return a[RAW_COL.ts_epoch] - b[RAW_COL.ts_epoch]; });
    appendRows_(CFG().tabs.raw, RAW_HEADER, newRows);
    writeChannelPanel_(chanStats);
    stateSetAll_({ 'poll:last_at': istStamp(Date.now() / 1000),
                   'poll:last_count': newRows.length,
                   'poll:count': Number(stateGet_('poll:count', 0)) + 1 });
    SpreadsheetApp.flush();
    return { appended: newRows.length, channels: chanStats };
  } finally {
    lock.releaseLock();
  }
}

/**
 * Re-read replies on recent threads. Separate 30-minute trigger.
 *
 * Why this exists: conversations.history returns a message at its OWN timestamp, so a thread
 * from Tuesday that gets a new reply on Thursday never reappears in Thursday's window — the
 * reply is simply never seen. For a ticketing system that is not cosmetic: the reply is the
 * first response, and without it the ticket never leaves NEW and the latency column stays
 * empty while somebody is actually working on it.
 *
 * It is a separate, slower trigger because doing it every minute would cost one API call per
 * open thread per minute and blow the daily budget. Every 30 minutes over a 3-day lookback is
 * a few thousand calls a day against an allowance of 100,000.
 */
function sweepThreadReplies() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(0)) return { skipped: 'another run holds the lock' };
  try {
    var cutoff = (Date.now() / 1000) - CFG().slack.sweepLookbackDays * 86400;
    var sh = ss_().getSheetByName(CFG().tabs.raw);
    if (!sh || sh.getLastRow() < 2) return { swept: 0 };

    var last = sh.getLastRow();
    var from = Math.max(2, last - CFG().slack.dedupeTailRows + 1);
    var vals = sh.getRange(from, 1, last - from + 1, RAW_HEADER.length).getValues();

    var seen = new Set(), parents = [];
    for (var i = 0; i < vals.length; i++) {
      seen.add(String(vals[i][RAW_COL.key]));
      var isParent = !vals[i][RAW_COL.thread_ref] && Number(vals[i][RAW_COL.reply_count] || 0) > 0;
      if (isParent && Number(vals[i][RAW_COL.ts_epoch]) >= cutoff) {
        parents.push({ channel_id: String(vals[i][RAW_COL.channel_id]),
                       name: String(vals[i][RAW_COL.channel_name]),
                       ts: String(vals[i][RAW_COL.message_id]) });
      }
    }

    var newRows = [];
    for (var p = 0; p < parents.length; p++) {
      try {
        var rd = slackGet_('conversations.replies',
                           { channel: parents[p].channel_id, ts: parents[p].ts, limit: 200 });
        (rd.messages || []).forEach(function (r) {
          if (r.ts === parents[p].ts) return;
          var row = toRow_(r, parents[p].channel_id, parents[p].name);
          if (seen.has(row[RAW_COL.key])) return;
          seen.add(row[RAW_COL.key]);
          newRows.push(row);
        });
      } catch (e) { /* one dead thread must not end the sweep */ }
    }
    newRows.sort(function (a, b) { return a[RAW_COL.ts_epoch] - b[RAW_COL.ts_epoch]; });
    appendRows_(CFG().tabs.raw, RAW_HEADER, newRows);
    stateSetAll_({ 'sweep:last_at': istStamp(Date.now() / 1000),
                   'sweep:threads': parents.length, 'sweep:new_replies': newRows.length });
    SpreadsheetApp.flush();
    return { threads: parents.length, appended: newRows.length };
  } finally {
    lock.releaseLock();
  }
}

var CHANNEL_HEADER = ['channel_id', 'name', 'last_poll', 'new_last_poll', 'total_seen',
                      'last_error', 'row_capacity'];

/** The listening-channels panel. This is the first thing anyone looks at to answer "is it
 *  actually listening", so it carries the errors rather than hiding them in a log. */
function writeChannelPanel_(stats) {
  var existing = {};
  readTabObjects_(CFG().tabs.channels).forEach(function (r) { existing[r.channel_id] = r; });
  var sh = ss_().getSheetByName(CFG().tabs.raw);
  var rows = sh ? Math.max(0, sh.getLastRow() - 1) : 0;
  var cap = Math.round(100 * rows / CFG().rawRowRotate) + '% of rotation';
  var now = istStamp(Date.now() / 1000);

  var out = stats.map(function (s) {
    var prev = existing[s.channel_id] || {};
    var total = Number(prev.total_seen || 0) + s.fetched;
    return [s.channel_id, s.name || prev.name || '', now, s.fetched, total, s.error || '', cap];
  });
  replaceRows_(CFG().tabs.channels, CHANNEL_HEADER, out);
}
