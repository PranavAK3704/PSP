/**
 * Config.gs — settings, secrets, tab plumbing, and the four primitives everything else needs.
 *
 * ── PASTE THIS FILE FIRST ───────────────────────────────────────────────────────────────────
 * Apps Script puts every .gs file into ONE global scope and runs top-level statements in
 * editor file order — an order you can change by dragging files in the sidebar. So a top-level
 * `const` here that another file's top-level `const` reads is a bug waiting for someone to
 * reorder the list. Nothing in this project has cross-file top-level state: every shared value
 * comes from a function (CFG(), LEX(), DCREG()) that builds on first call and memoises into a
 * `_cache` object. Order then cannot matter, because nothing runs until something calls it.
 *
 * ── SECRETS LIVE IN SCRIPT PROPERTIES, NOT HERE ─────────────────────────────────────────────
 * This file is committed. Project Settings → Script Properties is not. Four keys:
 *
 *   SLACK_BOT_TOKEN   xoxb-…    the read-only bot token
 *   CHANNELS          C123,C456 comma-separated channel ids to listen on
 *   INTAKE_NOTIFY     off | email-dry | email
 *   INTAKE_SECRET     any long random string — signs the partner status-page tokens.
 *                     SET IT ONCE AND NEVER CHANGE IT: public_token is derived from it, so
 *                     editing it silently kills every status link already emailed. healthCheck()
 *                     detects the change and says so, but cannot undo it.
 *
 * A missing SLACK_BOT_TOKEN throws by name rather than failing as a 401 twenty lines later.
 */

var _cache = {};   // memo store for CFG / LEX / DCREG / exemplar index — per execution only


// ── settings ────────────────────────────────────────────────────────────────────────────────

