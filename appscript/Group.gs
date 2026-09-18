/**
 * Group.gs — several messages become ONE issue, then that issue becomes a register row.
 *
 * ── THE ONE DELIBERATE DIVERGENCE FROM THE PYTHON PIPELINE ──────────────────────────────────
 * Python re-derives grouping from scratch for every run: it replays every message, rebuilds
 * every issue, and deletes and rewrites the stage tables. That is correct and unaffordable
 * here — the 6-minute execution cap and a raw_messages tab heading for half a million rows
 * both rule it out.
 *
 * So this runs INCREMENTALLY, and two things change:
 *
 *   1. Only messages after the row watermark are assigned, and the assignment is written back
 *      into the message's own row. The watermark IS the idempotency: a row is grouped exactly
 *      once, so there is no run_id to scope and nothing to delete and rebuild.
 *
 *   2. Grouping loads the OPEN ISSUE INDEX — issues whose anchor is inside the longest window
 *      (180 days) — rather than replaying every message that ever built them. Same answer,
 *      bounded memory, because an issue's window is measured from its ANCHOR and the anchor
 *      never moves.
 *
 * The register's derived fields are maintained the same way, for the same reason: reply_count
 * only grows and the first response is the earliest qualifying reply, so both can be updated as
 * messages arrive instead of rescanning every member. Recomputing them would mean scanning
 * raw_messages for each issue's members on every run, which is the one thing this design is
 * built to avoid.
 */

function issueId_(channelId, messageId) { return 'ISS-' + channelId + '-' + messageId; }

/** The LONGEST window among the shared token kinds wins. A message sharing both a waybill (14d)
 *  and a mobile (180d) with an issue is judged against 180 days — the person outlives the
 *  parcel. An empty kind set falls back to the default. */
function windowFor_(kinds) {
  var G = CFG().grouping, best = G.defaultWindowS;
  var seen = false;
  kinds.forEach(function (k) {
    var w = G.windows.hasOwnProperty(k) ? G.windows[k] : G.defaultWindowS;
    if (!seen || w > best) { best = w; seen = true; }
  });
  return seen ? best : G.defaultWindowS;
}

function tokKey_(kind, value) { return kind + SEP + value; }

/**
 * Assign each message to an issue. `messages` must be in ascending ts_epoch order.
 *
 * Each message: {channel_id, message_id, ts_epoch, thread_ref, tokens:[[kind,value]],
 *                evidence_decision}
 * Each open issue: {issue_id, anchor_channel_id, anchor_message_id, ts_epoch,
 *                   tokens:[[kind,value]]}
 *
 * Returns {assignments:[{channel_id, message_id, issue_id, rule, confidence, reason}],
 *          newIssues:[{issue_id, anchor_channel_id, anchor_message_id, ts_epoch, tokens}],
 *          tokenGrowth: Map<issue_id, [[kind,value]]>}
 */
