/**
 * Valmo intake — interim ticket sink, and the status page a raiser can actually open.
 *
 * Paste into script.google.com, bound to a Google Sheet. Deploy as a Web App.
 *
 *   POST  <url>                 the pipeline creates a ticket        (needs the shared secret)
 *   GET   <url>?t=<token>       one ticket's status, as a web page   (safe to send a partner)
 *   GET   <url>                 a health check; lists nothing
 *
 * ── IDEMPOTENCY IS THIS FILE'S JOB, AND IT IS NOT OPTIONAL ─────────────────────────────────
 * The pipeline sends an `idempotency_key` derived from the SOURCE MESSAGE — sha256 of
 * (source_system, channel_id, message_id). The same Slack message always produces the same key,
 * on every run, from every machine.
 *
 * A retry, a re-run, a second operator, or a network timeout that succeeded server-side will all
 * send the same key again. If this script appends blindly, the sheet grows duplicate tickets and
 * we have rebuilt — in a spreadsheet — precisely the problem the project exists to fix.
 *
 * So: look the key up FIRST, return the existing row if found, and only then append. The lock is
 * what makes that safe when two requests arrive at once.
 *
 * ── WHY THE STATUS PAGE NEEDS ITS OWN TOKEN ────────────────────────────────────────────────
 * An acknowledgement is worthless without somewhere to look. But the deployment URL is a WRITE
 * endpoint, so handing it to a partner would hand them the ability to create tickets — and a
 * row number like VAL-47 is trivially enumerable, so ?ref=VAL-48 would read somebody else's.
 *
 * Two separate fixes, both needed:
 *   1. POST requires a shared secret held in a Script Property. The URL alone cannot write.
 *   2. Every ticket gets an unguessable random token. The status page serves exactly the one
 *      ticket that token names — never a list, never a neighbour.
 *
 * ── DEPLOY SETTINGS THAT MATTER ────────────────────────────────────────────────────────────
 *   Execute as:      Me
 *   Who has access:  Anyone within <your org>
 *
 * Set the secret once, in the Apps Script editor:
 *   Project Settings -> Script Properties -> add  INTAKE_SECRET = <a long random string>
 * Put the same value in backend/data/appscript_secret.txt (mode 600, already gitignored).
 */

const SHEET_NAME = 'tickets';

const HEADERS = [
  'idempotency_key', 'created_at', 'source_system', 'source_id', 'permalink',
  'title', 'disposition', 'dc_code', 'raiser',
  'occurrence_count', 'first_raised_at', 'last_raised_at',
  'reply_count', 'first_response_s', 'state', 'flags', 'entities', 'description',
  'public_token', 'status_note', 'updated_at',
];

const COL = {};
HEADERS.forEach(function (h, i) { COL[h] = i; });   // 0-based index into a row array

function sheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sh = ss.getSheetByName(SHEET_NAME);
  if (!sh) {
    sh = ss.insertSheet(SHEET_NAME);
    sh.appendRow(HEADERS);
    sh.setFrozenRows(1);
  }
  return sh;
}

function secret_() {
  return PropertiesService.getScriptProperties().getProperty('INTAKE_SECRET') || '';
}

/** Row number for an existing key, or 0. Column A only — one read, not a full-sheet scan. */
function findKey_(sh, key) {
  const last = sh.getLastRow();
  if (last < 2) return 0;
  const col = sh.getRange(2, 1, last - 1, 1).getValues();
  for (let i = 0; i < col.length; i++) if (col[i][0] === key) return i + 2;
  return 0;
}

/** Row number for a public token, or 0. */
function findToken_(sh, token) {
  const last = sh.getLastRow();
  if (last < 2 || !token) return 0;
  const col = sh.getRange(2, COL.public_token + 1, last - 1, 1).getValues();
  for (let i = 0; i < col.length; i++) if (col[i][0] === token) return i + 2;
  return 0;
}

function json_(o) {
  return ContentService.createTextOutput(JSON.stringify(o))
      .setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  let d;
  try {
    d = JSON.parse(e.postData.contents);
  } catch (err) {
    return json_({ ok: false, error: 'bad_json' });
  }

  // The URL is not the credential. Someone who learns it still cannot write.
  const want = secret_();
  if (!want) return json_({ ok: false, error: 'server_not_configured: set INTAKE_SECRET' });
  if (d.secret !== want) return json_({ ok: false, error: 'bad_secret' });

  if (!d.idempotency_key) return json_({ ok: false, error: 'missing_idempotency_key' });

  // Serialised so two concurrent posts of the same key cannot both miss the lookup and append.
  const lock = LockService.getScriptLock();
  try {
    lock.waitLock(20000);
  } catch (err) {
    return json_({ ok: false, error: 'busy' });
  }

  try {
    const sh = sheet_();
    const existing = findKey_(sh, d.idempotency_key);
    if (existing) {
      // ALREADY CREATED. Return the original reference AND its original token — an
      // acknowledgement sent after a retry must point at the same page as the first one.
      const row = sh.getRange(existing, 1, 1, HEADERS.length).getValues()[0];
      return json_({ ok: true, ref: 'VAL-' + existing, row: existing, created: false,
                     token: row[COL.public_token] });
    }

    const token = Utilities.getUuid().replace(/-/g, '');
    const ents = d.entities || {};
    const now = new Date().toISOString();
    sh.appendRow([
      d.idempotency_key, now, d.source_system || '', d.source_id || '',
      d.source_permalink || '', d.title || '', d.disposition || '', d.dc_code || '',
      d.raiser || '', d.occurrence_count || 1, d.first_raised_at || '', d.last_raised_at || '',
      d.reply_count || 0,
      d.first_response_latency_s === null ? '' : d.first_response_latency_s,
      d.state || 'open', (d.flags || []).join('; '), JSON.stringify(ents),
      d.description || '', token, '', now,
    ]);
    const row = sh.getLastRow();
    return json_({ ok: true, ref: 'VAL-' + row, row: row, created: true, token: token });
  } finally {
    lock.releaseLock();
  }
}

