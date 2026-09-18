/**
 * Notify.gs — tell the people who need to know. Two different audiences, two different messages.
 *
 * ── WHO GETS TOLD ───────────────────────────────────────────────────────────────────────────
 * The person who typed the message in Slack is usually a Meesho Area Manager raising an issue
 * ON BEHALF OF a Delivery Centre. The DC is the party whose problem it is, is not a Meesho
 * employee, has no Meesho account, and today hears nothing at all.
 *
 * So there are two notifications and they are not the same message:
 *
 *   RAISER (the AM)  "We have your issue and it is in the queue."
 *   DC               "An issue was raised for your centre." — they did not raise it, and
 *                    telling them "we have your issue" is confusing and slightly wrong.
 *
 * The DC is found from the dc_code the pipeline already extracts, looked up in the _dc_contacts
 * tab. No code, no contact, no message — and that is recorded rather than silent, because "the
 * DC was never told" is exactly the thing you want to be able to count.
 *
 * ── WHY EMAIL FIRST, AND WHY BEHIND A SEAM ──────────────────────────────────────────────────
 * Email ships today with zero approvals. SMS to an Indian number needs DLT registration under
 * Meesho's principal entity and a pre-approved template, and getting the category wrong
 * (Service-Implicit, not Promotional) means messages are DND-scrubbed and only delivered
 * 9am-9pm — while DC problems get raised at 2am. WhatsApp needs a template approved on a
 * Business account, and Meta begins charging for service and in-window utility messages from
 * 1 October 2026. Both are somebody else's paperwork, so both are adapters behind dispatch_()
 * rather than blockers.
 *
 * ── FIVE GUARDS, AND THE ONE THAT WAS WRONG ─────────────────────────────────────────────────
 * The quota guard used to count SENDS against a RECIPIENT quota. Adding the DC as a second
 * recipient makes that arithmetic wrong by a factor of two, and the failure is a batch that
 * dies halfway through a busy evening with nothing in the log. It counts recipients now.
 */

var CONTACT_HEADER = ['dc_code', 'dc_name', 'email', 'mobile', 'whatsapp', 'active', 'notes'];

function notifyMode_() { return prop_('INTAKE_NOTIFY', 'off'); }

/** The DC contact directory, keyed by uppercase hub code. Hand-maintained, like _dc_codes. */
function contacts_() {
  if (_cache.contacts) return _cache.contacts;
  var m = new Map();
  readTabObjects_(CFG().tabs.contacts).forEach(function (r) {
    var code = String(r.dc_code || '').trim().toUpperCase();
    if (!code) return;
    if (String(r.active).toLowerCase() === 'false') return;
    m.set(code, {
      code: code, name: String(r.dc_name || code),
      email: String(r.email || '').trim(),
      // A mobile typed into a Sheet cell arrives as a NUMBER, and '+91 99000 00001' arrives as
      // text — two shapes for the same phone. Normalise to bare 10 digits here, once.
      mobile: normalisePhone_(r.mobile),
      whatsapp: normalisePhone_(r.whatsapp) || normalisePhone_(r.mobile)
    });
  });
  _cache.contacts = m;
  return m;
}

/** Bare 10-digit Indian mobile, or ''. Anything else is rejected rather than half-sent. */
function normalisePhone_(v) {
  var d = String(v === null || v === undefined ? '' : v).replace(/[^0-9]/g, '');
  if (d.length === 12 && d.indexOf('91') === 0) d = d.slice(2);
  if (d.length === 11 && d.indexOf('0') === 0) d = d.slice(1);
  return /^[6-9][0-9]{9}$/.test(d) ? d : '';
}

/**
 * The one place a message leaves this system. Every channel is a case here.
 *
 * SMS and WhatsApp deliberately THROW rather than silently doing nothing: a notification
 * channel that is switched on but unconfigured must not look like a delivered message. The
 * error lands in dc_notify_error where it can be counted.
 */