function groupMessages(messages, openIssues) {
  var G = CFG().grouping;
  var strong = G.joinOn, weak = G.joinWeak ? G.joinOnWeak : [];

  // anchor_of is keyed on the ANCHOR message only — never on a message that merely joined an
  // issue. That is deliberate and load-bearing: a reply resolves by thread_ref only when its
  // parent is itself an issue anchor. A reply to a message that entity-joined somewhere falls
  // through to `unassigned` and goes to a human, because we do not actually know it belongs.
  var anchorOf = new Map();
  var issueTokens = new Map();     // insertion-ordered: earliest-created issue wins a tie
  var issueTs = new Map();

  (openIssues || []).forEach(function (it) {
    anchorOf.set(it.anchor_channel_id + SEP + it.anchor_message_id, it.issue_id);
    var s = new Set();
    (it.tokens || []).forEach(function (t) { s.add(tokKey_(t[0], t[1])); });
    issueTokens.set(it.issue_id, s);
    issueTs.set(it.issue_id, Number(it.ts_epoch) || 0);
  });

  var assignments = [], newIssues = [], growth = new Map();

  for (var mi = 0; mi < messages.length; mi++) {
    var m = messages[mi];
    var mine = new Set();
    (m.tokens || []).forEach(function (t) { mine.add(tokKey_(t[0], t[1])); });

    var issue = null, rule = null, conf = 0.0, reason = '';

    // ── rule 1: a thread reply belongs to its parent's issue. Free, and always right. ────────
    if (m.thread_ref) {
      var parent = m.channel_id + SEP + m.thread_ref;
      if (anchorOf.has(parent)) {
        issue = anchorOf.get(parent);
        rule = 'thread_ref';
        conf = G.conf.threadRef;
        reason = 'thread_ref -> anchor ' + m.thread_ref;
      }
    }

    // ── rule 2: shares a strong identifier with an open issue, inside that kind's window ─────
    if (!issue && mine.size) {
      var passes = [[strong, 'entityJoinStrong', 'strong'], [weak, 'entityJoinWeak', 'weak']];
      for (var p = 0; p < passes.length && !issue; p++) {
        var kinds = passes[p][0];
        if (!kinds.length) continue;
        var cand = new Set();
        mine.forEach(function (k) {
          if (kinds.indexOf(k.split(SEP)[0]) >= 0) cand.add(k);
        });
        if (!cand.size) continue;

        var bestId = null, bestShared = null;
        issueTokens.forEach(function (itoks, iid) {
          var shared = [];
          cand.forEach(function (k) { if (itoks.has(k)) shared.push(k); });
          if (!shared.length) return;
          var sk = new Set(shared.map(function (k) { return k.split(SEP)[0]; }));
          var win = windowFor_(sk);
          if (Math.abs(m.ts_epoch - issueTs.get(iid)) <= win) {
            // Most recent anchor wins; strict > means an exact tie goes to the first in
            // insertion order, i.e. the earliest-created issue.
            if (bestId === null || issueTs.get(iid) > issueTs.get(bestId)) {
              bestId = iid; bestShared = shared;
            }
          }
        });

        if (bestId !== null) {
          issue = bestId;
          rule = 'entity_join_' + passes[p][2];
          conf = G.conf[passes[p][1]];
          var sortedShared = bestShared.slice().sort();
          var kindSet = new Set(sortedShared.map(function (k) { return k.split(SEP)[0]; }));
          reason = 'shares ' + sortedShared.map(function (k) {
                     var parts = k.split(SEP); return parts[0] + '=' + parts[1];
                   }).join(', ') + ' with ' + issue + ' within ' +
                   Math.round(windowFor_(kindSet) / 86400) + 'd';
        }
      }
    }

    // ── rule 3: a new issue, or honestly unassigned ──────────────────────────────────────────
    if (!issue) {
      if (m.thread_ref) {
        // The parent is gated, or predates the corpus. We do not know what this belongs to,
        // and guessing would attach a real reply to the wrong ticket.
        rule = 'unassigned';
        reason = 'reply whose parent is gated or not in corpus';
      } else if (m.evidence_decision === 'orphan') {
        rule = 'unassigned';
        reason = 'bare follow-up with no positive evidence';
      } else {
        issue = issueId_(m.channel_id, m.message_id);
        rule = 'new_issue';
        conf = G.conf.threadRef;              // borrows thread_ref's 1.0 rather than owning a key
        reason = 'top-level message with no prior match — new issue anchored here';
        anchorOf.set(m.channel_id + SEP + m.message_id, issue);
        issueTokens.set(issue, new Set(mine));
        issueTs.set(issue, m.ts_epoch);
        newIssues.push({ issue_id: issue, anchor_channel_id: m.channel_id,
                         anchor_message_id: m.message_id, ts_epoch: m.ts_epoch,
                         tokens: Array.from(mine).map(function (k) { return k.split(SEP); }) });
      }
    }

    // A joined message GROWS the issue's identifier set — but never moves its timestamp. The
    // window anchor does not slide, or a long-running issue would never age out.
    if (issue && rule !== 'new_issue') {
      var set = issueTokens.get(issue);
      var added = [];
      mine.forEach(function (k) { if (!set.has(k)) { set.add(k); added.push(k.split(SEP)); } });
      if (added.length) {
        if (!growth.has(issue)) growth.set(issue, []);
        growth.set(issue, growth.get(issue).concat(added));
      }
    }

    assignments.push({ channel_id: m.channel_id, message_id: m.message_id, issue_id: issue,
                       rule: rule, confidence: conf, reason: reason });
  }

  return { assignments: assignments, newIssues: newIssues, tokenGrowth: growth };
}


