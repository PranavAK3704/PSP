/**
 * Tests.gs — run runAllTests() from the editor BEFORE trusting anything this project produces.
 *
 * ── WHAT THESE ARE ──────────────────────────────────────────────────────────────────────────
 * Every expected value below was produced by running the reference Python implementation and
 * capturing its output. They are not hand-written guesses, and they are not a restatement of
 * what this code happens to do — several of them fail if you make the single most tempting
 * "simplification" in the file they cover.
 *
 * The six that matter most, and what each catches:
 *
 *   1. sha256Hex        — Apps Script returns SIGNED bytes and must encode an embedded NUL as
 *                         one 0x00. If either is wrong, every ticket id is wrong.
 *   2. seqRatio         — difflib's autojunk. Omit it and the 0.119 case below returns 0.616,
 *                         which is the wrong side of the 0.72 duplicate threshold.
 *   3. dcTierA          — the (?i:...) expansion. Add a global `i` flag and "DC landing time"
 *                         starts extracting LAN.
 *   4. evidenceScore    — the Devanagari word-boundary fix. Use \b and the Hindi rows score 0.
 *   5. normalise        — JS \w is ASCII. Use it and Devanagari vanishes from the gate.
 *   6. BM25 index       — three numbers that move if the tokeniser or the index is off.
 *
 * Non-ASCII characters in this file are written as \uXXXX escapes on purpose, so that nothing
 * in the copy-paste path can silently mangle them and turn a real failure into a passing test.
 */

var _T = { pass: 0, fail: 0, skip: 0, notes: [] };

function _eq(name, got, want) {
  var g = JSON.stringify(got), w = JSON.stringify(want);
  if (g === w) { _T.pass++; return; }
  _T.fail++;
  _T.notes.push('FAIL  ' + name + '\n        got  ' + g + '\n        want ' + w);
}
function _close(name, got, want, tol) {
  if (typeof got === 'number' && Math.abs(got - want) <= tol) { _T.pass++; return; }
  _T.fail++;
  _T.notes.push('FAIL  ' + name + '\n        got  ' + got + '\n        want ' + want);
}
function _skip(name, why) { _T.skip++; _T.notes.push('SKIP  ' + name + ' \u2014 ' + why); }

