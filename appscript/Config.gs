/**
 * Config.gs — settings, secrets, tab plumbing, and the four primitives everything else needs.
 *
 * ── PASTE THIS FILE FIRST ───────────────────────────────────────────────────────────────────
 * Apps Script puts every .gs file into ONE global scope and runs top-level statements in
 * editor file order — an order you can change by dragging files in the sidebar. So a top-level
 * `const` here that another file's top-level `const` reads is a bug waiting for someone to
 * reorder the list. Nothing in this project has cross-file top-level state: every shared value
 * comes from a function (CFG(), LEX(), DCREG()) that builds on first call and memoises into a
 * `_cache` object. Order then cannot matter, because nothing runs until something calls it.
 *
 * ── SECRETS LIVE IN SCRIPT PROPERTIES, NOT HERE ─────────────────────────────────────────────
 * This file is committed. Project Settings → Script Properties is not. Four keys:
 *
 *   SLACK_BOT_TOKEN   xoxb-…    the read-only bot token
 *   CHANNELS          C123,C456 comma-separated channel ids to listen on
 *   INTAKE_NOTIFY     off | email-dry | email
 *   INTAKE_SECRET     any long random string — signs the partner status-page tokens
 *
 * A missing SLACK_BOT_TOKEN throws by name rather than failing as a 401 twenty lines later.
 */

var _cache = {};   // memo store for CFG / LEX / DCREG / exemplar index — per execution only


// ── settings ────────────────────────────────────────────────────────────────────────────────

/** Every tunable in one place. Numbers carry the comment explaining why they are that number. */
function CFG() {
  if (_cache.cfg) return _cache.cfg;
  _cache.cfg = {

    tabs: {
      raw:       'raw_messages',
      issues:    'issues',
      tickets:   'tickets',
      channels:  'channels',
      state:     '_state',
      dcCodes:   '_dc_codes',
      dcDeny:    '_dc_denylist',
      exemplars: '_exemplars'
    },

    // Slack read pacing. 0.2s between calls keeps us far under the tier-3 ~50/min limit while
    // leaving room inside the 6-minute execution cap.
    slack: {
      pauseMs:      200,
      pageLimit:    200,
      maxPages:     20,     // hard cap: 4,000 messages per channel per run
      coldStartDays: 7,     // first-ever poll of a channel reaches back this far

      // How far back sweepThreadReplies looks for threads that may have gained a reply since
      // they were last read. See the note in SlackParser.gs for why a separate sweep exists.
      sweepLookbackDays: 3,

      // How many trailing rows of raw_messages are read to drop already-ingested messages.
      // This MUST comfortably exceed the number of rows your channels produce in
      // sweepLookbackDays, or the sweep can re-append a reply it already has. At ~3,000
      // messages/day this covers over a week. If your volume grows past ~8,000/day, raise it.
      dedupeTailRows: 25000
    },

    // evidence.yaml thresholds/weights. request=0.4 so ops_noun(0.6)+request lands exactly on
    // the 1.0 issue threshold — "can we get the payout released?" must qualify.
    evidence: {
      issue: 1.0, weak: 0.5,
      w: { identifier: 1.0, opsNoun: 0.6, problem: 0.5, request: 0.4, urgency: 0.15 }
    },

    // grouping.yaml. A ticket number or a person stays the same issue for 180 days; one
    // shipment is done in 14. The LONGEST window among shared kinds wins.
    grouping: {
      defaultWindowS: 7 * 86400,
      windows: { kapture_id: 180 * 86400, mobile: 180 * 86400, pilot_id: 180 * 86400,
                 email: 180 * 86400, waybill: 14 * 86400 },
      joinOn:     ['kapture_id', 'waybill', 'mobile', 'pilot_id', 'email'],
      joinOnWeak: ['dc_code'],
      // dc_code identifies a PLACE, not an incident — joining on it merges every issue a hub
      // raised in a week into whichever came first. Off until measured.
      joinWeak: false,
      conf: { threadRef: 1.0, entityJoinStrong: 0.75, entityJoinWeak: 0.35, unassigned: 0.0 },
      adjudicateBelow: 0.50
    },

    // dedupe.py. 0.95 same-channel because two DIFFERENT problems from one person in one hour
    // is ordinary; near-verbatim is not ambiguous, merely similar is.
    dedupe: { windowS: 3600, textSimilarity: 0.72, sameChannelSimilarity: 0.95 },

    // classify.yaml. minScore does not bind on this corpus — the margin does all the work — but
    // it is the right guard for a new disposition with two examples.
    classify: { minScore: 3.0, minMargin: 0.15, topK: 5, goldWeight: 3.0, k1: 1.2, b: 0.6 },

    // Bounds on one pipeline run, so a backlog is worked off in slices instead of one
    // execution hitting the 6-minute cap and dying with nothing written. A run that stops at
    // the cap has already advanced nothing; the next trigger simply picks up where it left off.
    maxMessagesPerRun: 4000,

    // Trailing rows of the issues tab read to find open issues. Must exceed the number of
    // issues created inside the longest grouping window (180 days as shipped).
    issueTailRows: 30000,

    // Suppress a ticket with no DC code, mobile, waybill or ticket id? Depends on how the desk
    // works. Off means such tickets are created and flagged rather than withheld.
    requireIdentifier: false,

    // Measured at +17.9 precision points for zero coverage cost — the largest single lever we
    // have. Off because collapsing five dispositions into one changes how the desk routes work,
    // and that is a desk decision, not a code decision.
    mergeMoneyClasses: false,
    moneyClasses: ['payment_not_received', 'payment_reconciliation', 'cod_shortfall',
                   'cod_pendency', 'consumables_payment'],
    moneyMergedName: 'money',

    // ── THE HARD WALL ────────────────────────────────────────────────────────────────────
    // A Google Sheet holds 10,000,000 cells WORKBOOK-WIDE, across every tab. raw_messages has
    // 25 columns, so it alone tops out near 400,000 rows — and the other tabs are drawing on
    // the same budget. At 100k messages/month that is about four months; at 1M/month it is
    // twelve days. Rotation is not a nicety at the top of that range, it is the difference
    // between a working system and a parser that throws on every run with the day's messages
    // already aged out of Slack's cheap window.
    rawRowWarn:   260000,
    rawRowRotate: 340000,
    archiveKeepMonths: 3
  };
  return _cache.cfg;
}