// ── register ────────────────────────────────────────────────────────────────────────────────

/** Identifier kinds that move an issue to IDENTIFIED. NOTE this is NOT the same set as Emit's
 *  ACTIONABLE, which also counts dc_code — a hub code says WHERE, not WHICH, so it identifies
 *  a place to ask rather than a thing to act on. */
var IDENTIFYING = ['kapture_id', 'waybill', 'mobile', 'pilot_id', 'email'];

/**
 * The state an issue should be in, given what is known about it.
 *
 * NEW → IDENTIFIED → OPEN → RESOLVED → CLOSED, plus FLAGGED. Register only ever emits the first
 * three: RESOLVED and CLOSED are a judgement about whether the partner's problem went away, and
 * nothing in a message stream can assert that on its own. A human moves it in the UI.
 *
 * Priority: ANY reply at all → OPEN, regardless of identifiers. Somebody engaged.
 */
function registerState_(tokensByKind, replyCount) {
  if (replyCount > 0) return 'OPEN';
  for (var i = 0; i < IDENTIFYING.length; i++) {
    var k = IDENTIFYING[i];
    if (tokensByKind[k] && tokensByKind[k].length) return 'IDENTIFIED';
  }
  if (tokensByKind.dc_code && tokensByKind.dc_code.length) return 'IDENTIFIED';
  return 'NEW';
}

/**
 * Fold one newly-arrived member message into an issue's derived fields, in place.
 *
 * `iss` carries the running counters; `msg` is the new member. Called in ts order, so the first
 * qualifying reply seen IS the first response — no rescan of the issue's members is needed.
 *
 * Two exclusions, both of which leave latency NULL and record a reason rather than a number:
 *   · the reply is by the raiser themselves — answering your own message is not a response
 *   · the reply is a gated ack — "noted 👍" is not a first response either
 * Both produce a self-explaining latency_excluded_reason, because a silent NULL in a latency
 * column reads as "instant" to anyone skimming.
 */
function foldMember_(iss, msg) {
  if (msg.message_id === iss.anchor_message_id) return;
  iss.reply_count = (iss.reply_count || 0) + 1;
  if (String(msg.author_id || '') === String(iss.raiser_id || '')) {
    iss.replies_from_raiser = (iss.replies_from_raiser || 0) + 1;
  }
  if (iss.first_response_latency_s === null || iss.first_response_latency_s === undefined ||
      iss.first_response_latency_s === '') {
    var isRaiser = String(msg.author_id || '') === String(iss.raiser_id || '');
    if (!isRaiser && !msg.gated) {
      iss.first_response_latency_s = round_(msg.ts_epoch - iss.ts_epoch, 3);
      iss.latency_excluded_reason = '';
    }
  }
}

/** The reason string for an issue that still has no qualifying first response. */
function latencyReason_(iss) {
  if (iss.first_response_latency_s !== null && iss.first_response_latency_s !== undefined &&
      iss.first_response_latency_s !== '') return '';
  if (!iss.reply_count) return 'no replies';
  return (iss.replies_from_raiser === iss.reply_count)
    ? 'replies exist but none qualify: all from the raiser'
    : 'replies exist but none qualify: all gated acks';
}