/** Minimal HTML escape — ticket text is partner-written and goes straight into the page. */
function esc_(s) {
  return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
}

function statusPage_(row, rowNum) {
  const state = String(row[COL.state] || 'open').toLowerCase();
  const colour = state === 'resolved' ? '#1a7f37'
               : state === 'in_progress' ? '#9a6700' : '#0969da';
  const label = state === 'resolved' ? 'Resolved'
              : state === 'in_progress' ? 'Being worked on' : 'Received';
  const occ = Number(row[COL.occurrence_count] || 1);

  const html =
    '<!doctype html><html><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<title>Ticket VAL-' + rowNum + '</title><style>' +
    'body{font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;' +
    'margin:0;background:#f6f8fa;color:#1f2328}' +
    '.w{max-width:620px;margin:0 auto;padding:28px 18px}' +
    '.c{background:#fff;border:1px solid #d1d9e0;border-radius:12px;padding:22px 24px}' +
    '.ref{font:600 13px ui-monospace,SFMono-Regular,Menlo,monospace;color:#59636e}' +
    'h1{font-size:19px;margin:6px 0 14px;line-height:1.35}' +
    '.pill{display:inline-block;padding:4px 12px;border-radius:999px;color:#fff;' +
    'font-size:12.5px;font-weight:600;background:' + colour + '}' +
    'table{border-collapse:collapse;width:100%;margin-top:18px;font-size:13.5px}' +
    'td{padding:7px 0;border-top:1px solid #eaeef2;vertical-align:top}' +
    'td:first-child{color:#59636e;width:40%}' +
    '.note{margin-top:16px;padding:12px 14px;background:#f6f8fa;border-radius:8px;' +
    'font-size:13.5px}' +
    '.f{margin-top:16px;font-size:12px;color:#59636e}' +
    '</style></head><body><div class="w"><div class="c">' +
    '<div class="ref">VAL-' + rowNum + '</div>' +
    '<h1>' + esc_(row[COL.title]) + '</h1>' +
    '<span class="pill">' + label + '</span>' +
    '<table>' +
    '<tr><td>Raised by</td><td>' + esc_(row[COL.raiser]) + '</td></tr>' +
    '<tr><td>First raised</td><td>' + esc_(String(row[COL.first_raised_at]).slice(0, 16)) +
      '</td></tr>' +
    (occ > 1 ? '<tr><td>Times raised</td><td><b>' + occ + '</b> — counted, not duplicated</td></tr>'
             : '') +
    (row[COL.dc_code] ? '<tr><td>DC</td><td>' + esc_(row[COL.dc_code]) + '</td></tr>' : '') +
    '<tr><td>Category</td><td>' + esc_(row[COL.disposition] || 'being categorised') +
      '</td></tr>' +
    '<tr><td>Last updated</td><td>' + esc_(String(row[COL.updated_at]).slice(0, 16)) +
      '</td></tr>' +
    '</table>' +
    (row[COL.status_note]
      ? '<div class="note">' + esc_(row[COL.status_note]) + '</div>'
      : '<div class="note">Your message was picked up automatically and a ticket was created. ' +
        'This page updates as the ticket moves.</div>') +
    '<div class="f">Keep this link to check back. It shows only this ticket.</div>' +
    '</div></div></body></html>';

  return HtmlService.createHtmlOutput(html)
      .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function doGet(e) {
  const token = e && e.parameter ? e.parameter.t : null;

  // No token: a health check only. It must never list tickets — this URL is shared around.
  if (!token) {
    return json_({ ok: true, sheet: SHEET_NAME,
                   rows: Math.max(0, sheet_().getLastRow() - 1),
                   configured: !!secret_() });
  }

  const sh = sheet_();
  const rowNum = findToken_(sh, token);
  if (!rowNum) {
    return HtmlService.createHtmlOutput(
        '<body style="font:15px -apple-system,sans-serif;padding:36px;color:#1f2328">' +
        '<p>No ticket matches this link.</p>' +
        '<p style="color:#59636e;font-size:13.5px">It may have been mistyped. ' +
        'Raising the issue again in the channel will create a fresh one.</p></body>');
  }
  return statusPage_(sh.getRange(rowNum, 1, 1, HEADERS.length).getValues()[0], rowNum);
}
