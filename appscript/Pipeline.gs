/**
 * Pipeline.gs — raw_messages becomes issues becomes tickets. The 5-minute trigger's entry point.
 *
 * Stage order is fixed and each stage depends on the one before:
 *
 *   noise ──▶ entities ──▶ evidence ──▶ group ──▶ register ──▶ dedupe ──▶ classify ──▶ emit
 *
 * evidence needs the entity KINDS, so extraction runs first. Grouping needs the evidence verdict
 * to know an orphan from an issue. Emit needs the duplicate links to write "counted_into".
 *
 * ── INCREMENTAL, AND WHY THAT IS SAFE ───────────────────────────────────────────────────────
 * Only rows after the watermark are processed, and the watermark advances once they are written.
 * There is no run_id and nothing is deleted and rebuilt. That works because every identifier
 * downstream is derived from its source: an issue is ISS-<channel>-<message> and a ticket is
 * sha256 of the same pair. Processing a row twice would therefore produce the same issue and the
 * same ticket rather than duplicates — the watermark is an efficiency, not the thing keeping us
 * correct.
 */

var ISSUE_HEADER = [
  'issue_id', 'anchor_channel_id', 'anchor_message_id', 'permalink', 'raiser_id', 'raiser_name',
  'raiser_email', 'dc_code', 'entity_tokens_json', 'intent', 'intent_source', 'informational',
  'kapture_ticket_ids_json', 'first_response_latency_s', 'latency_excluded_reason',
  'reply_count', 'replies_from_raiser', 'state', 'duplicate_of', 'ts_epoch', 'ts_iso',
  'anchor_text', 'source_system', 'updated_at'
];

var TICKET_HEADER = [
  'idempotency_key', 'issue_id', 'source_system', 'source_id', 'source_permalink', 'title',
  'description', 'raiser', 'raiser_email', 'dc_code', 'intent', 'entity_tokens_json',
  'flags_json', 'kapture_ticket_ids_json', 'reply_count', 'first_response_latency_s', 'state',
  'duplicate_of', 'suppressed', 'suppressed_reason', 'occurrence_count', 'occurrences_json',
  'first_raised_at', 'last_raised_at', 'acknowledged_at', 'acknowledge_error', 'agent_note',
  'updated_by', 'updated_at', 'public_token'
];

function objToRow_(header, obj) {
  return header.map(function (h) {
    var v = obj[h];
    return (v === null || v === undefined) ? '' : v;
  });
}

/** Read the tail of the issues tab and keep those inside the longest grouping window.
 *  A tail read is valid because anchors are appended in timestamp order and updates never
 *  reorder rows. */
function openIssues_() {
  var sh = ss_().getSheetByName(CFG().tabs.issues);
  if (!sh || sh.getLastRow() < 2) return [];
  var last = sh.getLastRow();
  var from = Math.max(2, last - CFG().issueTailRows + 1);
  var vals = sh.getRange(from, 1, last - from + 1, ISSUE_HEADER.length).getValues();

  var maxWindow = CFG().grouping.defaultWindowS;
  Object.keys(CFG().grouping.windows).forEach(function (k) {
    maxWindow = Math.max(maxWindow, CFG().grouping.windows[k]);
  });
  var cutoff = (Date.now() / 1000) - maxWindow;

  var out = [];
  for (var i = 0; i < vals.length; i++) {
    var o = {};
    for (var j = 0; j < ISSUE_HEADER.length; j++) o[ISSUE_HEADER[j]] = vals[i][j];
    o.ts_epoch = Number(o.ts_epoch);
    if (o.ts_epoch < cutoff) continue;
    o.reply_count = Number(o.reply_count) || 0;
    o.replies_from_raiser = Number(o.replies_from_raiser) || 0;
    o._row = from + i;
    var toks = [];
    try {
      var parsed = JSON.parse(o.entity_tokens_json || '{}');
      Object.keys(parsed).forEach(function (k) {
        (parsed[k] || []).forEach(function (v) { toks.push([k, v]); });
      });
    } catch (e) { /* a hand-edited cell must not stop the run */ }
    o.tokens = toks;
    out.push(o);
  }
  return out;
}

/** Turn the token pair list into {kind: [values sorted]}, which is what every consumer wants. */
function tokensByKind_(pairs) {
  var m = {};
  (pairs || []).forEach(function (p) {
    if (!m[p[0]]) m[p[0]] = [];
    if (m[p[0]].indexOf(p[1]) < 0) m[p[0]].push(p[1]);
  });
  Object.keys(m).forEach(function (k) { m[k].sort(); });
  return m;
}

/**
 * The whole structuring pass. Returns a summary object; everything else lands in the tabs.
 */
