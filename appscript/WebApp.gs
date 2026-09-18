/**
 * WebApp.gs — the agent queue, and the (currently dormant) partner status page.
 *
 * ── TWO AUDIENCES, ONE DEPLOYMENT ───────────────────────────────────────────────────────────
 * doGet with ?t=<token> serves a single ticket's status, meant for the person who raised it.
 * doGet with no token serves the agent queue. They are the same deployment because Apps Script
 * gives you one URL, and the token is what separates them.
 *
 * Today the deployment is "Anyone within Meesho", so a partner following the token link hits a
 * sign-in they cannot pass. The page is built and correct; it is simply not reachable by the
 * people it is for. When domain approval lands, switching the deployment to "Anyone" makes it
 * live with no code change. Nothing else in the project depends on that switch.
 *
 * ── WHY THE QUEUE LEADS WITH "NEEDS A CATEGORY" ─────────────────────────────────────────────
 * NOVEL is not a category — it is the classifier refusing to guess, and it is the one state
 * that needs a human before anything else can happen. Rendering it as just another bucket
 * alongside the real dispositions is what made the previous dashboard unreadable.
 */

function doGet(e) {
  var token = e && e.parameter ? e.parameter.t : null;
  if (token) return partnerPage_(String(token));
  var tpl = HtmlService.createTemplateFromFile('UI');
  return tpl.evaluate()
    .setTitle('Partner Intake')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

/** Who is acting. Available because the deployment is domain-restricted; blank if it ever is
 *  not, which the write path treats as "unknown", never as "allowed to be anonymous". */
function currentUser_() {
  try { return Session.getActiveUser().getEmail() || ''; } catch (e) { return ''; }
}

function esc_(s) {
  return String(s === null || s === undefined ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── data for the UI ──────────────────────────────────────────────────────────────────────────

var STATE_GROUPS = {
  'NEEDS A CATEGORY': [],
  'Open':             ['NEW', 'IDENTIFIED', 'OPEN'],
  'Being worked on':  ['WORKING'],
  'Resolved':         ['RESOLVED', 'CLOSED']
};

/** Everything the queue renders, in one call. The UI does no second round trip. */
function getQueue() {
  var tickets = readTabObjects_(CFG().tabs.tickets);
  var out = [];
  for (var i = 0; i < tickets.length; i++) {
    var t = tickets[i];
    var sup = t.suppressed === true || String(t.suppressed).toLowerCase() === 'true';
    var intent = String(t.intent || '');
    var needsCategory = !sup && (intent === '' || intent === 'NOVEL');
    var state = String(t.state || 'NEW');

    var group;
    if (sup) group = 'Withheld';
    else if (needsCategory) group = 'NEEDS A CATEGORY';
    else if (STATE_GROUPS['Being worked on'].indexOf(state) >= 0) group = 'Being worked on';
    else if (STATE_GROUPS['Resolved'].indexOf(state) >= 0) group = 'Resolved';
    else group = 'Open';

    var flags = [];
    try { flags = JSON.parse(t.flags_json || '[]'); } catch (e) { flags = []; }
    var toks = {};
    try { toks = JSON.parse(t.entity_tokens_json || '{}'); } catch (e) { toks = {}; }
    var occ = [];
    try { occ = JSON.parse(t.occurrences_json || '[]'); } catch (e) { occ = []; }

    out.push({
      key: String(t.idempotency_key), ref: String(t.idempotency_key).slice(0, 8).toUpperCase(),
      group: group, title: String(t.title || ''), description: String(t.description || ''),
      raiser: String(t.raiser || ''), raiser_email: String(t.raiser_email || ''),
      dc_code: String(t.dc_code || ''), intent: intent, state: state,
      permalink: String(t.source_permalink || ''), flags: flags, tokens: toks,
      occurrence_count: Number(t.occurrence_count || 1), occurrences: occ,
      reply_count: Number(t.reply_count || 0),
      latency: t.first_response_latency_s === '' ? null : Number(t.first_response_latency_s),
      duplicate_of: String(t.duplicate_of || ''),
      suppressed_reason: String(t.suppressed_reason || ''),
      acknowledged_at: String(t.acknowledged_at || ''),
      acknowledge_error: String(t.acknowledge_error || ''),
      agent_note: String(t.agent_note || ''), updated_by: String(t.updated_by || ''),
      last_raised_at: String(t.last_raised_at || '')
    });
  }
  // Most recently raised first inside each group — the queue is worked from the top.
  out.sort(function (a, b) { return a.last_raised_at < b.last_raised_at ? 1 : -1; });

  return {
    tickets: out,
    channels: readTabObjects_(CFG().tabs.channels),
    dispositions: knownDispositions_(),
    health: healthSummary_(),
    user: currentUser_()
  };
}

/** Every disposition the classifier can currently produce, from the exemplar tab itself —
 *  so a category added by a human appears in the dropdown on the next load, with no deploy. */
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

/** Move a ticket's state, and optionally leave a note the partner is allowed to see. */
function setTicketState(key, state, note) {
  var allowed = ['NEW', 'IDENTIFIED', 'OPEN', 'WORKING', 'RESOLVED', 'CLOSED'];
  if (allowed.indexOf(state) < 0) throw new Error('unknown state: ' + state);
  var row = ticketRow_(key);
  if (!row) throw new Error('no such ticket');
  var sh = ss_().getSheetByName(CFG().tabs.tickets);
  sh.getRange(row, TICKET_HEADER.indexOf('state') + 1).setValue(state);
  if (note !== null && note !== undefined) {
    sh.getRange(row, TICKET_HEADER.indexOf('agent_note') + 1).setValue(String(note).slice(0, 2000));
  }
  sh.getRange(row, TICKET_HEADER.indexOf('updated_by') + 1).setValue(currentUser_());
  sh.getRange(row, TICKET_HEADER.indexOf('updated_at') + 1).setValue(istStamp(Date.now() / 1000));
  SpreadsheetApp.flush();
  return { ok: true, state: state };
}

/**
 * Name a NOVEL ticket — the moment the creep loop closes.
 *
 * The label is written onto the ticket AND appended to the _exemplars tab as a GOLD row. Gold
 * counts for three silver, so one human label can outvote a partially-matching established
 * class; without that weighting the next identical message would still come back NOVEL and the
 * queue would be theatre.
 */
function nameDisposition(key, disposition) {
  disposition = String(disposition || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_')
                      .replace(/^_+|_+$/g, '');
  if (!disposition) throw new Error('a category name is required');
  var row = ticketRow_(key);
  if (!row) throw new Error('no such ticket');

  var sh = ss_().getSheetByName(CFG().tabs.tickets);
  var title = String(sh.getRange(row, TICKET_HEADER.indexOf('title') + 1).getValue() || '');
  var desc = String(sh.getRange(row, TICKET_HEADER.indexOf('description') + 1).getValue() || '');
  // The exemplar text is the partner's own words — the first line of the description, which is
  // the anchor message before we added our own scaffolding. Learning from our own title would
  // teach the classifier to recognise its own output.
  var text = desc.split('\n')[0] || title;

  sh.getRange(row, TICKET_HEADER.indexOf('intent') + 1).setValue(disposition);
  sh.getRange(row, TICKET_HEADER.indexOf('updated_by') + 1).setValue(currentUser_());
  sh.getRange(row, TICKET_HEADER.indexOf('updated_at') + 1).setValue(istStamp(Date.now() / 1000));

  appendRows_(CFG().tabs.exemplars, ['id', 'disposition', 'label_provenance', 'text'],
              [['GOLD-' + String(key).slice(0, 8), disposition, 'gold', text]]);
  SpreadsheetApp.flush();
  return { ok: true, disposition: disposition };
}

// ── the partner page ─────────────────────────────────────────────────────────────────────────

/** One ticket, by its unguessable token. Shows status and the agent's note — never the
 *  internal flags, the classifier's reasoning, or anybody else's ticket. */
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
                  CLOSED: 'Closed' }[String(found.state)] || 'Received';
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