/** Every tunable in one place. Numbers carry the comment explaining why they are that number. */
function CFG() {
  if (_cache.cfg) return _cache.cfg;
  _cache.cfg = {

    tabs: {
      raw:       'raw_messages',
      issues:    'issues',
      tickets:   'tickets',
      channels:  'channels',
      state:     '_state',
      dcCodes:   '_dc_codes',
      dcDeny:    '_dc_denylist',
      exemplars: '_exemplars',
      agents:    '_agents',
      contacts:  '_dc_contacts',
      questions: '_questions',
      llmCache:  '_llm_cache'
    },

    // A UI write waits this long for the script lock before giving up. The pipeline can hold it
    // for tens of seconds on a big batch, and an agent would rather wait than see their click
    // silently do nothing — which is exactly what happened before any of these paths locked.
    uiLockMs: 20000,

    // The queue list is capped. getQueue used to return every ticket ever written, with full
    // descriptions, to every agent every refresh — several MB per agent per minute by month two.
    queueLimit: 400,

    // Slack read pacing. 0.2s between calls keeps us far under the tier-3 ~50/min limit while
    // leaving room inside the 6-minute execution cap.
    slack: {
      pauseMs:      200,
      pageLimit:    200,
      maxPages:     20,     // hard cap: 4,000 messages per channel per run
      coldStartDays: 7,     // first-ever poll of a channel reaches back this far

      // How far back sweepThreadReplies looks for threads that may have gained a reply since
      // they were last read. See the note in SlackParser.gs for why a separate sweep exists.
      sweepLookbackDays: 3,

      // Hard bounds on one sweep. 120 threads x 48 runs/day = 5,760 UrlFetch calls against a
      // 100,000/day allowance, and 150s leaves the 6-minute cap a wide margin to append in.
      sweepMaxThreads: 120,
      sweepDeadlineMs: 150000,

      // How many trailing rows of raw_messages are read to drop already-ingested messages.
      // This MUST comfortably exceed the number of rows your channels produce in
      // sweepLookbackDays, or the sweep can re-append a reply it already has. At ~3,000
      // messages/day this covers over a week. If your volume grows past ~8,000/day, raise it.
      dedupeTailRows: 25000
    },

    // evidence.yaml thresholds/weights. request=0.4 so ops_noun(0.6)+request lands exactly on
    // the 1.0 issue threshold — "can we get the payout released?" must qualify.
    evidence: {
      issue: 1.0, weak: 0.5,
      w: { identifier: 1.0, opsNoun: 0.6, problem: 0.5, request: 0.4, urgency: 0.15 }
    },

    // grouping.yaml. A ticket number or a person stays the same issue for 180 days; one
    // shipment is done in 14. The LONGEST window among shared kinds wins.
    grouping: {
      defaultWindowS: 7 * 86400,
      windows: { kapture_id: 180 * 86400, mobile: 180 * 86400, pilot_id: 180 * 86400,
                 email: 180 * 86400, waybill: 14 * 86400 },
      joinOn:     ['kapture_id', 'waybill', 'mobile', 'pilot_id', 'email'],
      joinOnWeak: ['dc_code'],
      // dc_code identifies a PLACE, not an incident — joining on it merges every issue a hub
      // raised in a week into whichever came first. Off until measured.
      joinWeak: false,
      conf: { threadRef: 1.0, entityJoinStrong: 0.75, entityJoinWeak: 0.35, unassigned: 0.0 },
      adjudicateBelow: 0.50
    },

    // dedupe.py. 0.95 same-channel because two DIFFERENT problems from one person in one hour
    // is ordinary; near-verbatim is not ambiguous, merely similar is.
    dedupe: { windowS: 3600, textSimilarity: 0.72, sameChannelSimilarity: 0.95 },

    // classify.yaml. minScore does not bind on this corpus — the margin does all the work — but
    // it is the right guard for a new disposition with two examples.
    classify: { minScore: 3.0, minMargin: 0.15, topK: 5, goldWeight: 3.0, k1: 1.2, b: 0.6 },

    // Bounds on one pipeline run, so a backlog is worked off in slices instead of one
    // execution hitting the 6-minute cap and dying with nothing written. A run that stops at
    // the cap has already advanced nothing; the next trigger simply picks up where it left off.
    maxMessagesPerRun: 4000,

    // Trailing rows of the issues tab read to find open issues. Must exceed the number of
    // issues created inside the longest grouping window (180 days as shipped).
    issueTailRows: 30000,

    // ── the model tier ───────────────────────────────────────────────────────────────────
    // Asked ONLY about messages BM25 could not place, so cost scales with uncertainty rather
    // than with volume, and every answer is cached on the message hash forever.
    //
    // claude-opus-5 because that is the right default and downgrading for cost is your call,
    // not mine: `claude-haiku-4-5` is roughly a fifth the price and this is a short
    // classification, so it is a reasonable switch to make deliberately after you have seen
    // both on real traffic. One line either way.
    llm: {
      enabled: true,                 // no ANTHROPIC_KEY means no calls regardless
      model: 'claude-opus-5',
      // 20 x ~2s keeps a pipeline run far inside the 6-minute cap even in the worst case.
      maxCallsPerRun: 20,
      // A ceiling a runaway loop cannot climb over. Raise it once you know the real rate.
      maxCallsPerDay: 500,
      // Below this the model's own answer is treated as a hedge, not a category — which is
      // the whole point of asking it for a confidence at all.
      trustConfidence: 0.70
    },

    // ── how well did we understand the message? ──────────────────────────────────────────
    // Two independent things have to land for a ticket to be actionable: WHO/WHERE (an
    // identifier somebody can look up) and WHAT (a category the desk routes on). Either can
    // fail on its own, so they are scored separately and the weaker one decides.
    //
    // This is the seam a better model slots into later. Replace identificationBand() and
    // nothing else in the project has to change — the asking, the copy and the UI all read
    // the band, never the internals.
    identify: {
      // A category is only trusted when the classifier separated the top two by at least this
      // much. Below it, do not ask category-specific questions — a wrong category asks the
      // wrong thing of the one person who was trying to help.
      trustCategoryMargin: 0.30,
      // Ask at most this many. A message with six questions in it does not get answered.
      maxQuestions: 3
    },

    // ── filing into Kapture ──────────────────────────────────────────────────────────────
    // Kapture is the CRM the support agents are already onboarded to: an email to its intake
    // address becomes a ticket assigned to a real person. So this stops being a ticketing
    // system and becomes a triage layer that feeds one — which is also the right shape given
    // that Kapture is going to be replaced. The Sheet stays the system of record; Kapture is
    // a sink. When it goes, one function goes with it.
    kapture: {
      // OFF by default and it should stay off until you have watched a week of what this would
      // have filed. Every false positive becomes a real ticket a real agent has to work and
      // close. You can loosen this later; you cannot un-spam a shared queue, and you get about
      // one chance at the desk's goodwill.
      autoFile: false,

      // Even with autoFile on, never file something the classifier would not commit to. A
      // ticket with no category lands in Kapture as "miscellaneous" and rots.
      autoFileNeedsCategory: true,
      // ...nor one with nothing anybody could act on.
      autoFileNeedsIdentifier: true
    },

    // ── telling the delivery centre ──────────────────────────────────────────────────────
    // Meesho AMs raise tickets on behalf of DCs, who are not Meesho employees and today hear
    // nothing. Email ships with no approvals. SMS needs DLT registration under Meesho's own
    // principal entity, and the template MUST be registered Service-Implicit — Promotional is
    // DND-scrubbed and only delivered 9am-9pm, while DC problems get raised at 2am. WhatsApp
    // needs a template approved on Meesho's Business account, and Meta starts charging for
    // service and in-window utility messages from 1 October 2026.
    //
    // So email is on and the other two are adapters waiting for someone else's paperwork.
    dcNotify: { enabled: true, email: true, whatsapp: false, sms: false },

    // Suppress a ticket with no DC code, mobile, waybill or ticket id? Depends on how the desk
    // works. Off means such tickets are created and flagged rather than withheld.
    requireIdentifier: false,

    // Measured at +17.9 precision points for zero coverage cost — the largest single lever we
    // have. Off because collapsing five dispositions into one changes how the desk routes work,
    // and that is a desk decision, not a code decision.
    mergeMoneyClasses: false,
    moneyClasses: ['payment_not_received', 'payment_reconciliation', 'cod_shortfall',
                   'cod_pendency', 'consumables_payment'],
    moneyMergedName: 'money',

    // ── THE HARD WALL ────────────────────────────────────────────────────────────────────
    // A Google Sheet holds 10,000,000 cells WORKBOOK-WIDE, across every tab. raw_messages has
    // 25 columns, so it alone tops out near 400,000 rows — and the other tabs are drawing on
    // the same budget. At 100k messages/month that is about four months; at 1M/month it is
    // twelve days. Rotation is not a nicety at the top of that range, it is the difference
    // between a working system and a parser that throws on every run with the day's messages
    // already aged out of Slack's cheap window.
    rawRowWarn:   260000,
    rawRowRotate: 340000,
    archiveKeepMonths: 3
  };
  return _cache.cfg;
}

