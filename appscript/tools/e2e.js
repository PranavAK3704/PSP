// e2e.js — end-to-end test: a fake Slack and an in-memory Sheet, running the real .gs files
// unmodified.
//
//     node appscript/tools/e2e.js
//
// ── WHY THIS EXISTS ALONGSIDE Tests.gs ──────────────────────────────────────────────────────
// Tests.gs pins the ALGORITHMS — every golden vector came from the reference Python. This pins
// the WIRING: ten messages in, tickets out, an agent acting on them, and a second pipeline run
// that must not undo any of it. Those are the failures that actually shipped, and no unit test
// would have caught them:
//
//   · a categorised ticket silently reverting to NOVEL on the next Slack reply
//   · the pipeline's row rewrite clobbering an agent's state, note and assignee
//   · the delivery centre being told nothing, or told the raiser's message
//
// Needs node on a dev machine; it cannot run inside Apps Script. It is not part of the paste
// set and nothing in production depends on it.
//
// ── ONE RULE FOR THIS FILE ──────────────────────────────────────────────────────────────────
// Derive expectations from the fixture; never hardcode a count. An assertion pinned at "1 mail"
// broke the moment the fixture grew a second ticket for the same DC — the fixture changing, not
// the code. A test that fails when nothing broke gets ignored, and then it protects nothing.
const fs = require('fs'), vm = require('vm'), path = require('path');
const DIR = path.resolve(__dirname, '..');   // the appscript/ folder

// ── in-memory Sheet ──────────────────────────────────────────────────────────────────────────
function mkSheet(name) {
  const S = { name, data: [], frozen: 0, hidden: false };
  S.getName = () => name;
  S.getLastRow = () => S.data.length;
  S.getLastColumn = () => S.data.reduce((m, r) => Math.max(m, r.length), 0);
  S.getMaxColumns = () => Math.max(S.getLastColumn(), 30);
  S.setFrozenRows = n => { S.frozen = n; return S; };
  S.hideSheet = () => { S.hidden = true; return S; };
  S.deleteRows = (start, n) => { S.data.splice(start - 1, n); };
  S.getRange = (r, c, nr, nc) => {
    nr = nr === undefined ? 1 : nr; nc = nc === undefined ? 1 : nc;
    return {
      getValues() {
        const out = [];
        for (let i = 0; i < nr; i++) {
          const row = S.data[r - 1 + i] || [];
          const o = [];
          for (let j = 0; j < nc; j++) o.push(row[c - 1 + j] === undefined ? '' : row[c - 1 + j]);
          out.push(o);
        }
        return out;
      },
      setValues(vals) {
        vals.forEach((row, i) => {
          const ri = r - 1 + i;
          while (S.data.length <= ri) S.data.push([]);
          row.forEach((v, j) => { S.data[ri][c - 1 + j] = v; });
        });
        return this;
      },
      setValue(v) { return this.setValues([[v]]); },
      getValue() { return this.getValues()[0][0]; },
      clearContent() {
        for (let i = 0; i < nr; i++) {
          const ri = r - 1 + i;
          if (S.data[ri]) for (let j = 0; j < nc; j++) S.data[ri][c - 1 + j] = '';
        }
        return this;
      },
      setFontWeight() { return this; }
    };
  };
  return S;
}
const SHEETS = {};
const SS = {
  getSheetByName: n => SHEETS[n] || null,
  insertSheet: n => (SHEETS[n] = mkSheet(n))
};

