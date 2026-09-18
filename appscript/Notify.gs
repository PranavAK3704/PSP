/**
 * Notify.gs — tell the person who raised it that we heard them.
 *
 * ── WHY EMAIL, AND WHY FROM HERE ────────────────────────────────────────────────────────────
 * Three earlier routes failed. A Slack reaction tells you nothing about where to look. A reply
 * in-channel adds clutter to the channel the ticket came from. SMTP from the previous host
 * timed out, because that host resolves smtp.gmail.com IPv6-first and has no IPv6 egress.
 * MailApp goes out over HTTPS from Google's own infrastructure, so none of that applies.
 *
 * ── THE LINK IS CURRENTLY DORMANT, AND THE MAIL SAYS SO ─────────────────────────────────────
 * The status page is deployed with Meesho-only access, so a partner opening the link would hit
 * a sign-in they can never pass. Until "Anyone" is approved, the mail carries the ticket
 * reference, its category and its state IN THE BODY, and the link is only added for recipients
 * on the Meesho domain. An email whose only value is a link nobody can open is worse than no
 * email — it reads as a system that does not work.
 *
 * ── FIVE GUARDS ─────────────────────────────────────────────────────────────────────────────
 * Each one exists because its absence produced a real failure: duplicate mail on every run, a
 * partner emailed about a ticket the desk had deliberately withheld, a crash on an empty
 * address, mail sent to a bot, and a run that burned the daily quota in one go.
 */

function notifyMode_() { return prop_('INTAKE_NOTIFY', 'off'); }

/** Send acknowledgements for tickets that have not had one. Safe to call every run. */
function sendAcknowledgements() {
  var mode = notifyMode_();
  if (mode === 'off') return { skipped: 'INTAKE_NOTIFY=off' };

  var lock = LockService.getScriptLock();
  if (!lock.tryLock(0)) return { skipped: 'another run holds the lock' };
  try {
    var rows = readTabObjects_(CFG().tabs.tickets);
    var sh = ss_().getSheetByName(CFG().tabs.tickets);
    if (!sh) return { sent: 0 };

    var ackCol = TICKET_HEADER.indexOf('acknowledged_at') + 1;
    var errCol = TICKET_HEADER.indexOf('acknowledge_error') + 1;
    var quota = MailApp.getRemainingDailyQuota();
    var sent = 0, failed = 0, skipped = 0, updates = [];

    for (var i = 0; i < rows.length; i++) {
      var t = rows[i], row = i + 2;

      // guard 1 — already acknowledged. The whole point of an idempotent ticket id.
      if (t.acknowledged_at) continue;
      // guard 2 — the desk deliberately withheld this one. Telling the partner we raised it
      // would be a lie, and telling them we did not is worse.
      if (t.suppressed === true || String(t.suppressed).toLowerCase() === 'true') { skipped++; continue; }
      // guard 3 — no address. Recorded rather than silently skipped, so the count adds up.
      var to = String(t.raiser_email || '').trim();
      if (!to) {
        updates.push({ row: row, col: errCol,
                       value: 'no email on the raiser profile (needs users:read.email)' });
        failed++; continue;
      }
      // guard 4 — never mail a bot. It bounces, and it looks like the system is talking to
      // itself in whatever inbox catches it.
      if (/^B[A-Z0-9]+$/.test(String(t.raiser || '')) || to.indexOf('@') < 0) { skipped++; continue; }
      // guard 5 — leave headroom rather than dying mid-batch with half the queue sent.
      if (quota - sent < 20) {
        updates.push({ row: row, col: errCol,
                       value: 'daily mail quota nearly exhausted; will retry next run' });
        break;
      }

      var body = acknowledgementBody_(t, to);
      try {
        if (mode === 'email-dry') {
          updates.push({ row: row, col: errCol, value: 'DRY RUN — not sent (INTAKE_NOTIFY=email-dry)' });
        } else {
          MailApp.sendEmail({ to: to, subject: body.subject, htmlBody: body.html,
                              body: body.text, name: 'Partner Support Intake' });
          updates.push({ row: row, col: ackCol, value: istStamp(Date.now() / 1000) });
          updates.push({ row: row, col: errCol, value: '' });
          sent++;
        }
      } catch (e) {
        updates.push({ row: row, col: errCol, value: String(e.message || e).slice(0, 220) });
        failed++;
      }
    }

    updates.forEach(function (u) { sh.getRange(u.row, u.col).setValue(u.value); });
    stateSetAll_({ 'notify:last_at': istStamp(Date.now() / 1000),
                   'notify:last_sent': sent, 'notify:last_failed': failed });
    SpreadsheetApp.flush();
    return { sent: sent, failed: failed, skipped: skipped, mode: mode, quota_left: quota - sent };
  } finally {
    lock.releaseLock();
  }
}

/** The mail itself. Plain enough to read on a phone, which is where it will be read. */
function acknowledgementBody_(t, to) {
  var ref = String(t.idempotency_key || '').slice(0, 8).toUpperCase();
  var category = t.intent && t.intent !== 'NOVEL' ? String(t.intent).replace(/_/g, ' ')
                                                  : 'being categorised';
  var state = { NEW: 'Received', IDENTIFIED: 'Received', OPEN: 'Being looked at',
                WORKING: 'Being worked on', RESOLVED: 'Resolved',
                CLOSED: 'Closed' }[String(t.state)] || 'Received';

  // Only Meesho addresses get the link, because only they can open it today. When "Anyone"
  // access is approved, delete this condition and the link goes to everybody — nothing else
  // in this file changes.
  var internal = /@meesho\.com$/i.test(to);
  var url = prop_('WEBAPP_URL', '');
  var link = (internal && url) ? url + '?t=' + encodeURIComponent(t.public_token) : '';

  var lines = [
    'We have your issue and it is in the queue.',
    '',
    'Reference : ' + ref,
    'Issue     : ' + String(t.title || '').slice(0, 160),
    'Category  : ' + category,
    'Status    : ' + state,
    ''
  ];
  if (link) lines.push('Track it here: ' + link, '');
  lines.push('You do not need to reply to this message. If the details above are wrong, say so',
             'in the same channel where you raised it and the correction will be picked up.');

  var esc = function (s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  };
  var html =
    '<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;font-size:14px;' +
    'line-height:1.55;color:#1a1a1a;max-width:560px">' +
    '<p>We have your issue and it is in the queue.</p>' +
    '<table style="border-collapse:collapse;margin:16px 0">' +
    ['Reference', 'Issue', 'Category', 'Status'].map(function (k, i) {
      var v = [ref, String(t.title || '').slice(0, 160), category, state][i];
      return '<tr><td style="padding:3px 16px 3px 0;color:#666">' + k + '</td>' +
             '<td style="padding:3px 0"><b>' + esc(v) + '</b></td></tr>';
    }).join('') + '</table>' +
    (link ? '<p><a href="' + esc(link) + '" style="background:#1a1a1a;color:#fff;padding:9px 16px;' +
            'border-radius:6px;text-decoration:none;display:inline-block">Track this issue</a></p>' : '') +
    '<p style="color:#666;font-size:13px">You do not need to reply to this message. If the ' +
    'details above are wrong, say so in the same channel where you raised it and the ' +
    'correction will be picked up.</p></div>';

  return { subject: '[' + ref + '] ' + String(t.title || 'Your issue').slice(0, 90),
           text: lines.join('\n'), html: html };
}