/**
 * Plain language for each category, and where it actually goes.
 *
 * ── WHY THIS EXISTS ─────────────────────────────────────────────────────────────────────────
 * `cod_pendency` means nothing to a support agent on their first week. Worse, the machine-facing
 * name hides the only thing they need to decide between two candidates: what each one MEANS and
 * which desk it lands on. The team names are taken from the ops-authored SOPs' own escalation
 * field, not invented here.
 *
 * Anything not in this table falls back to a prettified version of its own name, so a category
 * a human invents in the UI still reads sensibly without a code change.
 */
function DISPOSITIONS() {
  if (_cache.disp) return _cache.disp;
  _cache.disp = {
    payment_not_received:   ['Payment has not arrived',      'Money was due and has not landed.',                      'Cost Ops'],
    payment_reconciliation: ['Payment amount is wrong',      'The payout came, but the figure does not match.',        'Cost Ops'],
    cod_shortfall:          ['Cash handed in is short',      'Less COD reached us than was collected.',                'COD desk'],
    cod_pendency:           ['COD not handed over yet',      'Cash collected is still sitting with the partner.',      'Cash handover'],
    hardstop_loss:          ['Parcel written off as lost',   'A parcel was marked lost and charged to the partner.',   'Losses team'],
    shortage_loss:          ['Items missing from a bag',     'A bag arrived short and somebody is being charged.',     'Losses team'],
    qc_failure:             ['Failed a quality check',       'A parcel was rejected at a QC gate.',                    'SX claims'],
    load_planning:          ['Load or route problem',        'Vehicles, trips or capacity do not match the plan.',     'Planning team'],
    capacity_panel_issue:   ['Capacity panel is wrong',      'The panel shows wrong numbers or blocks a change.',      'Area Managers'],
    technical_issue:        ['Something in the app broke',   'A screen, a login or a scan is not working.',            'Tech'],
    consumables_order:      ['Supplies have not arrived',    'Bags, tape or labels were ordered and never came.',      'Vendors'],
    consumables_damaged:    ['Supplies arrived damaged',     'What was delivered cannot be used.',                     'Suppliers'],
    consumables_payment:    ['Charged wrongly for supplies', 'A deduction for consumables looks wrong.',               'Cost Ops'],
    invoice_request:        ['Needs an invoice or bill',     'A document is needed for GST or for records.',           'Area Managers']
  };
  return _cache.disp;
}