// ── fake Slack ───────────────────────────────────────────────────────────────────────────────
const T0 = Math.floor(Date.now() / 1000) - 3600;   // an hour ago, inside the 7-day cold start
const MSGS = [
  { ts: `${T0 + 10}.000100`, user: 'U1', text: 'DC Code: NQS. Daily closure not happening since morning.' },
  { ts: `${T0 + 20}.000200`, user: 'U2', text: 'noted thanks' },
  { ts: `${T0 + 30}.000300`, user: 'U3', text: 'can we go to play arena?' },
  { ts: `${T0 + 40}.000400`, user: 'U1', text: 'heavy rain expected today, plan accordingly' },
  { ts: `${T0 + 50}.000500`, user: 'U4', text: 'payout not received for 9900000001, please check',
    thread_ts: `${T0 + 50}.000500`, reply_count: 2 },
  { ts: `${T0 + 70}.000700`, user: 'U5', text: 'mobile 9900000001 payment still pending' },
  { ts: `${T0 + 80}.000800`, user: 'U6', text: 'मेरा पेमेंट नहीं आया' },
  { ts: `${T0 + 90}.000900`, user: 'U7', text: 'any update ??' },
  // A real message from the live channel. The classifier gets it to within 10.8% of a second
  // category and refuses — the exact case the decision panel exists for.
  { ts: `${T0 + 100}.001000`, user: 'U8', text: 'DC NQS payment nahi aaya, please help' }
];
const REPLIES = {
  [`${T0 + 50}.000500`]: [
    { ts: `${T0 + 50}.000500`, user: 'U4', text: 'payout not received for 9900000001, please check' },
    { ts: `${T0 + 55}.000550`, user: 'U9', text: 'checking now', thread_ts: `${T0 + 50}.000500` },
    { ts: `${T0 + 60}.000600`, user: 'U4', text: 'thanks', thread_ts: `${T0 + 50}.000500` }
  ]
};
let FETCHES = 0;
const UrlFetchApp = {
  fetch(url) {
    FETCHES++;
    const u = new URL(url);
    const m = u.pathname.split('/').pop();
    let body;
    if (m === 'auth.test') body = { ok: true, url: 'https://meesho.slack.com/', team_id: 'T1' };
    else if (m === 'conversations.info') body = { ok: true, channel: { name: 'lm-ams' } };
    else if (m === 'conversations.history') {
      const oldest = parseFloat(u.searchParams.get('oldest') || '0');
      body = { ok: true, messages: MSGS.filter(x => parseFloat(x.ts) >= oldest) };
    } else if (m === 'conversations.replies') {
      body = { ok: true, messages: REPLIES[u.searchParams.get('ts')] || [] };
    } else if (m === 'users.info') {
      const id = u.searchParams.get('user');
      body = { ok: true, user: { real_name: 'User ' + id, is_bot: false,
                                 profile: { email: id.toLowerCase() + '@partner.example' } } };
    } else body = { ok: false, error: 'unknown_method' };
    return { getContentText: () => JSON.stringify(body), getResponseCode: () => 200 };
  }
};

// ── the rest of the Apps Script surface ──────────────────────────────────────────────────────
const PROPS = { SLACK_BOT_TOKEN: 'xoxb-test', CHANNELS: 'C1',
                INTAKE_NOTIFY: 'email-dry', INTAKE_SECRET: 'test-secret' };
const MAILED = [];
const sandbox = {
  console, SEP_UNUSED: null,
  SpreadsheetApp: { getActiveSpreadsheet: () => SS, flush: () => {} },
  PropertiesService: { getScriptProperties: () => ({ getProperty: k => PROPS[k] || null }) },
  LockService: { getScriptLock: () => ({ tryLock: () => true, releaseLock: () => {} }) },
  UrlFetchApp,
  MailApp: { getRemainingDailyQuota: () => 1500,
             sendEmail: o => MAILED.push(o) },
  Logger: { log: () => {} },
  Session: { getActiveUser: () => ({ getEmail: () => 'asha@meesho.com' }),
             getEffectiveUser: () => ({ getEmail: () => 'asha@meesho.com' }) },
  HtmlService: { createTemplateFromFile: () => ({ evaluate: () => ({}) }),
                 createHtmlOutput: h => ({ setTitle: () => h }) },
  ScriptApp: { getProjectTriggers: () => [], newTrigger: () => ({ timeBased: () => ({
    everyMinutes: () => ({ create: () => {} }), everyDays: () => ({ atHour: () => ({ create: () => {} }) }) }) }) },
  DriveApp: { getFoldersByName: () => ({ hasNext: () => false }),
              createFolder: () => ({ createFile: () => {} }) },
  MimeType: { CSV: 'text/csv' },
  Utilities: {
    DigestAlgorithm: { SHA_256: 'SHA_256' }, Charset: { UTF_8: 'UTF_8' },
    sleep: () => {},
    computeDigest: (a, s) => Array.from(require('crypto').createHash('sha256')
      .update(s, 'utf8').digest()).map(b => (b > 127 ? b - 256 : b)),
    formatDate: (d, tz, fmt) => {
      const p = n => String(n).padStart(2, '0');
      return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}` +
             `T${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
    }
  }
};
vm.createContext(sandbox);
const FILES = ['Config.gs', 'Lexicons.gs', 'SlackParser.gs', 'Noise.gs', 'Entities.gs',
               'Evidence.gs', 'Group.gs', 'Dedupe.gs', 'Classify.gs', 'Emit.gs', 'Pipeline.gs',
               'Notify.gs', 'WebApp.gs', 'Health.gs', 'Setup.gs', 'Tests.gs'];
