/**
 * WebApp.gs — the agent queue's server side, and the partner status page.
 *
 * ── WHO IS ASKING ───────────────────────────────────────────────────────────────────────────
 * The deployment is executeAs=Me + access=Domain. In that mode Session.getActiveUser() returns
 * the VIEWER's email, not the deployer's, because viewer and deployer share the meesho.com
 * Workspace domain. That is what lets 10-20 agents be identified without any of them being
 * given edit access to the spreadsheet — the script writes on their behalf.
 *
 * It also means the Sheet's own revision history records only the deployer for every change, so
 * the `updated_by` column IS the audit trail. It is written server-side from currentUser_() and
 * never accepted from the client.
 *
 * ── EVERY WRITE GOES THROUGH requireAgent_ AND withLock_ ────────────────────────────────────
 * Before both existed, any Meesho employee with the /exec URL could reclassify tickets and
 * append GOLD exemplars — and gold counts for three silver, so a handful of bad labels skews
 * the classifier permanently with no undo. And an agent's edit landing between the pipeline's
 * read and its write was silently reverted. Neither failed loudly; both just quietly lost work.
 */

var AGENT_HEADER = ['email', 'name', 'active', 'role', 'filter_json', 'last_seen_at', 'updated_at'];

function doGet(e) {
  var token = e && e.parameter ? e.parameter.t : null;
  if (token) return partnerPage_(String(token));
  var tpl = HtmlService.createTemplateFromFile('UI');
  return tpl.evaluate()
    .setTitle('Partner Intake')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/** The viewer's email, or '' if Google would not tell us. '' is treated as a hard deny
 *  everywhere rather than as a blank string — an empty updated_by is worse than an error. */
function currentUser_() {
  try { return String(Session.getActiveUser().getEmail() || '').toLowerCase(); }
  catch (e) { return ''; }
}

function esc_(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * The agent roster, as a Map keyed by lowercase email.
 *
 * It is a plain Sheet tab on purpose: adding a teammate is typing a row, not a deploy. There is
 * no admin UI because the people who can add agents are the people who can open the Sheet, and
 * that is already the right permission boundary.
 */
function agents_() {
  if (_cache.agents) return _cache.agents;
  var m = new Map();
  readTabObjects_(CFG().tabs.agents).forEach(function (r, i) {
    var email = String(r.email || '').trim().toLowerCase();
    if (!email) return;
    m.set(email, { email: email, name: String(r.name || email.split('@')[0]),
                   active: String(r.active).toLowerCase() !== 'false',
                   role: String(r.role || 'agent'),
                   filter_json: String(r.filter_json || ''), row: i + 2 });
  });
  _cache.agents = m;
  return m;
}

/** Resolve the caller, or throw something they can act on. */
function requireAgent_() {
  var email = currentUser_();
  if (!email) {
    throw new Error('Could not identify you. Sign in with your @meesho.com account — if you ' +
                    'are signed into more than one Google account, open this in that profile.');
  }
  var a = agents_().get(email);
  if (!a || !a.active) {
    throw new Error('You are not on the agent roster (' + email + '). Ask whoever owns the ' +
                    'Sheet to add a row to the _agents tab.');
  }
  return a;
}

/** What a ticket's state actually is: what a human set, falling back to what the pipeline
 *  derived. Keeping them in separate columns is what stops the pipeline walking one back. */
function effectiveState_(t) {
  return String(t.desk_state || t.pipeline_state || 'NEW');
}

var OPEN_STATES = ['NEW', 'IDENTIFIED', 'OPEN'];
var DONE_STATES = ['RESOLVED', 'CLOSED'];

function groupFor_(t) {
  var sup = t.suppressed === true || String(t.suppressed).toLowerCase() === 'true';
  if (sup) return 'Withheld';
  var intent = String(t.intent || '');
  if (intent === '' || intent === 'NOVEL') return 'NEEDS A CATEGORY';
  var st = effectiveState_(t);
  if (st === 'WORKING') return 'Being worked on';
  if (DONE_STATES.indexOf(st) >= 0) return 'Resolved';
  return 'Open';
}

// ── the queue ────────────────────────────────────────────────────────────────────────────────

/**
 * The list payload: short fields only, capped, newest first.
 *
 * It used to return every ticket ever written with full descriptions, occurrences and typed
 * entities — roughly 1-2 KB each. At this project's own steady state that is ~15k tickets a
 * month, so by month two every agent was pulling several megabytes across the google.script.run
 * bridge on every refresh. Detail is now a separate call for the one ticket being looked at.
 *
 * ── WHY IT RETURNS EVERY ACTIVE GROUP AT ONCE ───────────────────────────────────────────────
 * Filtering by group on the server meant every tab click was a Sheets round trip before the tab
 * even highlighted — about two seconds of a button that looks broken. The three ACTIVE groups
 * come back together so the browser can switch tabs, search and filter instantly with no server
 * call at all. Resolved and Withheld are fetched only when opened, because they grow without
 * bound and nobody works them daily.
 */
var ARCHIVE_GROUPS = ['Resolved', 'Withheld'];

function getQueue(opts) {
  var me = requireAgent_();
  opts = opts || {};
  var wantArchive = ARCHIVE_GROUPS.indexOf(opts.group) >= 0;
  var rows = readTabObjects_(CFG().tabs.tickets);
  var counts = { 'NEEDS A CATEGORY': 0, 'Open': 0, 'Being worked on': 0, 'Resolved': 0, 'Withheld': 0 };
  var mine = 0, unassigned = 0, list = [];

  for (var i = rows.length - 1; i >= 0; i--) {        // newest first without sorting the whole set
    var t = rows[i];
    if (!t.idempotency_key) continue;
    var g = groupFor_(t);
    counts[g] = (counts[g] || 0) + 1;
    var assignee = String(t.assigned_to || '');
    if (assignee === me.email) mine++;
    if (!assignee && g !== 'Withheld' && g !== 'Resolved') unassigned++;

    // The browser does group / mine / search filtering. Only the archive split happens here.
    if (wantArchive ? g !== opts.group : ARCHIVE_GROUPS.indexOf(g) >= 0) continue;
    if (list.length >= CFG().queueLimit) continue;

    var flags = 0;
    try { flags = JSON.parse(t.flags_json || '[]').length; } catch (e) { flags = 0; }
    list.push({
      key: String(t.idempotency_key), ref: String(t.idempotency_key).slice(0, 8).toUpperCase(),
      group: g, title: String(t.title || ''), dc_code: String(t.dc_code || ''),
      raiser: String(t.raiser || ''), intent: String(t.intent || ''),
      intent_label: t.intent && t.intent !== 'NOVEL' ? dispositionInfo(t.intent).label : '',
      state: effectiveState_(t), assigned_to: assignee, flags: flags,
      occurrence_count: Number(t.occurrence_count || 1),
      acknowledged: !!t.acknowledged_at, dc_told: !!t.dc_notified_at,
      filed: !!t.filed_at, asked: !!t.asked_at, answered: !!t.answered_at,
      last_raised_at: String(t.last_raised_at || ''),
      first_raised_at: String(t.first_raised_at || ''),
      margin: t.intent_margin === '' ? null : Number(t.intent_margin),
      has_note: !!String(t.agent_note || '')
    });
  }

  return {
    tickets: list, counts: counts, mine: mine, unassigned: unassigned,
    archive: wantArchive ? opts.group : '',
    total: rows.length, capped: list.length >= CFG().queueLimit,
    channels: readTabObjects_(CFG().tabs.channels),
    dispositions: knownDispositions_().map(dispositionInfo),
    roster: Array.from(agents_().values())
      .filter(function (a) { return a.active; })
      .map(function (a) { return { email: a.email, name: a.name }; }),
    health: healthSummary_(),
    me: { email: me.email, name: me.name, role: me.role, filter: me.filter_json }
  };
}

/** Everything about ONE ticket. Called when a row is opened, not for the whole list. */
function getTicket(key) {
  requireAgent_();
  var rows = readTabObjects_(CFG().tabs.tickets);
  for (var i = 0; i < rows.length; i++) {
    if (String(rows[i].idempotency_key) !== String(key)) continue;
    var t = rows[i];
    var parse = function (v, d) { try { return JSON.parse(v || d); } catch (e) { return JSON.parse(d); } };
    return {
      key: String(t.idempotency_key), ref: String(t.idempotency_key).slice(0, 8).toUpperCase(),
      group: groupFor_(t), title: String(t.title || ''), description: String(t.description || ''),
      raiser: String(t.raiser || ''), raiser_email: String(t.raiser_email || ''),
      dc_code: String(t.dc_code || ''), intent: String(t.intent || ''),
      state: effectiveState_(t), pipeline_state: String(t.pipeline_state || ''),
      permalink: String(t.source_permalink || ''),
      // The classifier's own uncertainty. On a NOVEL these are the two categories it could not
      // separate — which is exactly the choice to put in front of a human.
      margin: t.intent_margin === '' ? null : Number(t.intent_margin),
      runner_up: String(t.intent_runner_up || ''),
      // The two candidates, each with what it MEANS and which desk it lands on — that is what
      // a human actually needs to choose between them, not the snake_case name.
      choices: String(t.intent_candidates || t.intent_runner_up || '').split('|')
        .filter(function (x, i, a) { return x && x !== 'NOVEL' && a.indexOf(x) === i; })
        .slice(0, 2).map(dispositionInfo),
      intent_info: t.intent && t.intent !== 'NOVEL' ? dispositionInfo(t.intent) : null,
      dc: dcContact_(String(t.dc_code || '')),
      flags: parse(t.flags_json, '[]'), tokens: parse(t.entity_tokens_json, '{}'),
      occurrences: parse(t.occurrences_json, '[]'),
      occurrence_count: Number(t.occurrence_count || 1),
      reply_count: Number(t.reply_count || 0),
      latency: t.first_response_latency_s === '' ? null : Number(t.first_response_latency_s),
      duplicate_of: String(t.duplicate_of || ''),
      suppressed_reason: String(t.suppressed_reason || ''),
      acknowledged_at: String(t.acknowledged_at || ''),
      acknowledge_error: String(t.acknowledge_error || ''),
      dc_notified_at: String(t.dc_notified_at || ''),
      dc_notify_error: String(t.dc_notify_error || ''),
      agent_note: String(t.agent_note || ''), assigned_to: String(t.assigned_to || ''),
      filed_at: String(t.filed_at || ''), file_error: String(t.file_error || ''),
      asked_at: String(t.asked_at || ''), asked_for: String(t.asked_for || ''),
      answered_at: String(t.answered_at || ''), answer: String(t.answer || ''),
      answer_identifiers: String(t.answer_identifiers || ''),
      ask: askDraft(t),                       // the message to send, and what it asks for
      identification: identificationBand(t),  // how much we actually understood
      kapture_to: kaptureAddress_(),
      kapture_blockers: kaptureBlockers_(t, { auto: false }),
      updated_by: String(t.updated_by || ''), updated_at: String(t.updated_at || ''),
      last_raised_at: String(t.last_raised_at || '')
    };
  }
  throw new Error('No such ticket.');
}

/** Who the delivery centre actually is, for the two-party status. A code with no contact row
 *  is the answer that matters most — it means that centre is hearing nothing. */
function dcContact_(code) {
  if (!code) return null;
  var c = contacts_().get(String(code).toUpperCase());
  if (!c) return { code: code, name: '', known: false };
  return { code: c.code, name: c.name, known: true,
           email: c.email, mobile: c.mobile ? c.mobile.slice(0, 2) + 'XXXXX' + c.mobile.slice(-3) : '' };
}

function knownDispositions_() {
  var s = new Set();
  readTabObjects_(CFG().tabs.exemplars).forEach(function (r) {
    if (r.disposition && String(r.disposition) !== 'NOVEL') s.add(String(r.disposition));
  });
  return Array.from(s).sort();
}

function ticketRow_(key) {
  var t = readTab_(CFG().tabs.tickets);
  for (var i = 0; i < t.rows.length; i++) {
    if (String(t.rows[i][0]) === String(key)) return i + 2;
  }
  return 0;
}

/**
 * Write the desk block for one ticket as ONE setValues over its contiguous range.
 *
 * Atomic at the range level, which is what stops two agents acting on the same ticket producing
 * a mixed row — state from one, note from the other, and updated_by naming whoever happened to
 * write last. The previous version issued four separate setValue calls and did exactly that.
 */
function writeDesk_(row, patch) {
  var sh = sheet_(CFG().tabs.tickets, TICKET_HEADER);   // widens if the header grew
  var width = TICKET_HEADER.length - TICKET_PIPELINE_COLS;
  var cur = sh.getRange(row, TICKET_DESK_START, 1, width).getValues()[0];
  var vals = cur.slice();
  for (var i = 0; i < width; i++) {
    var name = TICKET_HEADER[TICKET_PIPELINE_COLS + i];
    if (patch.hasOwnProperty(name)) vals[i] = patch[name];
  }
  sh.getRange(row, TICKET_DESK_START, 1, width).setValues([vals]);
}

// ── agent actions ────────────────────────────────────────────────────────────────────────────

var ALLOWED_STATES = ['NEW', 'IDENTIFIED', 'OPEN', 'WORKING', 'RESOLVED', 'CLOSED'];

/** Move a ticket's state, and optionally leave a note the partner can see. */
function setTicketState(key, state, note) {
  var me = requireAgent_();
  if (ALLOWED_STATES.indexOf(state) < 0) throw new Error('unknown state: ' + state);
  return withLock_(function () {
    var row = ticketRow_(key);
    if (!row) throw new Error('No such ticket.');
    var patch = { desk_state: state, updated_by: me.email,
                  updated_at: istStamp(Date.now() / 1000) };
    if (note !== null && note !== undefined) patch.agent_note = String(note).slice(0, 2000);
    writeDesk_(row, patch);
    SpreadsheetApp.flush();
    return { ok: true, state: state, updated_by: me.email };
  });
}

/** Save the note alone, without touching state — so an agent typing a long note does not
 *  accidentally move a ticket they were only annotating. */
function saveNote(key, note) {
  var me = requireAgent_();
  return withLock_(function () {
    var row = ticketRow_(key);
    if (!row) throw new Error('No such ticket.');
    writeDesk_(row, { agent_note: String(note || '').slice(0, 2000), updated_by: me.email,
                      updated_at: istStamp(Date.now() / 1000) });
    SpreadsheetApp.flush();
    return { ok: true };
  });
}

/** Assign (or unassign, with an empty email). The target must be on the roster — otherwise a
 *  typo silently assigns work to nobody and the ticket looks handled. */
function setAssignee(key, email) {
  var me = requireAgent_();
  email = String(email || '').trim().toLowerCase();
  if (email && !agents_().has(email)) throw new Error('Not on the roster: ' + email);
  return withLock_(function () {
    var row = ticketRow_(key);
    if (!row) throw new Error('No such ticket.');
    writeDesk_(row, { assigned_to: email,
                      assigned_at: email ? istStamp(Date.now() / 1000) : '',
                      updated_by: me.email, updated_at: istStamp(Date.now() / 1000) });
    SpreadsheetApp.flush();
    return { ok: true, assigned_to: email };
  });
}

/** Take it yourself. The commonest assignment by far is "mine", and making an agent find their
 *  own name in a list of twenty is friction for the one action they do most. */
function claimTicket(key) {
  var me = requireAgent_();
  return setAssignee(key, me.email);
}

/** Assign several at once — the realistic way a lead distributes a morning backlog. */
function setAssigneeBulk(keys, email) {
  var me = requireAgent_();
  email = String(email || '').trim().toLowerCase();
  if (email && !agents_().has(email)) throw new Error('Not on the roster: ' + email);
  return withLock_(function () {
    var t = readTab_(CFG().tabs.tickets);
    var idx = new Map();
    for (var i = 0; i < t.rows.length; i++) idx.set(String(t.rows[i][0]), i + 2);
    var now = istStamp(Date.now() / 1000), n = 0;
    (keys || []).forEach(function (k) {
      var row = idx.get(String(k));
      if (!row) return;
      writeDesk_(row, { assigned_to: email, assigned_at: email ? now : '',
                        updated_by: me.email, updated_at: now });
      n++;
    });
    SpreadsheetApp.flush();
    return { ok: true, assigned: n, to: email };
  });
}

/**
 * Name a NOVEL ticket. This is the moment the creep loop closes — and the moment two earlier
 * bugs lived.
 *
 * The category is written to the ISSUE row, not just the ticket. The pipeline rebuilds a
 * ticket's `intent` from its issue on every update, so writing only the ticket meant every
 * categorised ticket silently reverted to NOVEL and jumped back to the top of the queue the
 * next time any Slack reply touched its issue. The gold exemplar still improved future
 * classification, so the loop looked like it worked — only that one ticket bounced back, which
 * reads as the tool undoing your work.
 *
 * The gold exemplar append also runs under the lock. appendRows_ targets getLastRow() + 1, so
 * two agents categorising two different tickets in the same second both computed the same row
 * and one label was overwritten — and with goldWeight 3.0 that is the highest-value row in the
 * system.
 */
function nameDisposition(key, disposition) {
  var me = requireAgent_();
  disposition = String(disposition || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_')
                      .replace(/^_+|_+$/g, '');
  if (!disposition) throw new Error('A category name is required.');

  return withLock_(function () {
    var sh = ss_().getSheetByName(CFG().tabs.tickets);
    var t = readTab_(CFG().tabs.tickets);
    var row = 0, issueId = '', desc = '', title = '';
    for (var i = 0; i < t.rows.length; i++) {
      if (String(t.rows[i][0]) !== String(key)) continue;
      row = i + 2;
      issueId = String(t.rows[i][TICKET_HEADER.indexOf('issue_id')] || '');
      title = String(t.rows[i][TICKET_HEADER.indexOf('title')] || '');
      desc = String(t.rows[i][TICKET_HEADER.indexOf('description')] || '');
      break;
    }
    if (!row) throw new Error('No such ticket.');

    // The partner's own words: the first line of the description, before our scaffolding.
    // Learning from our own generated title would teach the classifier to recognise its output.
    var text = desc.split('\n')[0] || title;

    sh.getRange(row, TICKET_HEADER.indexOf('intent') + 1).setValue(disposition);
    writeDesk_(row, { updated_by: me.email, updated_at: istStamp(Date.now() / 1000) });

    // The fix for the reversion: the issue is the source of truth for intent.
    if (issueId) {
      var it = readTab_(CFG().tabs.issues);
      var iCol = ISSUE_HEADER.indexOf('intent') + 1;
      var sCol = ISSUE_HEADER.indexOf('intent_source') + 1;
      for (var j = 0; j < it.rows.length; j++) {
        if (String(it.rows[j][0]) !== issueId) continue;
        var ish = ss_().getSheetByName(CFG().tabs.issues);
        ish.getRange(j + 2, iCol).setValue(disposition);
        ish.getRange(j + 2, sCol).setValue('human');
        break;
      }
    }

    appendRows_(CFG().tabs.exemplars, ['id', 'disposition', 'label_provenance', 'text'],
                [['GOLD-' + String(key).slice(0, 8), disposition, 'gold', text]]);
    SpreadsheetApp.flush();
    _cache.matcher = undefined;                      // next classify run rebuilds with this row
    return { ok: true, disposition: disposition };
  });
}

/** Remember this agent's view preferences. Kept in the roster tab keyed by email, NOT in
 *  UserProperties: under executeAs=Me the effective user is the deployer for every request, so
 *  all 20 agents would silently share one store and overwrite each other. */
function saveAgentFilter(json) {
  var me = requireAgent_();
  return withLock_(function () {
    var sh = ss_().getSheetByName(CFG().tabs.agents);
    sh.getRange(me.row, AGENT_HEADER.indexOf('filter_json') + 1).setValue(String(json || '').slice(0, 2000));
    sh.getRange(me.row, AGENT_HEADER.indexOf('last_seen_at') + 1).setValue(istStamp(Date.now() / 1000));
    SpreadsheetApp.flush();
    return { ok: true };
  });
}

// ── the partner page ─────────────────────────────────────────────────────────────────────────

/** One ticket, by its unguessable token. Status and the agent's note — never the internal
 *  flags, the classifier's reasoning, or anybody else's ticket. */
function partnerPage_(token) {
  var tickets = readTabObjects_(CFG().tabs.tickets);
  var found = null;
  for (var i = 0; i < tickets.length; i++) {
    if (String(tickets[i].public_token) === token) { found = tickets[i]; break; }
  }
  var body;
  if (!found) {
    body = '<h1>Not found</h1><p>This link is not valid. If you raised an issue recently, ' +
           'reply in the same channel and someone will pick it up.</p>';
  } else {
    var state = { NEW: 'Received', IDENTIFIED: 'Received', OPEN: 'Being looked at',
                  WORKING: 'Being worked on', RESOLVED: 'Resolved',
                  CLOSED: 'Closed' }[effectiveState_(found)] || 'Received';
    var cat = found.intent && found.intent !== 'NOVEL'
      ? String(found.intent).replace(/_/g, ' ') : 'being categorised';
    body = '<div class="ref">' + esc_(String(found.idempotency_key).slice(0, 8).toUpperCase()) + '</div>' +
           '<h1>' + esc_(found.title) + '</h1>' +
           '<div class="badge">' + esc_(state) + '</div>' +
           '<dl><dt>Category</dt><dd>' + esc_(cat) + '</dd>' +
           '<dt>Raised</dt><dd>' + esc_(found.first_raised_at || '') + '</dd>' +
           (Number(found.occurrence_count || 1) > 1
             ? '<dt>Raised before</dt><dd>' + esc_(found.occurrence_count) + ' times</dd>' : '') +
           '</dl>' +
           (found.agent_note ? '<div class="note"><b>Update from the team</b><p>' +
              esc_(found.agent_note) + '</p></div>' : '') +
           '<p class="muted">This page updates on its own. There is nothing to reply to.</p>';
  }
  return HtmlService.createHtmlOutput(
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;max-width:560px;' +
    'margin:40px auto;padding:0 20px;color:#1a1a1a;line-height:1.55}' +
    '.ref{font:600 12px ui-monospace,monospace;letter-spacing:.08em;color:#888}' +
    'h1{font-size:20px;margin:6px 0 14px}' +
    '.badge{display:inline-block;background:#111;color:#fff;padding:5px 12px;border-radius:99px;' +
    'font-size:13px;font-weight:600}' +
    'dl{display:grid;grid-template-columns:auto 1fr;gap:6px 18px;margin:22px 0;font-size:14px}' +
    'dt{color:#777}dd{margin:0;font-weight:600}' +
    '.note{background:#f5f5f4;border-radius:8px;padding:14px 16px;margin:20px 0;font-size:14px}' +
    '.note p{margin:6px 0 0}.muted{color:#888;font-size:13px;margin-top:28px}</style>' + body)
    .setTitle('Your issue');
}