function runPipeline() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(0)) return { skipped: 'another run holds the lock' };
  try {
    var t0 = Date.now();
    var startRow = Number(stateGet_('pipe:last_row', 0)) || 0;
    var slice = readTabSlice_(CFG().tabs.raw, startRow + 1, CFG().maxMessagesPerRun);
    if (!slice.rows.length) {
      stateSet_('pipe:last_at', istStamp(Date.now() / 1000));
      return { processed: 0, note: 'nothing new since row ' + startRow };
    }

    var reg = DCREG();
    var msgs = [], writeback = [];

    // ── stages 1-3: noise, entities, evidence ───────────────────────────────────────────────
    for (var i = 0; i < slice.rows.length; i++) {
      var r = slice.rows[i];
      var g = function (n) { return r[RAW_COL[n]]; };
      var text = String(g('text') || '');

      var nc = noiseClassify(text, String(g('subtype') || '') || null, !!g('has_media'));
      var ents = nc.gated ? [] : extractEntities(text, reg.registry, reg.denylist);
      var kinds = [];
      ents.forEach(function (e) { if (kinds.indexOf(e.kind) < 0) kinds.push(e.kind); });

      var ev;
      if (nc.gated) {
        // Gated messages are skipped by evidence entirely, exactly as the reference does.
        ev = { decision: '', score: null, reasons: '' };
      } else if (g('thread_ref')) {
        // A threaded reply is never asked to justify itself. Otherwise "will update in 30 mins"
        // is rejected as evidence-free and the thread loses its own reply.
        ev = { decision: 'reply', score: null,
               reasons: "thread reply — inherits its parent's issue" };
      } else {
        ev = evidenceScore(text, kinds);
      }

      msgs.push({
        row: startRow + 1 + i,
        channel_id: String(g('channel_id')), message_id: String(g('message_id')),
        ts_epoch: Number(g('ts_epoch')), ts_iso: String(g('ts_iso')),
        author_id: String(g('author_id') || ''), author_name: String(g('author_name') || ''),
        author_email: String(g('author_email') || ''), text: text,
        thread_ref: String(g('thread_ref') || ''), permalink: String(g('permalink') || ''),
        source_system: String(g('source_system') || 'slack'),
        gated: nc.gated, informational: nc.informational,
        evidence_decision: ev.decision,
        tokens: ents.map(function (e) { return [e.kind, e.value]; })
      });
      writeback.push([nc.gated, nc.gateRule || '', nc.informational, ev.decision || '']);
    }

    // Candidates for grouping: not gated, and not evidence-rejected.
    var candidates = msgs.filter(function (m) {
      return !m.gated && m.evidence_decision !== 'not_an_issue';
    });
    candidates.sort(function (a, b) {
      // An explicit tiebreak the reference SQL lacks — without it two messages sharing a
      // timestamp group in whatever order the store happened to return, and the run is not
      // reproducible.
      return (a.ts_epoch - b.ts_epoch) ||
             (a.channel_id < b.channel_id ? -1 : a.channel_id > b.channel_id ? 1 : 0) ||
             (a.message_id < b.message_id ? -1 : a.message_id > b.message_id ? 1 : 0);
    });

    // ── stage 4: grouping ───────────────────────────────────────────────────────────────────
    var open = openIssues_();
    var gr = groupMessages(candidates, open);

    var assignByKey = new Map();
    gr.assignments.forEach(function (a) {
      assignByKey.set(a.channel_id + SEP + a.message_id, a);
    });

    // ── stage 5: register ───────────────────────────────────────────────────────────────────
    var issueById = new Map();
    open.forEach(function (o) { issueById.set(o.issue_id, o); });

    gr.newIssues.forEach(function (ni) {
      var m = null;
      for (var k = 0; k < candidates.length; k++) {
        if (candidates[k].channel_id === ni.anchor_channel_id &&
            candidates[k].message_id === ni.anchor_message_id) { m = candidates[k]; break; }
      }
      if (!m) return;
      issueById.set(ni.issue_id, {
        issue_id: ni.issue_id, anchor_channel_id: m.channel_id, anchor_message_id: m.message_id,
        permalink: m.permalink, raiser_id: m.author_id, raiser_name: m.author_name,
        raiser_email: m.author_email, dc_code: '', entity_tokens_json: '{}',
        intent: '', intent_source: 'unadjudicated', informational: m.informational,
        kapture_ticket_ids_json: '[]', first_response_latency_s: '',
        latency_excluded_reason: '', reply_count: 0, replies_from_raiser: 0, state: 'NEW',
        duplicate_of: '', ts_epoch: m.ts_epoch, ts_iso: m.ts_iso, anchor_text: m.text,
        source_system: m.source_system, updated_at: '', tokens: ni.tokens, _isNew: true
      });
    });

    // Members grow the issue's tokens and its derived counters.
    gr.tokenGrowth.forEach(function (added, iid) {
      var iss = issueById.get(iid);
      if (!iss) return;
      iss.tokens = (iss.tokens || []).concat(added);
      iss._dirty = true;
    });

    candidates.forEach(function (m) {
      var a = assignByKey.get(m.channel_id + SEP + m.message_id);
      if (!a || !a.issue_id) return;
      var iss = issueById.get(a.issue_id);
      if (!iss) return;
      if (m.message_id === iss.anchor_message_id) return;
      foldMember_(iss, m);          // field names line up; it mutates the row in place
      iss._dirty = true;
    });

    // ── stage 6: dedupe, over the issues this run touched plus their recent neighbours ───────
    var dedupeScope = [];
    issueById.forEach(function (iss) {
      var tb = tokensByKind_(iss.tokens);
      dedupeScope.push({ issue_id: iss.issue_id, raiser_id: iss.raiser_id,
                         ts_epoch: Number(iss.ts_epoch),
                         anchor_channel_id: iss.anchor_channel_id,
                         dc_code: (tb.dc_code || [])[0] || '', text: iss.anchor_text,
                         kapture_ticket_ids: tb.kapture_id || [] });
    });
    var dupPairs = dedupeIssues(dedupeScope);
    var linked = new Map();
    dupPairs.forEach(function (p) {
      var iss = issueById.get(p.issue_id);
      if (iss && !iss.duplicate_of) { iss.duplicate_of = p.duplicate_of; iss._dirty = true; }
      if (!linked.has(p.duplicate_of)) linked.set(p.duplicate_of, []);
      linked.get(p.duplicate_of).push(p.issue_id);
    });

    // ── stage 7: classify ───────────────────────────────────────────────────────────────────
    var classified = 0, novel = 0, skipped = false;
    issueById.forEach(function (iss) {
      if (!iss._isNew && iss.intent) return;              // classify once, then leave it alone
      var c = classifyText(iss.anchor_text);
      if (c.skipped) { skipped = true; return; }
      iss.intent = c.disposition;
      iss.intent_source = 'bm25';
      iss._dirty = true;
      classified++;
      if (c.disposition === 'NOVEL') novel++;
    });

    // ── finalise issue rows ─────────────────────────────────────────────────────────────────
    var knownDisp = new Set();
    readTabObjects_(CFG().tabs.exemplars).forEach(function (r) {
      if (r.disposition) knownDisp.add(String(r.disposition));
    });

    var newIssueRows = [], updates = [];
    issueById.forEach(function (iss) {
      if (!iss._isNew && !iss._dirty) return;
      var tb = tokensByKind_(iss.tokens);
      iss.dc_code = (tb.dc_code || [])[0] || '';        // alphabetically first, not most frequent
      iss.entity_tokens_json = JSON.stringify(tb);
      iss.kapture_ticket_ids_json = JSON.stringify(tb.kapture_id || []);
      iss.state = (iss.state === 'RESOLVED' || iss.state === 'CLOSED' || iss.state === 'WORKING')
        ? iss.state                                      // a human moved it; never walk it back
        : registerState_(tb, Number(iss.reply_count) || 0);
      iss.latency_excluded_reason = latencyReason_(iss);
      iss.updated_at = istStamp(Date.now() / 1000);
      if (iss._isNew) newIssueRows.push(objToRow_(ISSUE_HEADER, iss));
      else updates.push({ row: iss._row, values: objToRow_(ISSUE_HEADER, iss) });
    });

    if (newIssueRows.length) appendRows_(CFG().tabs.issues, ISSUE_HEADER, newIssueRows);
    updates.forEach(function (u) {
      ss_().getSheetByName(CFG().tabs.issues)
           .getRange(u.row, 1, 1, ISSUE_HEADER.length).setValues([u.values]);
    });

    // ── stage 8: emit ───────────────────────────────────────────────────────────────────────
    var raisings = new Map();
    candidates.forEach(function (m) {
      if (m.thread_ref) return;                          // only TOP-LEVEL messages are raisings
      var a = assignByKey.get(m.channel_id + SEP + m.message_id);
      if (!a || !a.issue_id) return;
      if (!raisings.has(a.issue_id)) raisings.set(a.issue_id, []);
      raisings.get(a.issue_id).push({ channel: m.channel_id, source_id: m.channel_id + '/' +
                                      m.message_id, at: m.ts_iso, permalink: m.permalink });
    });

    var existingTickets = new Map();
    readTabObjects_(CFG().tabs.tickets).forEach(function (t, i) {
      existingTickets.set(String(t.idempotency_key), { row: i + 2, t: t });
    });

    var newTicketRows = [], ticketUpdates = [], created = 0, suppressed = 0;
    issueById.forEach(function (iss) {
      if (!iss._isNew && !iss._dirty) return;
      var tb = tokensByKind_(iss.tokens);
      var occ = (raisings.get(iss.issue_id) || []).slice();
      (linked.get(iss.issue_id) || []).forEach(function (dup) {
        occ = occ.concat(raisings.get(dup) || []);
      });
      occ.sort(function (a, b) { return String(a.at) < String(b.at) ? -1
                                      : String(a.at) > String(b.at) ? 1 : 0; });

      var d = buildDraft(iss, tb, occ, reg.registry, reg.denylist, knownDisp);
      var row = {
        idempotency_key: d.idempotency_key, issue_id: iss.issue_id,
        source_system: d.source_system, source_id: d.source_id,
        source_permalink: d.source_permalink, title: d.title, description: d.description,
        raiser: d.raiser, raiser_email: iss.raiser_email, dc_code: d.dc_code, intent: d.intent,
        entity_tokens_json: JSON.stringify(d.entity_tokens),
        flags_json: JSON.stringify(d.flags),
        kapture_ticket_ids_json: JSON.stringify(d.kapture_ticket_ids),
        reply_count: d.reply_count, first_response_latency_s: d.first_response_latency_s,
        state: d.state, duplicate_of: d.duplicate_of, suppressed: d.suppressed,
        suppressed_reason: d.suppressed_reason || '', occurrence_count: d.occurrence_count,
        occurrences_json: JSON.stringify(d.occurrences),
        first_raised_at: d.first_raised_at, last_raised_at: d.last_raised_at,
        acknowledged_at: '', acknowledge_error: '', agent_note: '', updated_by: '',
        updated_at: istStamp(Date.now() / 1000),
        public_token: publicToken_(d.idempotency_key)
      };

      var prev = existingTickets.get(d.idempotency_key);
      if (prev) {
        // Preserve everything a human owns. The pipeline owns the derived columns; the desk
        // owns state, the note and the acknowledgement — overwriting those would erase work.
        ['acknowledged_at', 'acknowledge_error', 'agent_note', 'updated_by'].forEach(function (k) {
          row[k] = prev.t[k] || '';
        });
        if (prev.t.state === 'RESOLVED' || prev.t.state === 'CLOSED' || prev.t.state === 'WORKING') {
          row.state = prev.t.state;
        }
        ticketUpdates.push({ row: prev.row, values: objToRow_(TICKET_HEADER, row) });
      } else {
        newTicketRows.push(objToRow_(TICKET_HEADER, row));
        if (d.suppressed) suppressed++; else created++;
      }
    });

    if (newTicketRows.length) appendRows_(CFG().tabs.tickets, TICKET_HEADER, newTicketRows);
    ticketUpdates.forEach(function (u) {
      ss_().getSheetByName(CFG().tabs.tickets)
           .getRange(u.row, 1, 1, TICKET_HEADER.length).setValues([u.values]);
    });

    // ── write the assignment back onto each message row, in ONE range write ──────────────────
    for (var w = 0; w < msgs.length; w++) {
      var a2 = assignByKey.get(msgs[w].channel_id + SEP + msgs[w].message_id);
      writeback[w] = writeback[w].concat(a2
        ? [a2.issue_id || '', a2.rule || '', a2.confidence, a2.reason || '']
        : ['', '', '', '']);
    }
    if (writeback.length) {
      ss_().getSheetByName(CFG().tabs.raw)
           .getRange(startRow + 2, RAW_COL.gated + 1, writeback.length, 8)
           .setValues(writeback);
    }

    stateSetAll_({
      'pipe:last_row': startRow + slice.rows.length,
      'pipe:last_at': istStamp(Date.now() / 1000),
      'pipe:last_processed': slice.rows.length,
      'pipe:classified': skipped ? 'SKIPPED: no exemplar index'
                                 : classified + ' classified, ' + novel + ' NOVEL',
      'pipe:last_ms': Date.now() - t0
    });
    SpreadsheetApp.flush();

    return { processed: slice.rows.length, issues_new: newIssueRows.length,
             tickets_created: created, tickets_suppressed: suppressed,
             duplicates_linked: dupPairs.length, classified: classified, novel: novel,
             ms: Date.now() - t0 };
  } finally {
    lock.releaseLock();
  }
}

/** A per-ticket token for the partner status page. Derived, not random, so it survives a
 *  rebuild of the row and never needs storing anywhere else to stay valid. */
function publicToken_(idempotencyKey) {
  return sha256Hex(prop_('INTAKE_SECRET', 'unset') + ':' + idempotencyKey).slice(0, 24);
}
