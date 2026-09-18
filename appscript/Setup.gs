/**
 * Setup.gs — one-time install, the triggers, and the daily maintenance that keeps the
 * spreadsheet inside its cell budget.
 *
 * ── RUN setup() ONCE, FROM THE EDITOR ───────────────────────────────────────────────────────
 * It creates every tab with its header, hides the reference tabs, and installs the triggers.
 * It is safe to run again: tabs that exist are left alone and triggers are replaced rather
 * than duplicated, which matters because a double-installed 1-minute trigger quietly doubles
 * your runtime bill.
 *
 * ── THE CELL BUDGET IS THE REAL LIMIT, NOT THE ROW COUNT ────────────────────────────────────
 * A Google Sheet holds 10,000,000 cells across ALL tabs. Three tabs grow with traffic:
 *
 *     raw_messages   25 cols × every message
 *     issues         24 cols × ~44% of messages   (52 messages became 23 issues in the demo)
 *     tickets        30 cols × ~26% of messages
 *
 * which is about 136 cells per message once the 180-day grouping window is full. Against an
 * 8,000,000-cell working budget that is roughly 59,000 messages per month in steady state.
 *
 * Archiving to another TAB does not help — an archive tab spends the same budget. So
 * dailyMaintenance() writes old rows out to CSV files in Drive and deletes them from the
 * sheet, which is the only move that actually returns cells.
 */

function setup() {
  var C = CFG();
  sheet_(C.tabs.raw, RAW_HEADER);
  sheet_(C.tabs.issues, ISSUE_HEADER);
  sheet_(C.tabs.tickets, TICKET_HEADER);
  sheet_(C.tabs.channels, CHANNEL_HEADER);
  sheet_(C.tabs.state, ['key', 'value', 'updated_at']);
  sheet_(C.tabs.dcCodes, ['code']);
  sheet_(C.tabs.dcDeny, ['token']);
  sheet_(C.tabs.exemplars, ['id', 'disposition', 'label_provenance', 'text']);

  [C.tabs.state, C.tabs.dcCodes, C.tabs.dcDeny, C.tabs.exemplars].forEach(function (n) {
    var sh = ss_().getSheetByName(n);
    if (sh) sh.hideSheet();
  });

  installTriggers();

  var missing = ['SLACK_BOT_TOKEN', 'CHANNELS', 'INTAKE_NOTIFY', 'INTAKE_SECRET']
    .filter(function (k) { return !prop_(k, ''); });

  var msg = 'Tabs created and triggers installed.\n\n';
  msg += missing.length
    ? 'STILL TO DO — Project Settings → Script Properties:\n  ' + missing.join('\n  ')
    : 'All four Script Properties are set.';
  msg += '\n\nThen import the three seed CSVs (File → Import → Upload) and run runAllTests().';
  Logger.log(msg);
  return msg;
}

/** Replace this script's triggers. Replacing rather than adding is deliberate — duplicate
 *  1-minute triggers are invisible in the editor and double the daily runtime bill. */
function installTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });

  // Cheap and frequent: a handful of API calls, so a message is in the sheet within a minute.
  ScriptApp.newTrigger('pollSlack').timeBased().everyMinutes(1).create();
  // The expensive one. Every 5 minutes keeps it inside the daily runtime budget with room to
  // spare, and five minutes from message to ticket is well inside what anyone notices.
  ScriptApp.newTrigger('runPipeline').timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('sendAcknowledgements').timeBased().everyMinutes(10).create();
  // Late replies to older threads — see the note in SlackParser.gs for why this is separate.
  ScriptApp.newTrigger('sweepThreadReplies').timeBased().everyMinutes(30).create();
  ScriptApp.newTrigger('dailyMaintenance').timeBased().everyDays(1).atHour(3).create();

  return ScriptApp.getProjectTriggers().length + ' triggers installed';
}

/** Stop everything. Useful when debugging, and the honest way to pause rather than letting a
 *  broken run fire every minute for a day. */