function dispatch_(channel, to, subject, text, html) {
  if (channel === 'email') {
    MailApp.sendEmail({ to: to, subject: subject, body: text, htmlBody: html,
                        name: 'Partner Support Intake' });
    return { ok: true, channel: 'email' };
  }
  if (channel === 'sms') {
    var url = prop_('SMS_GATEWAY_URL', '');
    if (!url) throw new Error('SMS is enabled but SMS_GATEWAY_URL is not set. Route through the ' +
                              'aggregator already declared in Meesho’s DLT chain — a new ' +
                              'account cannot send under Meesho’s header.');
    // Deliberately minimal and generic: the exact payload depends on which aggregator Meesho
    // already contracts with, and guessing one here would be a wrong answer that looks finished.
    var res = UrlFetchApp.fetch(url, {
      method: 'post', contentType: 'application/json', muteHttpExceptions: true,
      payload: JSON.stringify({ to: '91' + to, text: text,
                                template_id: prop_('SMS_TEMPLATE_ID', ''),
                                entity_id: prop_('SMS_ENTITY_ID', '') })
    });
    if (res.getResponseCode() >= 300) {
      throw new Error('SMS gateway HTTP ' + res.getResponseCode() + ': ' +
                      String(res.getContentText()).slice(0, 160));
    }
    return { ok: true, channel: 'sms' };
  }
  if (channel === 'whatsapp') {
    var token = prop_('WA_TOKEN', ''), phoneId = prop_('WA_PHONE_ID', ''),
        tpl = prop_('WA_TEMPLATE', '');
    if (!token || !phoneId || !tpl) {
      throw new Error('WhatsApp is enabled but WA_TOKEN / WA_PHONE_ID / WA_TEMPLATE are not all ' +
                      'set. These come from whoever runs Meesho’s WhatsApp Business account.');
    }
    var r2 = UrlFetchApp.fetch('https://graph.facebook.com/v21.0/' + phoneId + '/messages', {
      method: 'post', contentType: 'application/json', muteHttpExceptions: true,
      headers: { Authorization: 'Bearer ' + token },
      payload: JSON.stringify({
        messaging_product: 'whatsapp', to: '91' + to, type: 'template',
        template: { name: tpl, language: { code: 'en' },
                    components: [{ type: 'body',
                                   parameters: text.split('|').map(function (p) {
                                     return { type: 'text', text: p };
                                   }) }] }
      })
    });
    if (r2.getResponseCode() >= 300) {
      throw new Error('WhatsApp HTTP ' + r2.getResponseCode() + ': ' +
                      String(r2.getContentText()).slice(0, 160));
    }
    return { ok: true, channel: 'whatsapp' };
  }
  throw new Error('unknown channel: ' + channel);
}

/**
 * Send what is owed. Safe to call every run; it only ever sends what has not been sent.
 *
 * The raiser and the DC are tracked with SEPARATE columns, so a DC with no contact row does not
 * block the raiser's acknowledgement and does not get retried forever.
 */