/** {label, meaning, team} for any category, invented ones included. */
function dispositionInfo(key) {
  var k = String(key || '');
  var d = DISPOSITIONS()[k];
  if (d) return { key: k, label: d[0], meaning: d[1], team: d[2] };
  var pretty = k.replace(/_/g, ' ').replace(/^./, function (c) { return c.toUpperCase(); });
  return { key: k, label: pretty || 'Uncategorised', meaning: '', team: '' };
}

function props_() { return PropertiesService.getScriptProperties(); }

/** A Script Property, or `dflt`. Empty string counts as absent — a cleared property is not a value. */
function prop_(key, dflt) {
  var v = props_().getProperty(key);
  if (v === null || v === undefined || String(v).trim() === '') return dflt;
  return String(v).trim();
}

/** The bot token. Throws by name — a 401 twenty lines downstream is a worse error message. */
function slackToken_() {
  var t = prop_('SLACK_BOT_TOKEN', '');
  if (!t) throw new Error('Script Property SLACK_BOT_TOKEN is not set. ' +
                          'Project Settings → Script Properties → Add script property.');
  return t;
}

/** Channel ids to listen on. This list REPLACES the Python channel_qualification stage: you are
 *  only in channels the bot was invited to, and scoring a list you wrote yourself is theatre. */
function channelIds_() {
  var raw = prop_('CHANNELS', '');
  if (!raw) return [];
  return raw.split(',').map(function (s) { return s.trim(); }).filter(function (s) { return s; });
}


// ── the four primitives ─────────────────────────────────────────────────────────────────────

/**
 * IST +05:30 to the second.
 *
 * Built from UTC plus a LITERAL offset, never from the 'Asia/Calcutta' timezone name — the two
 * disagree by a second on the boundary and the record then fails the field contract. This is
 * derived exactly the way tools/validate.js derives it, so the two cannot drift.
 */
function istStamp(epochSeconds) {
  return Utilities.formatDate(new Date((epochSeconds + 19800) * 1000), 'UTC',
                              "yyyy-MM-dd'T'HH:mm:ss") + '+05:30';
}

/**
 * sha256 of a string, as 64 lowercase hex characters.
 *
 * Three traps, all of them silent:
 *   1. Apps Script returns SIGNED bytes (-128..127). Without `& 0xFF` every high byte becomes
 *      a negative number and `toString(16)` yields something like "-6b".
 *   2. A byte below 0x10 renders as one character. Without the pad the digest is short and
 *      still looks like a hash.
 *   3. computeDigest must encode an embedded U+0000 as a single 0x00, not as Modified-UTF-8's
 *      0xC0 0x80. Tests.gs pins two golden vectors that fail loudly if it ever does the latter.
 */
function sha256Hex(s) {
  var bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, s,
                                      Utilities.Charset.UTF_8);
  var hex = '';
  for (var i = 0; i < bytes.length; i++) {
    var b = bytes[i] & 0xFF;
    hex += (b < 16 ? '0' : '') + b.toString(16);
  }
  return hex;
}

/**
 * A string as an array of CODE POINTS.
 *
 * Python iterates str by code point; JS iterates by UTF-16 code unit. An emoji is 1 element in
 * Python and 2 in JS, which changes both the length and the match count in the similarity
 * ratio — and Slack messages have emoji. Anything doing length arithmetic on message text goes
 * through here first.
 */
function cp_(s) { return Array.from(s || ''); }

