/**
 * Valmo intake — interim ticket sink.
 *
 * Paste into script.google.com, bound to a Google Sheet. Deploy as a Web App.
 * The pipeline POSTs one ticket draft here and this appends a row.
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
 * ── DEPLOY SETTINGS THAT MATTER ────────────────────────────────────────────────────────────
 *   Execute as:      Me
 *   Who has access:  Anyone within <your org>     <-- NOT "Anyone". This URL is a write endpoint.
 *
 * The deployment URL is a credential. Put it in backend/data/appscript_url.txt (mode 600,
 * already gitignored) and never paste it into a chat or a commit.
 */

const SHEET_NAME = 'tickets';

const HEADERS = [
  'idempotency_key', 'created_at', 'source_system', 'source_id', 'permalink',
  'title', 'disposition', 'dc_code', 'raiser',
  'occurrence_count', 'first_raised_at', 'last_raised_at',
  'reply_count', 'first_response_s', 'state', 'flags', 'entities', 'description',
];

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

/** Row number for an existing key, or 0. Column A only — one read, not a full-sheet scan. */
function findKey_(sh, key) {
  const last = sh.getLastRow();
  if (last < 2) return 0;
  const col = sh.getRange(2, 1, last - 1, 1).getValues();
  for (let i = 0; i < col.length; i++) if (col[i][0] === key) return i + 2;
  return 0;
}

function doPost(e) {
  const out = (o) => ContentService.createTextOutput(JSON.stringify(o))
      .setMimeType(ContentService.MimeType.JSON);

  let d;
  try {
    d = JSON.parse(e.postData.contents);
  } catch (err) {
    return out({ ok: false, error: 'bad_json' });
  }
  if (!d.idempotency_key) return out({ ok: false, error: 'missing_idempotency_key' });

  // Serialised so two concurrent posts of the same key cannot both miss the lookup and append.
  const lock = LockService.getScriptLock();
  try {
    lock.waitLock(20000);
  } catch (err) {
    return out({ ok: false, error: 'busy' });
  }

  try {
    const sh = sheet_();
    const existing = findKey_(sh, d.idempotency_key);
    if (existing) {
      // ALREADY CREATED. Return the original reference — this is the whole point.
      return out({ ok: true, ref: 'VAL-' + existing, row: existing, created: false });
    }

    const ents = d.entities || {};
    sh.appendRow([
      d.idempotency_key,
      new Date().toISOString(),
      d.source_system || '',
      d.source_id || '',
      d.source_permalink || '',
      d.title || '',
      d.disposition || '',
      d.dc_code || '',
      d.raiser || '',
      d.occurrence_count || 1,
      d.first_raised_at || '',
      d.last_raised_at || '',
      d.reply_count || 0,
      d.first_response_latency_s === null ? '' : d.first_response_latency_s,
      d.state || '',
      (d.flags || []).join('; '),
      JSON.stringify(ents),
      d.description || '',
    ]);
    const row = sh.getLastRow();
    return out({ ok: true, ref: 'VAL-' + row, row: row, created: true });
  } finally {
    lock.releaseLock();
  }
}

/** Lets you confirm the deployment is live without writing anything. */
function doGet() {
  return ContentService.createTextOutput(JSON.stringify({
    ok: true, sheet: SHEET_NAME, rows: Math.max(0, sheet_().getLastRow() - 1),
  })).setMimeType(ContentService.MimeType.JSON);
}
