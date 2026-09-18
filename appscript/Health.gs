/**
 * Health.gs — the three things that must be true, checked the same way every time.
 *
 *     INTENT DETECTION · POLLING · ACKNOWLEDGEMENT
 *
 * Each of these has failed silently at least once. Classification was off for a day because an
 * index upload reported success and did not persist. Polling never started because one setting
 * was missing. Acknowledgement reported "sent" while every message died in TLS. In all three
 * the symptom was identical: nothing new appeared, and nothing said why.
 *
 * So this asks the system directly and prints a REASON for every "no". It reads only; it
 * creates nothing and sends nothing.
 *
 * ── A WARNING IS NOT AN OK ──────────────────────────────────────────────────────────────────
 * The previous version of this check printed "ALL THREE OK" while four acknowledgements were
 * failing, because it counted only hard failures. Warnings are counted separately here and the
 * verdict says so. A check that rounds problems down to green is worse than no check.
 */

function healthSummary_() {
  var out = [];
  var now = Date.now() / 1000;

  // ── 1. intent detection ───────────────────────────────────────────────────────────────────
  var exRows = 0;
  var exSh = ss_().getSheetByName(CFG().tabs.exemplars);
  if (exSh) exRows = Math.max(0, exSh.getLastRow() - 1);
  var classified = String(stateGet_('pipe:classified', ''));
  if (!exRows) {
    out.push({ label: 'INTENT', ok: false, warn: false,
               detail: 'no exemplars — import seed/_exemplars.csv into the ' +
                       CFG().tabs.exemplars + ' tab' });
  } else if (classified.indexOf('SKIPPED') === 0) {
    out.push({ label: 'INTENT', ok: false, warn: false,
               detail: exRows + ' exemplars but the last run did not classify: ' + classified });
  } else if (!classified) {
    out.push({ label: 'INTENT', ok: false, warn: true,
               detail: exRows + ' exemplars loaded, no pipeline run has completed yet' });
  } else {
    out.push({ label: 'INTENT', ok: true, warn: false, detail: exRows + ' exemplars · ' + classified });
  }

  // ── 2. polling ────────────────────────────────────────────────────────────────────────────
  var lastPoll = String(stateGet_('poll:last_at', ''));
  var chans = readTabObjects_(CFG().tabs.channels);
  var errs = chans.filter(function (c) { return c.last_error; });
  if (!channelIds_().length) {
    out.push({ label: 'POLLING', ok: false, warn: false,
               detail: 'Script Property CHANNELS is empty' });
  } else if (!lastPoll) {
    out.push({ label: 'POLLING', ok: false, warn: false,
               detail: 'no poll has ever completed — run setup(), then pollSlack() once by hand' });
  } else if (ageMinutes_(lastPoll) > 15) {
    out.push({ label: 'POLLING', ok: false, warn: false,
               detail: 'last poll was ' + Math.round(ageMinutes_(lastPoll)) +
                       ' minutes ago — check the trigger is installed' });
  } else if (errs.length) {
    out.push({ label: 'POLLING', ok: false, warn: true,
               detail: errs.length + ' channel(s) erroring: ' +
                       String(errs[0].last_error).slice(0, 80) });
  } else {
    out.push({ label: 'POLLING', ok: true, warn: false,
               detail: chans.length + ' channel(s) · last ' + lastPoll.slice(11, 19) });
  }

  // ── 3. acknowledgement ────────────────────────────────────────────────────────────────────
  var mode = notifyMode_();
  var tickets = readTabObjects_(CFG().tabs.tickets);
  var live = tickets.filter(function (t) {
    return !(t.suppressed === true || String(t.suppressed).toLowerCase() === 'true');
  });
  var sent = live.filter(function (t) { return t.acknowledged_at; });
  var failing = live.filter(function (t) { return t.acknowledge_error && !t.acknowledged_at; });

  if (mode === 'off') {
    out.push({ label: 'ACK', ok: false, warn: false, detail: 'off (INTAKE_NOTIFY=off)' });
  } else if (mode === 'email-dry') {
    out.push({ label: 'ACK', ok: false, warn: true, detail: 'DRY RUN — nothing is being sent' });
  } else if (failing.length && !sent.length) {
    out.push({ label: 'ACK', ok: false, warn: false,
               detail: 'every send failed: ' + String(failing[0].acknowledge_error).slice(0, 70) });
  } else if (failing.length) {
    out.push({ label: 'ACK', ok: false, warn: true,
               detail: sent.length + ' sent, ' + failing.length + ' failing: ' +
                       String(failing[0].acknowledge_error).slice(0, 60) });
  } else if (!live.length) {
    out.push({ label: 'ACK', ok: true, warn: false, detail: 'on, no tickets to acknowledge yet' });
  } else if (!sent.length) {
    out.push({ label: 'ACK', ok: false, warn: true, detail: 'on, but nothing acknowledged yet' });
  } else {
    out.push({ label: 'ACK', ok: true, warn: false,
               detail: sent.length + ' of ' + live.length + ' acknowledged' });
  }
  return out;
}

function ageMinutes_(istIso) {
  try {
    var t = Date.parse(String(istIso).replace('+05:30', '+05:30'));
    if (isNaN(t)) return 1e9;
    return (Date.now() - t) / 60000;
  } catch (e) { return 1e9; }
}

/** Run from the editor. Prints the three, with a reason for every "no". */
function healthCheck() {
  var rows = healthSummary_();
  var failed = 0, warned = 0, lines = [];
  rows.forEach(function (r) {
    var mark = r.ok ? '  ok  ' : (r.warn ? '  !!  ' : '  XX  ');
    if (!r.ok) { if (r.warn) warned++; else failed++; }
    lines.push(mark + ' ' + r.label + ' — ' + r.detail);
  });

  var sh = ss_().getSheetByName(CFG().tabs.tickets);
  var n = sh ? Math.max(0, sh.getLastRow() - 1) : 0;
  var verdict = failed ? (failed + ' BROKEN')
              : warned ? (warned + ' needs attention')
              : 'ALL THREE OK';
  lines.push('', '       tickets: ' + n + '   ' + verdict);

  var last = String(stateGet_('pipe:last_ms', ''));
  if (last) lines.push('       last pipeline run: ' + last + ' ms (6-minute cap is 360,000)');

  var out = lines.join('\n');
  Logger.log(out);
  return out;
}