function sendAcknowledgements() {
  var mode = notifyMode_();
  if (mode === 'off') return { skipped: 'INTAKE_NOTIFY=off' };

  var lock = LockService.getScriptLock();
  if (!lock.tryLock(0)) return { skipped: 'another run holds the lock' };
  try {
    var rows = readTabObjects_(CFG().tabs.tickets);
    var sh = ss_().getSheetByName(CFG().tabs.tickets);
    if (!sh) return { sent: 0 };

    var dry = mode === 'email-dry';
    // RECIPIENTS, not sends. Two recipients per ticket makes a send-based count wrong by 2x,
    // and the failure mode is the last batch of a heavy day dying mid-loop with no error.
    var quota = MailApp.getRemainingDailyQuota();
    var used = 0;
    var sent = 0, failed = 0, skipped = 0, dcSent = 0, dcSkipped = 0;
    var patches = [];

    for (var i = 0; i < rows.length; i++) {
      var t = rows[i], row = i + 2;
      var sup = t.suppressed === true || String(t.suppressed).toLowerCase() === 'true';
      if (sup) { skipped++; continue; }

      var patch = {};

      // ── 1. the raiser ─────────────────────────────────────────────────────────────────────
      if (!t.acknowledged_at) {
        var to = String(t.raiser_email || '').trim();
        if (!to || to.indexOf('@') < 0) {
          patch.acknowledge_error = 'no email on the raiser profile (needs users:read.email)';
          failed++;
        } else if (/^B[A-Z0-9]+$/.test(String(t.raiser || ''))) {
          skipped++;                                   // a bot raised it; nobody to tell
        } else if (quota - used < 20) {
          patch.acknowledge_error = 'daily mail quota nearly exhausted; will retry next run';
        } else {
          var b = raiserBody_(t, to);
          try {
            if (dry) { patch.acknowledge_error = 'DRY RUN — not sent (INTAKE_NOTIFY=email-dry)'; }
            else {
              dispatch_('email', to, b.subject, b.text, b.html);
              used++; sent++;
              patch.acknowledged_at = istStamp(Date.now() / 1000);
              patch.acknowledge_error = '';
            }
          } catch (e) {
            patch.acknowledge_error = String(e.message || e).slice(0, 220);
            failed++;
          }
        }
      }

      // ── 2. the delivery centre ────────────────────────────────────────────────────────────
      if (!t.dc_notified_at && CFG().dcNotify.enabled) {
        var code = String(t.dc_code || '').trim().toUpperCase();
        var c = code ? contacts_().get(code) : null;
        if (!code) {
          patch.dc_notify_error = 'no DC code on this ticket';
          dcSkipped++;
        } else if (!c) {
          // Counted, not silent. "How many DCs never heard from us" is the number that tells
          // you the contact directory is out of date.
          patch.dc_notify_error = 'no contact row for ' + code + ' in ' + CFG().tabs.contacts;
          dcSkipped++;
        } else {
          var d = dcBody_(t, c);
          var chans = [];
          if (CFG().dcNotify.email && c.email) chans.push(['email', c.email]);
          if (CFG().dcNotify.whatsapp && c.whatsapp) chans.push(['whatsapp', c.whatsapp]);
          if (CFG().dcNotify.sms && c.mobile) chans.push(['sms', c.mobile]);
          if (!chans.length) {
            patch.dc_notify_error = 'contact row for ' + code + ' has no address on an enabled channel';
            dcSkipped++;
          } else if (dry) {
            patch.dc_notify_error = 'DRY RUN — would notify ' +
              chans.map(function (x) { return x[0] + ':' + x[1]; }).join(', ');
          } else {
            var okAny = false, errs = [];
            for (var ci = 0; ci < chans.length; ci++) {
              if (chans[ci][0] === 'email' && quota - used < 20) {
                errs.push('mail quota nearly exhausted'); continue;
              }
              try {
                dispatch_(chans[ci][0], chans[ci][1], d.subject, d.text, d.html);
                if (chans[ci][0] === 'email') used++;
                okAny = true;
              } catch (e2) {
                errs.push(chans[ci][0] + ': ' + String(e2.message || e2).slice(0, 90));
              }
            }
            if (okAny) { patch.dc_notified_at = istStamp(Date.now() / 1000); dcSent++; }
            patch.dc_notify_error = errs.join(' | ');
          }
        }
      }

      if (Object.keys(patch).length) patches.push({ row: row, patch: patch });
    }

    patches.forEach(function (p) { writeDesk_(p.row, p.patch); });
    stateSetAll_({ 'notify:last_at': istStamp(Date.now() / 1000),
                   'notify:last_sent': sent, 'notify:last_failed': failed,
                   'notify:dc_sent': dcSent, 'notify:dc_skipped': dcSkipped });
    SpreadsheetApp.flush();
    return { sent: sent, failed: failed, skipped: skipped, dc_sent: dcSent,
             dc_skipped: dcSkipped, mode: mode, recipients_left: quota - used };
  } finally {
    lock.releaseLock();
  }
}