/** Python's round-half-away-from-zero at n decimal places, for the stored score strings. */
function round_(x, n) {
  if (x === null || x === undefined) return null;
  var f = Math.pow(10, n);
  return Math.round(x * f) / f;
}


// ── sheet plumbing ──────────────────────────────────────────────────────────────────────────
//
// One rule, and every Sheets performance problem in this project is a violation of it:
// ONE getValues() and ONE setValues() per tab per run. Never per row. A 500-row per-row write
// is ~500 round trips and will eat the 6-minute cap on its own.

/**
 * Run `fn` holding the script lock, or throw something an agent can read.
 *
 * EVERY write path must go through this. Before it existed the five triggers locked against
 * each other and the web app locked against nothing, so an agent's edit landing between the
 * pipeline's read and its write was silently reverted — no error, no trace, just a green toast
 * and a change that quietly disappeared. With 20 agents that is a daily event, and it reads as
 * "the tool is lying to me".
 *
 * Apps Script locks are ADVISORY: they only work because every writer asks. A new write path
 * that forgets this reintroduces the bug in full.
 */
function withLock_(fn) {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(CFG().uiLockMs)) {
    throw new Error('The pipeline is busy writing \u2014 nothing was changed. Try again in a moment.');
  }
  try { return fn(); } finally { lock.releaseLock(); }
}

function ss_() { return SpreadsheetApp.getActiveSpreadsheet(); }

/** The tab, created with `header` if absent. */
/**
 * Widen a sheet so a range of `n` columns is addressable.
 *
 * A new sheet is created with 26 columns. getRange(1, 1, 1, 38) on it does not silently clip —
 * it THROWS, and a throw inside a time-driven trigger is invisible unless you open the
 * executions list. That is exactly how runPipeline died on every run while pollSlack carried on
 * happily: messages kept landing in raw_messages and nothing ever became a ticket.
 *
 * Headers grow as features land, so this is not a one-off — it has to be checked on every
 * access, not just at creation.
 */
function ensureCols_(sh, n) {
  var have = sh.getMaxColumns();
  if (have < n) sh.insertColumnsAfter(have, n - have);
  return sh;
}

function sheet_(name, header) {
  var sh = ss_().getSheetByName(name);
  if (!sh) {
    sh = ss_().insertSheet(name);
    if (header && header.length) {
      ensureCols_(sh, header.length);
      sh.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold');
      sh.setFrozenRows(1);
    }
  } else if (header && header.length) {
    ensureCols_(sh, header.length);        // the header grew since this tab was made
  }
  return sh;
}

/** Whole tab in one read: {header: [...], rows: [[...]]}. Empty tab gives empty arrays. */
function readTab_(name) {
  var sh = ss_().getSheetByName(name);
  if (!sh) return { header: [], rows: [] };
  var last = sh.getLastRow(), cols = sh.getLastColumn();
  if (last < 1 || cols < 1) return { header: [], rows: [] };
  var all = sh.getRange(1, 1, last, cols).getValues();
  return { header: all[0], rows: all.slice(1) };
}

/** Whole tab as objects keyed by header. Convenience over readTab_ — same single read. */
function readTabObjects_(name) {
  var t = readTab_(name), out = [];
  for (var i = 0; i < t.rows.length; i++) {
    var o = {};
    for (var j = 0; j < t.header.length; j++) o[t.header[j]] = t.rows[i][j];
    out.push(o);
  }
  return out;
}

/**
 * Rows `from`..`from+n-1` of a tab (1-based, EXCLUDING the header row).
 *
 * This is what makes the pipeline incremental: raw_messages is append-only, so "everything I
 * have not structured yet" is a contiguous block and costs one read regardless of how large
 * the tab has grown.
 */
function readTabSlice_(name, fromDataRow, count) {
  var sh = ss_().getSheetByName(name);
  if (!sh) return { header: [], rows: [] };
  var last = sh.getLastRow(), cols = Math.min(sh.getLastColumn(), sh.getMaxColumns());
  if (last < 2 || cols < 1) return { header: [], rows: [] };
  var header = sh.getRange(1, 1, 1, cols).getValues()[0];
  var startSheetRow = fromDataRow + 1;                       // +1 for the header
  if (startSheetRow > last) return { header: header, rows: [] };
  var n = Math.min(count === undefined ? last : count, last - startSheetRow + 1);
  if (n <= 0) return { header: header, rows: [] };
  return { header: header, rows: sh.getRange(startSheetRow, 1, n, cols).getValues() };
}

