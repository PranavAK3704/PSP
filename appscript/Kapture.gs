/**
 * Kapture.gs — file a ticket into the CRM by email.
 *
 * ── WHAT THIS CHANGES ABOUT THE WHOLE PROJECT ───────────────────────────────────────────────
 * Kapture is where the support agents already work. An email to its intake address becomes a
 * ticket assigned to a real person. So this system stops being a ticketing system and becomes
 * a TRIAGE LAYER that feeds one — which is the right shape anyway, because Kapture is going to
 * be replaced. The Sheet stays the system of record; Kapture is a sink. When it goes, this one
 * file goes with it and nothing else moves.
 *
 * ── THE FORMAT IS IN ONE FUNCTION ON PURPOSE ────────────────────────────────────────────────
 * Kapture parses the email it receives, and nobody here knows the exact shape its parser wants.
 * So kaptureBody_() below is deliberately the only place the layout is decided: labelled plain
 * text, one field per line, section markers a parser can anchor on and a person can read. When
 * you find out what Kapture actually needs, edit that function and nothing else.
 *
 * ── WHY IT DOES NOT FILE EVERYTHING BY ITSELF ───────────────────────────────────────────────
 * Auto-filing is OFF. Every false positive becomes a real ticket a real agent has to work and
 * close, and the measured numbers are 82% coverage with 65% category precision. Filing into a
 * shared queue is far less forgiving than filing into your own Sheet: you can loosen the rule
 * later, you cannot un-spam a desk, and you get roughly one chance at its goodwill.
 *
 * So the default is a human clicking "Send to Kapture" in the queue. That click IS the
 * formatting step the CRM needs — done by a support agent who is paid to do it, rather than by
 * the Area Manager who only wanted to mention a problem.
 */

/** Where tickets are filed. A Script Property so it can be pointed at a test inbox first. */
function kaptureAddress_() {
  return prop_('KAPTURE_EMAIL', 'valmo.partnersupport@meesho.com');
}

/**
 * The email Kapture receives.
 *
 * EDIT THIS AND ONLY THIS when you learn the parser's real requirements. Everything else in
 * the project is indifferent to the layout.
 */
function kaptureBody_(t, dc, questions) {
  var info = t.intent && t.intent !== 'NOVEL' ? dispositionInfo(t.intent) : null;
  var ref = String(t.idempotency_key || '').slice(0, 8).toUpperCase();

  var toks = {};
  try { toks = JSON.parse(t.entity_tokens_json || '{}'); } catch (e) { toks = {}; }
  var ids = Object.keys(toks).sort().map(function (k) {
    return '  ' + k + ': ' + (toks[k] || []).map(function (r) { return r.value; }).join(', ');
  }).filter(function (l) { return l.indexOf(': ') === l.length - 2 ? false : true; });

  var lines = [];
  lines.push('Reference: ' + ref);
  lines.push('Category: ' + (info ? info.label : 'Uncategorised'));
  if (info && info.team) lines.push('Suggested team: ' + info.team);
  lines.push('Raised by: ' + String(t.raiser || '') +
             (t.raiser_email ? ' <' + t.raiser_email + '>' : ''));
  if (t.dc_code) {
    lines.push('Delivery centre: ' + t.dc_code +
               (dc && dc.known ? ' — ' + dc.name : ' (no contact on file)'));
  }
  lines.push('Raised at: ' + String(t.first_raised_at || t.last_raised_at || ''));

  // The recurrence line is the one thing this system knows that the CRM cannot: file the same
  // problem seven times through a form and Kapture has seven unrelated tickets.
  var occ = Number(t.occurrence_count || 1);
  if (occ > 1) {
    lines.push('RECURRING: raised ' + occ + ' times, first on ' +
               String(t.first_raised_at || '').slice(0, 10) + '. Treat as one ongoing problem.');
  }

  lines.push('', '--- WHAT THE PARTNER SAID ---',
             String(t.description || '').split('\n')[0] || '(no text — see the Slack link)');

  if (t.answer) {
    lines.push('', '--- DETAILS THEY ADDED WHEN ASKED ---');
    if (t.asked_for) lines.push('(we asked: ' + String(t.asked_for) + ')');
    lines.push(String(t.answer));
    if (t.answer_identifiers) lines.push('Identifiers in that reply: ' + t.answer_identifiers);
  } else if (questions && questions.length) {
    // Say what is missing rather than letting the agent discover it. An agent who knows the
    // first question to ask is an agent who does not have to read the whole thread first.
    lines.push('', '--- NOT YET SUPPLIED ---');
    questions.forEach(function (q) { lines.push('  - ' + q); });
  }

  if (ids.length) lines.push('', '--- IDENTIFIERS FOUND ---', ids.join('\n'));

  var flags = [];
  try { flags = JSON.parse(t.flags_json || '[]'); } catch (e) { flags = []; }
  if (flags.length) lines.push('', '--- CHECKS THAT FAILED ---', '  ' + flags.join('\n  '));

  lines.push('', '--- SOURCE ---');
  if (t.source_permalink) lines.push('Slack: ' + t.source_permalink);
  var url = prop_('WEBAPP_URL', '');
  if (url && t.public_token) lines.push('Intake record: ' + url + '?t=' + t.public_token);
  lines.push('Filed automatically from a partner conversation. Reply on the Slack thread to ' +
             'reach the person who raised it.');

  var subject = '[' + ref + '] ' + (info ? info.label : 'Uncategorised') +
                (t.dc_code ? ' — ' + t.dc_code : '');
  return { subject: prop_('KAPTURE_SUBJECT_PREFIX', '') + subject, text: lines.join('\n') };
}