for (const f of FILES) vm.runInContext(fs.readFileSync(path.join(DIR, f), 'utf8'), sandbox, { filename: f });
const ok = (label, cond, extra) => {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${label}${extra ? '   ' + extra : ''}`);
  if (!cond) process.exitCode = 1;
};

// ── seed the reference tabs from the CSVs, exactly as File → Import would ─────────────────────
function parseCsv(t) {
  const R = []; let f = '', r = [], q = false;
  for (let i = 0; i < t.length; i++) { const c = t[i];
    if (q) { if (c === '"') { if (t[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += c; }
    else if (c === '"') q = true; else if (c === ',') { r.push(f); f = ''; }
    else if (c === '\n') { r.push(f); f = ''; R.push(r); r = []; } else if (c !== '\r') f += c; }
  if (f || r.length) { r.push(f); R.push(r); } return R;
}
sandbox.setup();
for (const [file, tab] of [['_dc_codes', '_dc_codes'], ['_dc_denylist', '_dc_denylist'],
                           ['_exemplars', '_exemplars']]) {
  SHEETS[tab].data = parseCsv(fs.readFileSync(`${DIR}/seed/${file}.csv`, 'utf8')).filter(r => r.length > 1);
}


// ── run it ───────────────────────────────────────────────────────────────────────────────────
console.log('setup + seed');
sandbox.setup();
for (const [file, tab] of [['_dc_codes','_dc_codes'],['_dc_denylist','_dc_denylist'],['_exemplars','_exemplars']])
  SHEETS[tab].data = parseCsv(fs.readFileSync(`${DIR}/seed/${file}.csv`, 'utf8')).filter(r => r.length > 1);

// a delivery centre the AM raises tickets for
SHEETS._dc_contacts.data = [sandbox.CONTACT_HEADER,
  ['NQS', 'Nagpur South DC', 'nqs.ops@partner-dc.example', '9812345678', '', true, '']];
// a second agent to assign to
sandbox.appendRows_('_agents', sandbox.AGENT_HEADER,
  [['ravi@meesho.com', 'ravi', true, 'agent', '', '', '']]);
sandbox._cache.agents = undefined;
console.log('  agents:', [...sandbox.agents_().keys()].join(', '));

console.log('\npipeline');
sandbox.pollSlack();
const r1 = sandbox.runPipeline();
console.log('  ', JSON.stringify(r1));

console.log('\nqueue (as asha@meesho.com)');
const q = sandbox.getQueue({});
console.log('  counts:', JSON.stringify(q.counts), '| roster:', q.roster.length,
            '| me:', q.me.email, '| list fields:', Object.keys(q.tickets[0] || {}).length);

const novel = q.tickets.find(t => t.group === 'NEEDS A CATEGORY');
const money = q.tickets.find(t => t.intent === 'payment_not_received');

console.log('\n── what the decision panel will show (real computed values) ──');
q.tickets.filter(t => t.group === 'NEEDS A CATEGORY').forEach(k => {
  const d = sandbox.getTicket(k.key);
  console.log(`  "${d.title.slice(0,44)}"`);
  console.log(`     separation : ${d.margin==null?'n/a':(d.margin*100).toFixed(1)+'%'}   (needs 15%)`);
  console.log(`     torn between: ${d.intent} vs ${d.runner_up || '(nothing)'}`);
  console.log(`     centre      : ${d.dc ? (d.dc.known ? d.dc.name : d.dc.code+' — NO CONTACT ON FILE') : 'none identified'}`);
});

console.log('\n── what the decision screen now says (plain language, real routing) ──');
q.tickets.filter(t => t.group === 'NEEDS A CATEGORY').forEach(k => {
  const d = sandbox.getTicket(k.key);
  console.log(`  "${d.title.slice(0,44)}"`);
  (d.choices || []).forEach((c, i) => {
    console.log(`     ${i+1}. ${c.label}`);
    if (c.meaning) console.log(`        ${c.meaning}`);
    if (c.team)    console.log(`        Goes to ${c.team}`);
  });
  if (!(d.choices||[]).length) console.log('     (no candidates — full list offered)');
});

console.log('\n── agent actions ──');
sandbox.setTicketState(money.key, 'WORKING', 'Chased the payouts team, ETA tomorrow.');
sandbox.setAssignee(money.key, 'ravi@meesho.com');
const named = sandbox.nameDisposition(novel.key, 'Devanagari Payment Issue');
console.log('  named NOVEL ->', named.disposition);

let t = sandbox.getTicket(money.key);
ok('state set', t.state === 'WORKING', t.state);
ok('note saved', t.agent_note.startsWith('Chased'), '');
ok('assignee set', t.assigned_to === 'ravi@meesho.com', t.assigned_to);
ok('audit trail', t.updated_by === 'asha@meesho.com', t.updated_by);

console.log('\n── THE REGRESSION: does the pipeline revert an agent\'s work? ──');
// A new Slack reply touches both issues, which is exactly what used to undo everything.
MSGS.push({ ts: T0 + 200, user: 'U4', text: 'any update on this?',
            thread_ts: `${T0 + 50}.000500` });
MSGS.find(m => m.ts === `${T0 + 50}.000500`).reply_count = 3;
REPLIES[`${T0 + 50}.000500`].push({ ts: `${T0 + 200}.000200`, user: 'U4',
  text: 'any update on this?', thread_ts: `${T0 + 50}.000500` });
sandbox.pollSlack();
const r2 = sandbox.runPipeline();
console.log('  second pipeline run:', JSON.stringify(r2));

t = sandbox.getTicket(money.key);
ok('desk_state survived',  t.state === 'WORKING', t.state);
ok('note survived',        t.agent_note.startsWith('Chased'), t.agent_note.slice(0, 24));
ok('assignee survived',    t.assigned_to === 'ravi@meesho.com', t.assigned_to);
ok('updated_by survived',  t.updated_by === 'asha@meesho.com', t.updated_by);
ok('reply_count advanced', t.reply_count >= 2, String(t.reply_count));

const nt = sandbox.getTicket(novel.key);
ok('category did NOT revert to NOVEL', nt.intent === 'devanagari_payment_issue', nt.intent);
ok('and it left the NEEDS A CATEGORY group', nt.group !== 'NEEDS A CATEGORY', nt.group);

console.log('\n── notifications ──');
PROPS.INTAKE_NOTIFY = 'email'; sandbox._cache = {};
const n = sandbox.sendAcknowledgements();
console.log('  ', JSON.stringify(n));
const toAM = MAILED.filter(m => /partner\.example$/.test(m.to) === false || /^u\d/.test(m.to));
const toDC = MAILED.filter(m => m.to === 'nqs.ops@partner-dc.example');
ok('raiser (AM) notified', MAILED.length > 0, MAILED.length + ' mails');
// Derive the expectation rather than hardcoding it — this assertion was pinned at 1 and broke
// the moment the fixture grew a second NQS ticket, which was the fixture changing, not the code.
const nqsLive = sandbox.getQueue({}).tickets.filter(t => t.dc_code === 'NQS').length;
ok('every NQS ticket notified its centre', toDC.length === nqsLive,
   toDC.length + ' mails for ' + nqsLive + ' NQS ticket(s)');
if (toDC.length) {
  console.log('\n  --- what the DC receives ---');
  toDC[0].body.split('\n').forEach(l => console.log('      ' + l));
}
const amMail = MAILED.find(m => m.to !== 'nqs.ops@partner-dc.example');
ok('DC message differs from the raiser message',
   toDC.length && amMail && toDC[0].body !== amMail.body, '');

console.log('\n── bulk assign ──');
const keys = sandbox.getQueue({}).tickets.slice(0, 3).map(t => t.key);
const b = sandbox.setAssigneeBulk(keys, 'ravi@meesho.com');
ok('bulk assigned', b.assigned === keys.length, b.assigned + '/' + keys.length);

console.log('\n── roster gate ──');
sandbox.Session.getActiveUser = () => ({ getEmail: () => 'stranger@meesho.com' });
sandbox._cache.agents = undefined;
let denied = false;
try { sandbox.setTicketState(money.key, 'CLOSED', null); } catch (e) { denied = /roster/.test(e.message); }
ok('non-roster user is denied', denied, '');
sandbox.Session.getActiveUser = () => ({ getEmail: () => '' });
let anon = false;
try { sandbox.getQueue({}); } catch (e) { anon = /identify/.test(e.message); }
ok('unidentified user is denied', anon, '');

console.log('\n── health ──');
sandbox.Session.getActiveUser = () => ({ getEmail: () => 'asha@meesho.com' });
sandbox._cache = {};
console.log(sandbox.healthCheck());