function props_() { return PropertiesService.getScriptProperties(); }

/** A Script Property, or `dflt`. Empty string counts as absent — a cleared property is not a value. */
function prop_(key, dflt) {
  var v = props_().getProperty(key);
  if (v === null || v === undefined || String(v).trim() === '') return dflt;
  return String(v).trim();
}

/** The bot token. Throws by name — a 401 twenty lines downstream is a worse error message. */
function slackToken_() {
  var t = prop_('SLACK_BOT_TOKEN', '');
  if (!t) throw new Error('Script Property SLACK_BOT_TOKEN is not set. ' +
                          'Project Settings → Script Properties → Add script property.');
  return t;
}

/** Channel ids to listen on. This list REPLACES the Python channel_qualification stage: you are
 *  only in channels the bot was invited to, and scoring a list you wrote yourself is theatre. */
function channelIds_() {
  var raw = prop_('CHANNELS', '');
  if (!raw) return [];
  return raw.split(',').map(function (s) { return s.trim(); }).filter(function (s) { return s; });
}


// ── the four primitives ─────────────────────────────────────────────────────────────────────

/**
 * IST +05:30 to the second.
 *
 * Built from UTC plus a LITERAL offset, never from the 'Asia/Calcutta' timezone name — the two
 * disagree by a second on the boundary and the record then fails the field contract. This is
 * derived exactly the way tools/validate.js derives it, so the two cannot drift.
 */
function istStamp(epochSeconds) {
  return Utilities.formatDate(new Date((epochSeconds + 19800) * 1000), 'UTC',
                              "yyyy-MM-dd'T'HH:mm:ss") + '+05:30';
}