function removeTriggers() {
  var n = ScriptApp.getProjectTriggers().length;
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  return n + ' triggers removed';
}

/** Archive the oldest raw_messages rows out to Drive and delete them from the sheet. */
function dailyMaintenance() {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(0)) return { skipped: 'another run holds the lock' };
  try {
    var C = CFG();
    var sh = ss_().getSheetByName(C.tabs.raw);
    if (!sh) return { archived: 0 };
    var dataRows = Math.max(0, sh.getLastRow() - 1);
    if (dataRows <= C.rawRowRotate) {
      stateSetAll_({ 'maint:last_at': istStamp(Date.now() / 1000),
                     'maint:raw_rows': dataRows, 'maint:archived': 0 });
      return { archived: 0, raw_rows: dataRows, capacity_pct: Math.round(100 * dataRows / C.rawRowRotate) };
    }

    // Keep a working window; everything older goes to Drive. The window must stay larger than
    // the pipeline's unprocessed backlog, or rows would be archived before being structured.
    var keep = Math.max(C.rawRowWarn, Number(stateGet_('pipe:last_row', 0)) || 0);
    var drop = dataRows - keep;
    if (drop <= 0) return { archived: 0, raw_rows: dataRows };

    var vals = sh.getRange(2, 1, drop, RAW_HEADER.length).getValues();
    var csv = [RAW_HEADER].concat(vals).map(function (r) {
      return r.map(function (c) {
        var s = String(c === null || c === undefined ? '' : c);
        return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
      }).join(',');
    }).join('\n');

    var folder = archiveFolder_();
    var name = 'raw_messages_' + Utilities.formatDate(new Date(), 'UTC', 'yyyyMMdd_HHmmss') +
               '_' + drop + 'rows.csv';
    folder.createFile(name, csv, MimeType.CSV);

    sh.deleteRows(2, drop);
    // Every row index shifted down by `drop`, so the pipeline's row watermark must shift too
    // or it would skip exactly that many unprocessed messages.
    var wm = Number(stateGet_('pipe:last_row', 0)) || 0;
    stateSetAll_({ 'pipe:last_row': Math.max(0, wm - drop),
                   'maint:last_at': istStamp(Date.now() / 1000),
                   'maint:archived': drop, 'maint:archive_file': name,
                   'maint:raw_rows': dataRows - drop });
    SpreadsheetApp.flush();
    return { archived: drop, file: name, raw_rows: dataRows - drop };
  } finally {
    lock.releaseLock();
  }
}

function archiveFolder_() {
  var name = 'Intake archive';
  var it = DriveApp.getFoldersByName(name);
  return it.hasNext() ? it.next() : DriveApp.createFolder(name);
}

/** How close the workbook is to the 10M-cell wall, and how long the current rate gives you. */
function capacityReport() {
  var C = CFG(), total = 0, lines = [];
  [[C.tabs.raw, RAW_HEADER.length], [C.tabs.issues, ISSUE_HEADER.length],
   [C.tabs.tickets, TICKET_HEADER.length], [C.tabs.exemplars, 4],
   [C.tabs.dcCodes, 1], [C.tabs.dcDeny, 1]].forEach(function (p) {
    var sh = ss_().getSheetByName(p[0]);
    var rows = sh ? Math.max(0, sh.getLastRow() - 1) : 0;
    var cells = rows * p[1];
    total += cells;
    lines.push('  ' + p[0] + ': ' + rows + ' rows, ' + cells.toLocaleString() + ' cells');
  });
  var pct = (100 * total / 10000000).toFixed(1);
  lines.unshift('Workbook: ' + total.toLocaleString() + ' of 10,000,000 cells (' + pct + '%)');
  lines.push('', 'Steady state is about 136 cells per message with the 180-day grouping window,',
             'so roughly 59,000 messages/month fits. Shorten CFG().grouping.windows to raise it.');
  var out = lines.join('\n');
  Logger.log(out);
  return out;
}