function statusWord_(t) {
  return { NEW: 'Received', IDENTIFIED: 'Received', OPEN: 'Being looked at',
           WORKING: 'Being worked on', RESOLVED: 'Resolved',
           CLOSED: 'Closed' }[effectiveState_(t)] || 'Received';
}

function trackLink_(t, to) {
  // Only Meesho addresses get the link today, because the web app is domain-restricted and a
  // partner following it would hit a sign-in they can never pass. Delete the domain test when
  // access is widened to Anyone; nothing else changes.
  var url = prop_('WEBAPP_URL', '');
  if (!url) return '';
  return /@meesho\.com$/i.test(to) ? url + '?t=' + encodeURIComponent(t.public_token) : '';
}

function htmlShell_(intro, pairs, link, footer) {
  var esc = function (s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  };
  return '<div style="font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;' +
    'font-size:14px;line-height:1.55;color:#1a1a1a;max-width:560px">' +
    '<p>' + esc(intro) + '</p><table style="border-collapse:collapse;margin:16px 0">' +
    pairs.map(function (p) {
      return '<tr><td style="padding:3px 16px 3px 0;color:#666">' + esc(p[0]) + '</td>' +
             '<td style="padding:3px 0"><b>' + esc(p[1]) + '</b></td></tr>';
    }).join('') + '</table>' +
    (link ? '<p><a href="' + esc(link) + '" style="background:#1a1a1a;color:#fff;padding:9px 16px;' +
            'border-radius:6px;text-decoration:none;display:inline-block">Track this issue</a></p>' : '') +
    '<p style="color:#666;font-size:13px">' + esc(footer) + '</p></div>';
}

/** To the person who raised it. */
function raiserBody_(t, to) {
  var ref = String(t.idempotency_key || '').slice(0, 8).toUpperCase();
  var category = t.intent && t.intent !== 'NOVEL'
    ? String(t.intent).replace(/_/g, ' ') : 'being categorised';
  var pairs = [['Reference', ref], ['Issue', String(t.title || '').slice(0, 160)],
               ['Category', category], ['Status', statusWord_(t)]];
  if (t.dc_code) pairs.splice(2, 0, ['Delivery centre', String(t.dc_code)]);
  var link = trackLink_(t, to);
  var foot = 'You do not need to reply to this message. If the details above are wrong, say so ' +
             'in the same channel where you raised it and the correction will be picked up.';
  var text = ['We have your issue and it is in the queue.', ''].concat(
    pairs.map(function (p) { return p[0] + ': ' + p[1]; }),
    [''], link ? ['Track it here: ' + link, ''] : [], [foot]).join('\n');
  return { subject: '[' + ref + '] ' + String(t.title || 'Your issue').slice(0, 90),
           text: text, html: htmlShell_('We have your issue and it is in the queue.', pairs, link, foot) };
}

/** To the delivery centre. Different opening, because they did not raise it — somebody raised
 *  it about them, and a message that says "we have your issue" reads as a mistake. */
function dcBody_(t, c) {
  var ref = String(t.idempotency_key || '').slice(0, 8).toUpperCase();
  var category = t.intent && t.intent !== 'NOVEL'
    ? String(t.intent).replace(/_/g, ' ') : 'being categorised';
  var intro = 'An issue was raised for ' + c.name + ' and the support team is on it.';
  var pairs = [['Reference', ref], ['Centre', c.code + ' — ' + c.name],
               ['Issue', String(t.title || '').slice(0, 160)], ['Category', category],
               ['Status', statusWord_(t)], ['Raised by', String(t.raiser || 'Meesho team')]];
  var foot = 'No action is needed from you right now. The team handling it will be in touch if ' +
             'they need anything from the centre.';
  var text = [intro, ''].concat(pairs.map(function (p) { return p[0] + ': ' + p[1]; }),
                                ['', foot]).join('\n');
  return { subject: '[' + ref + '] Issue raised for ' + c.code, text: text,
           html: htmlShell_(intro, pairs, '', foot) };
}