/**
 * sha256 of a string, as 64 lowercase hex characters.
 *
 * Three traps, all of them silent:
 *   1. Apps Script returns SIGNED bytes (-128..127). Without `& 0xFF` every high byte becomes
 *      a negative number and `toString(16)` yields something like "-6b".
 *   2. A byte below 0x10 renders as one character. Without the pad the digest is short and
 *      still looks like a hash.
 *   3. computeDigest must encode an embedded U+0000 as a single 0x00, not as Modified-UTF-8's
 *      0xC0 0x80. Tests.gs pins two golden vectors that fail loudly if it ever does the latter.
 */
function sha256Hex(s) {
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, s,
                                      Utilities.Charset.UTF_8);
  var hex = '';
  for (var i = 0; i < bytes.length; i++) {
    var b = bytes[i] & 0xFF;
    hex += (b < 16 ? '0' : '') + b.toString(16);
  }
  return hex;
}

/**
 * A string as an array of CODE POINTS.
 *
 * Python iterates str by code point; JS iterates by UTF-16 code unit. An emoji is 1 element in
 * Python and 2 in JS, which changes both the length and the match count in the similarity
 * ratio — and Slack messages have emoji. Anything doing length arithmetic on message text goes
 * through here first.
 */
function cp_(s) { return Array.from(s || ''); }

/** Python's round-half-away-from-zero at n decimal places, for the stored score strings. */
function round_(x, n) {
  if (x === null || x === undefined) return null;
  var f = Math.pow(10, n);
  return Math.round(x * f) / f;
}


// ── sheet plumbing ──────────────────────────────────────────────────────────────────────────
//
// One rule, and every Sheets performance problem in this project is a violation of it:
// ONE getValues() and ONE setValues() per tab per run. Never per row. A 500-row per-row write
// is ~500 round trips and will eat the 6-minute cap on its own.

function ss_() { return SpreadsheetApp.getActiveSpreadsheet(); }

/** The tab, created with `header` if absent. */
function sheet_(name, header) {
  var sh = ss_().getSheetByName(name);
  if (!sh) {
    sh = ss_().insertSheet(name);
    if (header && header.length) {
      sh.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold');
      sh.setFrozenRows(1);
    }
  }
  return sh;
}

/** Whole tab in one read: {header: [...], rows: [[...]]}. Empty tab gives empty arrays. */
function readTab_(name) {
  var sh = ss_().getSheetByName(name);
  if (!sh) return { header: [], rows: [] };
  var last = sh.getLastRow(), cols = sh.getLastColumn();
  if (last < 1 || cols < 1) return { header: [], rows: [] };
  var all = sh.getRange(1, 1, last, cols).getValues();
  return { header: all[0], rows: all.slice(1) };
}

/** Whole tab as objects keyed by header. Convenience over readTab_ — same single read. */
function readTabObjects_(name) {
  var t = readTab_(name), out = [];
  for (var i = 0; i < t.rows.length; i++) {
    var o = {};
    for (var j = 0; j < t.header.length; j++) o[t.header[j]] = t.rows[i][j];
    out.push(o);
  }
  return out;
}

/**
 * Rows `from`..`from+n-1` of a tab (1-based, EXCLUDING the header row).
 *
 * This is what makes the pipeline incremental: raw_messages is append-only, so "everything I
 * have not structured yet" is a contiguous block and costs one read regardless of how large
 * the tab has grown.
 */
function readTabSlice_(name, fromDataRow, count) {
  var sh = ss_().getSheetByName(name);
  if (!sh) return { header: [], rows: [] };
  var last = sh.getLastRow(), cols = sh.getLastColumn();
  if (last < 2 || cols < 1) return { header: [], rows: [] };
  var header = sh.getRange(1, 1, 1, cols).getValues()[0];
  var startSheetRow = fromDataRow + 1;                       // +1 for the header
  if (startSheetRow > last) return { header: header, rows: [] };
  var n = Math.min(count === undefined ? last : count, last - startSheetRow + 1);
  if (n <= 0) return { header: header, rows: [] };
  return { header: header, rows: sh.getRange(startSheetRow, 1, n, cols).getValues() };
}