/** 1. Identity. A wrong digest here means every ticket id in the system is wrong. */
function testIdempotency() {
  _eq('idempotencyKey slack/C08T6NLL77H/1788482771.760339', idempotencyKey('slack', 'C08T6NLL77H', '1788482771.760339'), '895a36066aedd39ed825e49a26563f38b19959704348bec3db20975ede49d9de');
  _eq('idempotencyKey slack/C1/1.1', idempotencyKey('slack', 'C1', '1.1'), '21f5799168830f1d8cd6c184ed0b3f1eb7a91652555a8e8ce9df9070c1354e2c');
  _eq('idempotencyKey whatsapp/C08T6NLL77H/1788482771.760339', idempotencyKey('whatsapp', 'C08T6NLL77H', '1788482771.760339'), '13408bb0f88ac9dd8b1c2f4c2ecfe78ec5f4a8bcf586d852e72fbe966138be41');
  _eq('idempotencyKey slack/C-accent/1.1', idempotencyKey('slack', 'C\u00e9', '1.1'), 'b870afadf191d0b58510e1cda7e5cf2c2d5c6357e12ea2abf802954946da1b2c');
  _eq('sha256Hex empty', sha256Hex(''), 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
  // 64 lowercase hex, no exceptions: a short digest means the zero-pad is missing, and an
  // uppercase or negative character means the signed-byte mask is.
  _eq('sha256Hex shape', /^[0-9a-f]{64}$/.test(sha256Hex('x')), true);
}

/** 2. Timestamps. Built from UTC + a literal offset, never the Asia/Calcutta zone name. */
function testIstStamp() {
  _eq('istStamp 0', istStamp(0), '1970-01-01T05:30:00+05:30');
  _eq('istStamp 1788482771.760339', istStamp(1788482771.760339), '2026-09-04T06:16:11+05:30');
  _eq('istStamp 1788482771', istStamp(1788482771), '2026-09-04T06:16:11+05:30');
  _eq('istStamp 1735689599', istStamp(1735689599), '2025-01-01T05:29:59+05:30');
  _eq('istStamp 1735689600', istStamp(1735689600), '2025-01-01T05:30:00+05:30');
}

/** 3. difflib.SequenceMatcher, INCLUDING autojunk. The last case is the one that matters:
 *  without autojunk it returns 0.615854, which is the wrong side of the 0.72 threshold. */
function testSeqRatio() {
  _close('seqRatio mx1 captain panel not op|mx1 captain panel is not', seqRatio('mx1 captain panel not opening', 'mx1 captain panel is not opening'), 0.9508196721311475, 1e-12);
  _close('seqRatio payout not received for |payout not received for ', seqRatio('payout not received for last week', 'payout not received for last week'), 1.0, 1e-12);
  _close('seqRatio dc code: nqs. closure is|dc code: nqs. closure is', seqRatio('dc code: nqs. closure issue', 'dc code: nqs. closure issues'), 0.9818181818181818, 1e-12);
  _close('seqRatio abc|xyz', seqRatio('abc', 'xyz'), 0.0, 1e-12);
  _close('seqRatio |', seqRatio('', ''), 1.0, 1e-12);
  _close('seqRatio AUTOJUNK long pair', seqRatio('the load planning panel is down again the load planning panel is down again the load planning panel is down again the load planning panel is down again the load planning panel is down again the load planning panel is down again the load planning panel is down again the load planning panel is down again', 'the load planning panel is down again today the load planning panel is down again today the load planning panel is down again today the load planning panel is down again today the load planning panel is down again today the load planning panel is down again today the load planning panel is down again today the load planning panel is down again today'), 0.11926605504587157, 1e-12);
  // Long enough to trigger the purge, and asymmetric because the purge is computed on b only.
  _eq('seqRatio is bounded', seqRatio('abc','abc') === 1.0 && seqRatio('abc','xyz') === 0.0, true);
}

/** 4. normalise: three substitutions, and Devanagari MUST survive them. */
function testNormalise() {
  _eq('normalise Okk!!', normalise('Okk!!'), 'okk');
  _eq('normalise +1', normalise('+1'), '1');
  _eq('normalise fyi.', normalise('fyi.'), 'fyi');
  _eq('normalise ???', normalise('???'), '');
  _eq('normalise \ud83d\udc4d', normalise('\ud83d\udc4d'), '');
  _eq('normalise thanks a lot bhai \ud83d\ude4f', normalise('thanks a lot bhai \ud83d\ude4f'), 'thanks a lot bhai');
  _eq('normalise <@U09HH8QKZ43>', normalise('<@U09HH8QKZ43>'), '');
  _eq('normalise \u092e\u0947\u0930\u093e \u092a\u0947\u092e\u0947\u0902\u091f', normalise('\u092e\u0947\u0930\u093e \u092a\u0947\u092e\u0947\u0902\u091f'), '\u092e \u0930 \u092a \u092e \u091f');
  _eq('normalise Hello <!channel> team', normalise('Hello <!channel> team'), 'hello team');
}

/** 5. The gate and the informational axis. Note two cases that look like bugs and are not:
 *  'rain' matches inside 'training' and 'app' inside 'Happy' — plain substring search is the
 *  measured behaviour, and the reported shares were computed with it. */
function testNoiseGate() {
  _eq('noise heavy rain today', noiseClassify('heavy rain today', null, false), {gated: false, gateRule: null, informational: true, informationalRule: "strict:rain", borderline: false});
  _eq('noise training today then rain later', noiseClassify('training today then rain later', null, false), {gated: false, gateRule: null, informational: true, informationalRule: "strict:rain", borderline: false});
  _eq('noise Happy to help, rain expected', noiseClassify('Happy to help, rain expected', null, false), {gated: false, gateRule: null, informational: false, informationalRule: "kept:app_then_rain_with_ask(help)", borderline: false});
  _eq('noise planned downtime tonight', noiseClassify('planned downtime tonight', null, false), {gated: false, gateRule: null, informational: true, informationalRule: "announcement:planned downtime", borderline: false});
  _eq('noise Due to festival manpower absen', noiseClassify('Due to festival manpower absenteeism and rain since morning the hub is slow', null, false), {gated: false, gateRule: null, informational: true, informationalRule: "loose:rain(after festival)", borderline: true});
  _eq('noise Dear losses team, I have so ma', noiseClassify('Dear losses team, I have so many losses at my hub please reverse - rain has made it extremely difficult', null, false), {gated: false, gateRule: null, informational: false, informationalRule: "kept:loss_then_rain_with_ask(please)", borderline: false});
  _eq('noise noted thanks', noiseClassify('noted thanks', null, false), {gated: true, gateRule: "combined_ack", informational: false, informationalRule: null, borderline: false});
  _eq('noise haan bhai ho gaya thank you \ud83d\ude4f', noiseClassify('haan bhai ho gaya thank you \ud83d\ude4f', null, false), {gated: true, gateRule: "ack_phrase", informational: false, informationalRule: null, borderline: false});
  _eq('noise thanks but the DC is still dow', noiseClassify('thanks but the DC is still down', null, false), {gated: false, gateRule: null, informational: false, informationalRule: null, borderline: false});
  _eq('noise <@U1> <@U2>', noiseClassify('<@U1> <@U2>', null, false), {gated: true, gateRule: "mention_only", informational: false, informationalRule: null, borderline: false});
  _eq('noise \ud83d\udc4d', noiseClassify('\ud83d\udc4d', null, false), {gated: true, gateRule: "emoji_only", informational: false, informationalRule: null, borderline: false});
  _eq('noise ???', noiseClassify('???', null, false), {gated: false, gateRule: null, informational: false, informationalRule: null, borderline: false});
  _eq('noise (empty)', noiseClassify('', null, true), {gated: false, gateRule: "kept:empty_text_with_attachment", informational: false, informationalRule: null, borderline: false});
  _eq('noise gm all \ud83d\ude4f', noiseClassify('gm all \ud83d\ude4f', null, false), {gated: true, gateRule: "ack_phrase", informational: false, informationalRule: null, borderline: false});
  _eq('noise x', noiseClassify('x', 'channel_join', false), {gated: true, gateRule: "subtype:channel_join", informational: false, informationalRule: null, borderline: false});
}

/** 6. Positive evidence. The Devanagari rows fail if \b is used instead of the explicit
 *  lookarounds — \u0928\u0939\u0940\u0902 ends in an anusvara, which is not a word character. */
function testEvidence() {
  (function(){ var r = evidenceScore('can we go to play arena?', []);
    _eq('evidence decision can we go to play arena?', r.decision, 'not_an_issue');
    _close('evidence score can we go to play arena?', r.score, 0.0, 1e-9);
    _eq('evidence reasons can we go to play arena?', r.reasons, 'grammar_only:no_identifier_and_no_ops_noun'); })();
  (function(){ var r = evidenceScore('can we get the payout released?', []);
    _eq('evidence decision can we get the payout releas', r.decision, 'issue');
    _close('evidence score can we get the payout releas', r.score, 1.4, 1e-9);
    _eq('evidence reasons can we get the payout releas', r.reasons, 'ops:payout; request:can we,released'); })();
  (function(){ var r = evidenceScore('payout nahi aaya', []);
    _eq('evidence decision payout nahi aaya', r.decision, 'issue');
    _close('evidence score payout nahi aaya', r.score, 1.1, 1e-9);
    _eq('evidence reasons payout nahi aaya', r.reasons, 'ops:payout; problem:nahi'); })();
  (function(){ var r = evidenceScore('URGENT please help ASAP!!', []);
    _eq('evidence decision URGENT please help ASAP!!', r.decision, 'not_an_issue');
    _close('evidence score URGENT please help ASAP!!', r.score, 0.0, 1e-9);
    _eq('evidence reasons URGENT please help ASAP!!', r.reasons, 'grammar_only:no_identifier_and_no_ops_noun'); })();
  (function(){ var r = evidenceScore('any update ??', []);
    _eq('evidence decision any update ??', r.decision, 'orphan');
    _close('evidence score any update ??', r.score, 0.0, 1e-9);
    _eq('evidence reasons any update ??', r.reasons, 'grammar_only:no_identifier_and_no_ops_noun; followup:any update'); })();
  (function(){ var r = evidenceScore('?', []);
    _eq('evidence decision ?', r.decision, 'orphan');
    _close('evidence score ?', r.score, 0.0, 1e-9);
    _eq('evidence reasons ?', r.reasons, 'followup:punctuation_only'); })();
  (function(){ var r = evidenceScore('please share the wifi password', []);
    _eq('evidence decision please share the wifi passwo', r.decision, 'issue');
    _close('evidence score please share the wifi passwo', r.score, 1.4, 1e-9);
    _eq('evidence reasons please share the wifi passwo', r.reasons, 'ops:password; request:please,share'); })();
  (function(){ var r = evidenceScore('\u092e\u0947\u0930\u093e \u092a\u0947\u092e\u0947\u0902\u091f \u0928\u0939\u0940\u0902 \u0906\u092f\u093e', []);
    _eq('evidence decision \u092e\u0947\u0930\u093e \u092a\u0947\u092e\u0947\u0902\u091f \u0928\u0939\u0940\u0902 \u0906\u092f\u093e', r.decision, 'issue');
    _close('evidence score \u092e\u0947\u0930\u093e \u092a\u0947\u092e\u0947\u0902\u091f \u0928\u0939\u0940\u0902 \u0906\u092f\u093e', r.score, 1.1, 1e-9);
    _eq('evidence reasons \u092e\u0947\u0930\u093e \u092a\u0947\u092e\u0947\u0902\u091f \u0928\u0939\u0940\u0902 \u0906\u092f\u093e', r.reasons, 'ops:\u092a\u0947\u092e\u0947\u0902\u091f; problem:\u0928\u0939\u0940\u0902'); })();
  (function(){ var r = evidenceScore('\u0928\u0939\u0940\u0902 \u0906\u092f\u093e', []);
    _eq('evidence decision \u0928\u0939\u0940\u0902 \u0906\u092f\u093e', r.decision, 'not_an_issue');
    _close('evidence score \u0928\u0939\u0940\u0902 \u0906\u092f\u093e', r.score, 0.0, 1e-9);
    _eq('evidence reasons \u0928\u0939\u0940\u0902 \u0906\u092f\u093e', r.reasons, 'grammar_only:no_identifier_and_no_ops_noun'); })();
  (function(){ var r = evidenceScore('hardstop laga hua hai', []);
    _eq('evidence decision hardstop laga hua hai', r.decision, 'weak');
    _close('evidence score hardstop laga hua hai', r.score, 0.6, 1e-9);
    _eq('evidence reasons hardstop laga hua hai', r.reasons, 'ops:hardstop'); })();
}

/** 7. DC extraction. Needs the _dc_codes / _dc_denylist tabs. The case-sensitivity rows
 *  ('DC code: nxg' and 'DC landing time') are the ones that catch a stray `i` flag. */
function testDcExtraction() {
  var R = DCREG();
  if (!R.denylist.size) { _skip('DC extraction', 'the _dc_denylist tab is empty \u2014 import seed/_dc_denylist.csv'); return; }
  _eq('tierA DC Code: NXG', Array.from(dcTierA('DC Code: NXG', R.denylist).keys()).sort(), ["NXG"]);
  _eq('tierA DC code: nxg', Array.from(dcTierA('DC code: nxg', R.denylist).keys()).sort(), []);
  _eq('tierA DC landing time is late', Array.from(dcTierA('DC landing time is late', R.denylist).keys()).sort(), []);
  _eq('tierA The *NQS DC landing time', Array.from(dcTierA('The *NQS DC landing time', R.denylist).keys()).sort(), ["NQS"]);
  _eq('tierA HY9, IAM, VN4 hub not recive', Array.from(dcTierA('HY9, IAM, VN4 hub not recive', R.denylist).keys()).sort(), ["HY9", "IAM", "VN4"]);
  _eq('tierA LMDC PJ2/PJR', Array.from(dcTierA('LMDC PJ2/PJR', R.denylist).keys()).sort(), ["PJ2", "PJR"]);
  _eq('tierA FMH MFC issue', Array.from(dcTierA('FMH MFC issue', R.denylist).keys()).sort(), ["MFC"]);
  _eq('tierA Hubs CKH and RW3', Array.from(dcTierA('Hubs CKH and RW3', R.denylist).keys()).sort(), ["CKH", "RW3"]);
  _eq('tierA DC Codes:\nNQS\nIQU\nUB1', Array.from(dcTierA('DC Codes:\nNQS\nIQU\nUB1', R.denylist).keys()).sort(), ["IQU", "NQS", "UB1"]);
  _eq('tierA *IQU* DC issue', Array.from(dcTierA('*IQU* DC issue', R.denylist).keys()).sort(), ["IQU"]);
  _eq('tierA DC _UB1_ pending', Array.from(dcTierA('DC _UB1_ pending', R.denylist).keys()).sort(), ["UB1"]);
  _eq('tierA DC ~PJ2~ and `J93`', Array.from(dcTierA('DC ~PJ2~ and `J93`', R.denylist).keys()).sort(), ["J93", "PJ2"]);
  _eq('tierA hub L9D and J93 down', Array.from(dcTierA('hub L9D and J93 down', R.denylist).keys()).sort(), ["J93", "L9D"]);
  _eq('tierA DC R2F, K6L & T5X', Array.from(dcTierA('DC R2F, K6L & T5X', R.denylist).keys()).sort(), ["K6L", "R2F", "T5X"]);
  _eq('tierA DC CODE IS MISSING', Array.from(dcTierA('DC CODE IS MISSING', R.denylist).keys()).sort(), []);
  _eq('tierA DC code 3531 issue', Array.from(dcTierA('DC code 3531 issue', R.denylist).keys()).sort(), []);
  _eq('tierA hub NQSX down', Array.from(dcTierA('hub NQSX down', R.denylist).keys()).sort(), []);
  _eq('tierA DC AB1234 issue', Array.from(dcTierA('DC AB1234 issue', R.denylist).keys()).sort(), []);
  _eq('tierA 342 Tids are coming in Hardsto', Array.from(dcTierA('342 Tids are coming in Hardstop loss', R.denylist).keys()).sort(), []);
  if (!R.registry.size) { _skip('DC tier B', 'the _dc_codes tab is empty'); return; }
  _eq('tierB 342 Tids are coming in Hardsto', Array.from(dcTierB('342 Tids are coming in Hardstop loss', R.registry, R.denylist, new Set(Array.from(dcTierA('342 Tids are coming in Hardstop loss', R.denylist).keys()))).keys()).sort(), []);
  _eq('tierB the NQS hub is down', Array.from(dcTierB('the NQS hub is down', R.registry, R.denylist, new Set(Array.from(dcTierA('the NQS hub is down', R.denylist).keys()))).keys()).sort(), []);
  _eq('tierB MFC and IQU both down', Array.from(dcTierB('MFC and IQU both down', R.registry, R.denylist, new Set(Array.from(dcTierA('MFC and IQU both down', R.denylist).keys()))).keys()).sort(), ["IQU", "MFC"]);
}

/** 8. The numeric kinds must be MUTUALLY EXCLUSIVE — the first line is the measured case
 *  where unguarded patterns find a 'mobile' inside a ticket id and a ticket id inside a
 *  waybill, producing a plausible count and a wrong answer. */
function testEntityPatterns() {
  var R = DCREG();
  _eq('entities Registered mobile 9900000001, ', extractEntities('Registered mobile 9900000001, ticket 4788325630026, waybill VL0084870753799.', R.registry, R.denylist).filter(function(e){return e.kind !== 'dc_code';}).map(function(e){return [e.kind, e.value];}).sort(), [["kapture_id", "4788325630026"], ["mobile", "9900000001"], ["waybill", "VL0084870753799"]]);
  _eq('entities pilot id 12345678 not working', extractEntities('pilot id 12345678 not working', R.registry, R.denylist).filter(function(e){return e.kind !== 'dc_code';}).map(function(e){return [e.kind, e.value];}).sort(), [["pilot_id", "12345678"]]);
  _eq('entities VL9999999999999 held', extractEntities('VL9999999999999 held', R.registry, R.denylist).filter(function(e){return e.kind !== 'dc_code';}).map(function(e){return [e.kind, e.value];}).sort(), [["waybill", "VL9999999999999"]]);
  _eq('entities mail ops.team@meesho.com', extractEntities('mail ops.team@meesho.com', R.registry, R.denylist).filter(function(e){return e.kind !== 'dc_code';}).map(function(e){return [e.kind, e.value];}).sort(), [["email", "ops.team@meesho.com"]]);
}

/** 9. Titles: first sentence, truncated on a word boundary, DC prefix applied after. */
function testTitles() {
  _eq('title (empty)', makeTitle('', null), '(no text \u2014 see attachment)');
  _eq('title aaaaaaaaaaaaaaaaaaaaaaaaaa', makeTitle('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', null), 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\u2026');
  _eq('title Panel down. Please check u', makeTitle('Panel down. Please check urgently.', null), 'Panel down.');
  _eq('title <@U09HH8QKZ43> panel down', makeTitle('<@U09HH8QKZ43> panel down', null), 'panel down');
  _eq('title DC Code: NQS. Daily closur', makeTitle('DC Code: NQS. Daily closure not happening since morning.', null), 'DC Code: NQS.');
  _eq('title Check <https://example.com', makeTitle('Check <https://example.com/x|the panel> please. Second.', null), 'Check https://example.com/x please.');
  _eq('title Hello <!channel> <!subteam', makeTitle('Hello <!channel> <!subteam^S123|@ops> issue here. next', null), 'Hello issue here.');
  _eq('title line one\nline two', makeTitle('line one\nline two', null), 'line one line two');
  _eq('title with dc', makeTitle('Panel down. Please check.', 'NQS'), '[NQS] Panel down.');
  // The prefix is deliberately OUTSIDE the length budget: the hub code is the most useful
  // thing in a queue row and must never be the part that gets cut.
  _eq('title length no dc', makeTitle('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', null).length, 91);
}

/** 10. Flags. A missing required identifier is a FLAG, not a rejection. */
function testChecks() {
  var R = DCREG();
  _eq('flags payment_not_received {"mobile": ["9900000001"]}', runChecks({disposition: 'payment_not_received', reply_count: 3, first_response_latency_s: 12.5}, typedEntities({"mobile": ["9900000001"]}, R.registry, R.denylist), new Set(['payment_not_received','hardstop_loss','load_planning'])), []);
  _eq('flags hardstop_loss {}', runChecks({disposition: 'hardstop_loss', reply_count: 0, first_response_latency_s: null}, typedEntities({}, R.registry, R.denylist), new Set(['payment_not_received','hardstop_loss','load_planning'])), ["missing_required_waybill_for_hardstop_loss", "no_actionable_identifier"]);
  _eq('flags NOVEL {"dc_code": ["NQS"]}', runChecks({disposition: 'NOVEL', reply_count: 2, first_response_latency_s: null}, typedEntities({"dc_code": ["NQS"]}, R.registry, R.denylist), new Set(['payment_not_received','hardstop_loss','load_planning'])), ["disposition_novel_needs_human", "replies_exist_but_no_qualifying_first_response"]);
  _eq('flags load_planning {"dc_code": ["ZZZ"]}', runChecks({disposition: 'load_planning', reply_count: 0, first_response_latency_s: null}, typedEntities({"dc_code": ["ZZZ"]}, R.registry, R.denylist), new Set(['payment_not_received','hardstop_loss','load_planning'])), ["dc_code_not_in_registry:ZZZ"]);
}

/** 11. The BM25 index. Three numbers that move if the tokeniser or the index build is off. */
function testClassifierIndex() {
  var M = MATCHER();
  if (!M) { _skip('BM25 index', 'the _exemplars tab is empty \u2014 import seed/_exemplars.csv'); return; }
  _eq('BM25 N (one seed row tokenises empty and is dropped)', M.n, 1258);
  _close('BM25 avgdl', M.avgdl, 20.524642289348172, 1e-12);
  _close('BM25 default_idf', M.defaultIdf, 6.732607925936183, 1e-12);
  _eq('BM25 vocab', M.idf.size, 3324);
  // Devanagari yields no tokens at all, so it is always NOVEL. Measured, not accidental.
  _eq('BM25 devanagari is NOVEL', classifyText('\u092e\u0947\u0930\u093e \u092a\u0947\u092e\u0947\u0902\u091f \u0928\u0939\u0940\u0902 \u0906\u092f\u093e').disposition, 'NOVEL');
}

/** Grouping windows: the LONGEST window among the shared kinds wins. */
function testGroupingWindows() {
  _eq('window mobile', windowFor_(new Set(['mobile'])), 180 * 86400);
  _eq('window waybill', windowFor_(new Set(['waybill'])), 14 * 86400);
  // A message sharing both is judged against 180 days: the person outlives the parcel.
  _eq('window waybill+mobile', windowFor_(new Set(['waybill', 'mobile'])), 180 * 86400);
  _eq('window unknown kind', windowFor_(new Set(['nonsense'])), 7 * 86400);
  _eq('window empty', windowFor_(new Set([])), 7 * 86400);
  _eq('issueId', issueId_('C1', '1.5'), 'ISS-C1-1.5');
}

/** Run everything. This is the function to run from the editor. */
function runAllTests() {
  _T = { pass: 0, fail: 0, skip: 0, notes: [] };
  var suites = [testIdempotency, testIstStamp, testSeqRatio, testNormalise, testNoiseGate,
                testEvidence, testDcExtraction, testEntityPatterns, testTitles, testChecks,
                testClassifierIndex, testGroupingWindows];
  for (var i = 0; i < suites.length; i++) {
    try { suites[i](); }
    catch (e) {
      _T.fail++;
      _T.notes.push('ERROR ' + suites[i].name + ': ' + (e.message || e) +
                    (e.stack ? '\n        ' + String(e.stack).split('\n')[1] : ''));
    }
  }
  var head = _T.fail ? (_T.fail + ' FAILED, ' + _T.pass + ' passed')
                     : ('ALL PASS \u2014 ' + _T.pass + ' assertions');
  if (_T.skip) head += ', ' + _T.skip + ' skipped';
  var out = head + (_T.notes.length ? '\n\n' + _T.notes.join('\n') : '');
  Logger.log(out);
  return out;
}