/** Append in one write. Rows are padded/truncated to the tab's column count. */
function appendRows_(name, header, rows) {
  if (!rows || !rows.length) return 0;
  var sh = sheet_(name, header);
  var cols = Math.max(sh.getLastColumn(), header ? header.length : 0);
  ensureCols_(sh, cols);
  var padded = rows.map(function (r) {
    var out = r.slice(0, cols);
    while (out.length < cols) out.push('');
    return out;
  });
  sh.getRange(sh.getLastRow() + 1, 1, padded.length, cols).setValues(padded);
  return padded.length;
}

/** Replace every data row in one write, keeping the header. */
function replaceRows_(name, header, rows) {
  var sh = sheet_(name, header);
  var cols = header.length;
  ensureCols_(sh, cols);
  if (sh.getLastRow() > 1) sh.getRange(2, 1, sh.getLastRow() - 1, sh.getMaxColumns()).clearContent();
  if (!rows || !rows.length) return 0;
  var padded = rows.map(function (r) {
    var out = r.slice(0, cols);
    while (out.length < cols) out.push('');
    return out;
  });
  sh.getRange(2, 1, padded.length, cols).setValues(padded);
  return padded.length;
}

/**
 * Write many individual rows in as few Sheets calls as possible.
 *
 * `updates` is [{row, values}]. Rows are sorted and CONSECUTIVE runs are written with a single
 * setValues, because a Sheets round trip costs 10-50ms regardless of how much it carries and
 * that overhead is what actually binds.
 *
 * Measured before this existed: one call per updated row. A run that touched 2,000 issues made
 * 2,012 round trips — 20 to 100 seconds of pure overhead — and it grew linearly, so ~10,000
 * updates would have exceeded the 6-minute execution cap outright. Updated issues are almost
 * always the recent ones, which sit next to each other at the tail of the tab, so in practice
 * this collapses to a handful of calls.
 */
function writeRowsBatched_(name, width, updates, firstCol) {
  if (!updates || !updates.length) return 0;
  var sh = ss_().getSheetByName(name);
  if (!sh) return 0;
  var col = firstCol || 1;
  ensureCols_(sh, col + width - 1);
  var sorted = updates.slice().sort(function (a, b) { return a.row - b.row; });

  var calls = 0, i = 0;
  while (i < sorted.length) {
    var start = i, block = [sorted[i].values];
    while (i + 1 < sorted.length && sorted[i + 1].row === sorted[i].row + 1) {
      i++; block.push(sorted[i].values);
    }
    sh.getRange(sorted[start].row, col, block.length, width).setValues(block);
    calls++;
    i++;
  }
  return calls;
}

/** A named value in the _state tab. Watermarks and counters live here, not in Script Properties,
 *  because Properties cap at 9KB per value and are invisible when you are staring at the sheet. */
function stateGet_(key, dflt) {
  var t = readTab_(CFG().tabs.state);
  for (var i = 0; i < t.rows.length; i++) {
    if (String(t.rows[i][0]) === String(key)) {
      var v = t.rows[i][1];
      return (v === '' || v === null || v === undefined) ? dflt : v;
    }
  }
  return dflt;
}

/** Set several state keys in one read and one write. */
function stateSetAll_(pairs) {
  var name = CFG().tabs.state, header = ['key', 'value', 'updated_at'];
  var t = readTab_(name);
  var idx = {}, rows = t.rows.slice();
  for (var i = 0; i < rows.length; i++) idx[String(rows[i][0])] = i;
  var now = istStamp(Date.now() / 1000);
  Object.keys(pairs).forEach(function (k) {
    if (idx.hasOwnProperty(k)) { rows[idx[k]][1] = pairs[k]; rows[idx[k]][2] = now; }
    else { rows.push([k, pairs[k], now]); }
  });
  replaceRows_(name, header, rows);
}

function stateSet_(key, value) { var p = {}; p[key] = value; stateSetAll_(p); }