/** Append in one write. Rows are padded/truncated to the tab's column count. */
function appendRows_(name, header, rows) {
  if (!rows || !rows.length) return 0;
  var sh = sheet_(name, header);
  var cols = Math.max(sh.getLastColumn(), header ? header.length : 0);
  var padded = rows.map(function (r) {
    var out = r.slice(0, cols);
    while (out.length < cols) out.push('');
    return out;
  });
  sh.getRange(sh.getLastRow() + 1, 1, padded.length, cols).setValues(padded);
  return padded.length;
}

/** Replace every data row in one write, keeping the header. */
function replaceRows_(name, header, rows) {
  var sh = sheet_(name, header);
  var cols = header.length;
  if (sh.getLastRow() > 1) sh.getRange(2, 1, sh.getLastRow() - 1, sh.getMaxColumns()).clearContent();
  if (!rows || !rows.length) return 0;
  var padded = rows.map(function (r) {
    var out = r.slice(0, cols);
    while (out.length < cols) out.push('');
    return out;
  });
  sh.getRange(2, 1, padded.length, cols).setValues(padded);
  return padded.length;
}

/**
 * Write many individual rows in as few Sheets calls as possible.
 *
 * `updates` is [{row, values}]. Rows are sorted and CONSECUTIVE runs are written with a single
 * setValues, because a Sheets round trip costs 10-50ms regardless of how much it carries and
 * that overhead is what actually binds.
 *
 * Measured before this existed: one call per updated row. A run that touched 2,000 issues made
 * 2,012 round trips — 20 to 100 seconds of pure overhead — and it grew linearly, so ~10,000
 * updates would have exceeded the 6-minute execution cap outright. Updated issues are almost
 * always the recent ones, which sit next to each other at the tail of the tab, so in practice
 * this collapses to a handful of calls.
 */
function writeRowsBatched_(name, width, updates) {
  if (!updates || !updates.length) return 0;
  var sh = ss_().getSheetByName(name);
  if (!sh) return 0;
  var sorted = updates.slice().sort(function (a, b) { return a.row - b.row; });

  var calls = 0, i = 0;
  while (i < sorted.length) {
    var start = i, block = [sorted[i].values];
    while (i + 1 < sorted.length && sorted[i + 1].row === sorted[i].row + 1) {
      i++; block.push(sorted[i].values);
    }
    sh.getRange(sorted[start].row, 1, block.length, width).setValues(block);
    calls++;
    i++;
  }
  return calls;
}

/** A named value in the _state tab. Watermarks and counters live here, not in Script Properties,
 *  because Properties cap at 9KB per value and are invisible when you are staring at the sheet. */
function stateGet_(key, dflt) {
  var t = readTab_(CFG().tabs.state);
  for (var i = 0; i < t.rows.length; i++) {
    if (String(t.rows[i][0]) === String(key)) {
      var v = t.rows[i][1];
      return (v === '' || v === null || v === undefined) ? dflt : v;
    }
  }
  return dflt;
}

/** Set several state keys in one read and one write. */
function stateSetAll_(pairs) {
  var name = CFG().tabs.state, header = ['key', 'value', 'updated_at'];
  var t = readTab_(name);
  var idx = {}, rows = t.rows.slice();
  for (var i = 0; i < rows.length; i++) idx[String(rows[i][0])] = i;
  var now = istStamp(Date.now() / 1000);
  Object.keys(pairs).forEach(function (k) {
    if (idx.hasOwnProperty(k)) { rows[idx[k]][1] = pairs[k]; rows[idx[k]][2] = now; }
    else { rows.push([k, pairs[k], now]); }
  });
  replaceRows_(name, header, rows);
}

function stateSet_(key, value) { var p = {}; p[key] = value; stateSetAll_(p); }