/** Everything that would stop this ticket being filed, as readable sentences. Empty = fine. */
function kaptureBlockers_(t, opts) {
  var out = [];
  var sup = t.suppressed === true || String(t.suppressed).toLowerCase() === 'true';
  if (sup) out.push('it was withheld: ' + String(t.suppressed_reason || '').slice(0, 80));
  if (t.filed_at) out.push('already filed at ' + t.filed_at);
  if (opts && opts.auto) {
    var C = CFG().kapture;
    if (C.autoFileNeedsCategory && (!t.intent || t.intent === 'NOVEL')) {
      out.push('no category yet — a human decides this one');
    }
    if (C.autoFileNeedsIdentifier) {
      var flags = [];
      try { flags = JSON.parse(t.flags_json || '[]'); } catch (e) {}
      if (flags.indexOf('no_actionable_identifier') >= 0) {
        out.push('nothing in it anybody could act on');
      }
    }
  }
  return out;
}

/**
 * File one ticket. Called by the queue's "Send to Kapture" button.
 *
 * `force` lets an agent file something the auto-rules would refuse — deliberately available,
 * because a human looking at the ticket knows things the rules do not.
 */
function fileToKapture(key, force) {
  var me = requireAgent_();
  return withLock_(function () {
    var rows = readTabObjects_(CFG().tabs.tickets);
    var row = 0, t = null;
    for (var i = 0; i < rows.length; i++) {
      if (String(rows[i].idempotency_key) === String(key)) { row = i + 2; t = rows[i]; break; }
    }
    if (!t) throw new Error('No such ticket.');

    var blockers = kaptureBlockers_(t, { auto: false });
    if (blockers.length && !force) {
      throw new Error('Not filed — ' + blockers.join('; ') + '.');
    }

    var dc = dcContact_(String(t.dc_code || ''));
    var b = kaptureBody_(t, dc, questionsFor(t));
    var to = kaptureAddress_();
    var patch = {};
    try {
      if (notifyMode_() === 'email-dry') {
        patch.file_error = 'DRY RUN — would file to ' + to + ' as "' + b.subject + '"';
      } else {
        MailApp.sendEmail({ to: to, subject: b.subject, body: b.text,
                            replyTo: String(t.raiser_email || '') || undefined,
                            name: 'Partner Intake' });
        patch.filed_at = istStamp(Date.now() / 1000);
        patch.filed_ref = b.subject.slice(0, 120);
        patch.file_error = '';
      }
      patch.updated_by = me.email;
      patch.updated_at = istStamp(Date.now() / 1000);
      // Filing IS the handover. Leaving it "Open" after it has gone to a CRM means two queues
      // disagree about who owns it.
      if (!t.desk_state || t.desk_state === 'OPEN') patch.desk_state = 'WORKING';
    } catch (e) {
      patch.file_error = String(e.message || e).slice(0, 240);
    }
    writeDesk_(row, patch);
    SpreadsheetApp.flush();
    if (patch.file_error && !patch.filed_at) throw new Error(patch.file_error);
    return { ok: true, to: to, subject: b.subject, dry: notifyMode_() === 'email-dry' };
  });
}

/**
 * The trigger path. Files only what the conservative rules allow, and only when autoFile is on.
 *
 * Deliberately separate from fileToKapture so the rules that protect the desk cannot be
 * bypassed by accident — a human has to pass `force` explicitly, from a button, as themselves.
 */
function autoFileToKapture() {
  if (!CFG().kapture.autoFile) return { skipped: 'CFG().kapture.autoFile is off' };
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(0)) return { skipped: 'another run holds the lock' };
  try {
    var rows = readTabObjects_(CFG().tabs.tickets);
    var filed = 0, held = 0, failed = 0;
    var quota = MailApp.getRemainingDailyQuota(), used = 0;
    for (var i = 0; i < rows.length; i++) {
      var t = rows[i];
      if (t.filed_at) continue;
      if (kaptureBlockers_(t, { auto: true }).length) { held++; continue; }
      if (quota - used < 20) break;
      var dc = dcContact_(String(t.dc_code || ''));
      var b = kaptureBody_(t, dc, questionsFor(t));
      var patch = {};
      try {
        MailApp.sendEmail({ to: kaptureAddress_(), subject: b.subject, body: b.text,
                            name: 'Partner Intake' });
        used++; filed++;
        patch.filed_at = istStamp(Date.now() / 1000);
        patch.filed_ref = b.subject.slice(0, 120);
        patch.file_error = '';
        if (!t.desk_state || t.desk_state === 'OPEN') patch.desk_state = 'WORKING';
      } catch (e) {
        patch.file_error = String(e.message || e).slice(0, 240);
        failed++;
      }
      writeDesk_(i + 2, patch);
    }
    stateSetAll_({ 'kapture:last_at': istStamp(Date.now() / 1000),
                   'kapture:filed': filed, 'kapture:held': held, 'kapture:failed': failed });
    SpreadsheetApp.flush();
    return { filed: filed, held: held, failed: failed };
  } finally {
    lock.releaseLock();
  }
}

/** What the email would look like, without sending it. For checking the format against
 *  Kapture's parser before turning anything on. */
function previewKapture(key) {
  var rows = readTabObjects_(CFG().tabs.tickets);
  for (var i = 0; i < rows.length; i++) {
    if (key && String(rows[i].idempotency_key) !== String(key)) continue;
    var t = rows[i];
    var b = kaptureBody_(t, dcContact_(String(t.dc_code || '')), questionsFor(t));
    var out = 'To: ' + kaptureAddress_() + '\nSubject: ' + b.subject + '\n\n' + b.text;
    Logger.log(out);
    return out;
  }
  return 'No ticket found. Run it with no argument to preview the first one.';
}
