/**
 * Classify.gs — disposition matching by BM25 over labelled exemplars, or NOVEL. No model.
 *
 * ── WHAT "NOVEL" MEANS ──────────────────────────────────────────────────────────────────────
 * Not "no match" — BM25 always ranks something. NOVEL is declared when the best score is below
 * a floor, OR when the best and the runner-up are too close to separate. Both are REFUSALS, and
 * the refusal is the feature: a confident wrong label is invisible downstream and quietly
 * mis-routes a ticket, while a NOVEL goes to a human and becomes a new exemplar.
 *
 * ── THE CREEP LOOP LIVES HERE ───────────────────────────────────────────────────────────────
 * The index is rebuilt from the _exemplars tab on every run. So when somebody names a NOVEL
 * ticket in the UI, that row is appended to the tab and the NEXT run — five minutes later —
 * classifies against it. Correction becomes coverage with no deploy and no retraining. That is
 * the whole reason the exemplars are a Sheet tab rather than a constant in this file.
 *
 * ── A KNOWN LIMIT, STATED ───────────────────────────────────────────────────────────────────
 * The tokeniser is [a-z0-9]+ over lowercased text. Devanagari produces NO tokens at all, so a
 * message written in Devanagari scores 0.00 against every disposition and is always NOVEL. That
 * is measured, not accidental: BM25 is lexical and cannot match across scripts. It is why those
 * messages route to a human rather than being silently mislabelled, and it is the gap a
 * semantic tier would close. Do not "fix" it by widening the token class — that would produce
 * matches on shared punctuation and look like it was working.
 */

var _CL_K1 = 1.2, _CL_B = 0.6;

/** Render a number the way Python renders a float, so the explanation strings are diffable
 *  against the reference implementation: 3 prints as "3.0", not "3". */
function pyFloat_(x) {
  var f = Number(x);
  return (f === Math.floor(f) && isFinite(f)) ? f.toFixed(1) : String(f);
}

/**
 * 62 stopwords: an English block, then a deliberate Hinglish block.
 *
 * `not` is deliberately ABSENT — it is the difference between "payment received" and "payment
 * not received", which are two different dispositions.
 */
function STOP() {
  if (_cache.stop) return _cache.stop;
  _cache.stop = new Set([
    'the', 'a', 'an', 'is', 'am', 'are', 'was', 'were', 'be', 'been', 'to', 'of', 'in', 'on',
    'at', 'for', 'and', 'or', 'but', 'if', 'it', 'this', 'that', 'my', 'our', 'we', 'i',
    'you', 'please', 'pls', 'kindly', 'sir', 'madam', 'team', 'hi', 'hello', 'dear', 'thanks',
    'hai', 'hain', 'ka', 'ki', 'ke', 'ko', 'se', 'me', 'mein', 'kar', 'karo', 'raha', 'rahi',
    'gaya', 'gayi', 'ho', 'hu', 'hoon', 'bhi', 'aur', 'par', 'jo', 'kya', 'abhi'
  ]);
  return _cache.stop;
}

/** Lowercase, split on anything not [a-z0-9], drop stopwords and single characters.
 *  Devanagari, emoji, @, _, - and . are all separators — see the limit noted above. */
function clTok_(text) {
  var out = [], m, re = /[a-z0-9]+/g, s = String(text || '').toLowerCase();
  while ((m = re.exec(s)) !== null) {
    if (m[0].length > 1 && !STOP().has(m[0])) out.push(m[0]);
  }
  return out;
}

/**
 * Build the BM25 index from the _exemplars tab.
 *
 * Documents that tokenise to nothing are DROPPED — one of the 1,259 seed rows does, which is
 * why the index reports N=1258. That number, avgdl and default_idf are the three values
 * Tests.gs pins: if a port of this file is wrong, at least one of them moves.
 */
function buildMatcher_() {
  var C = CFG().classify;
  var rows = readTabObjects_(CFG().tabs.exemplars);
  var merge = CFG().mergeMoneyClasses, money = CFG().moneyClasses, mergedName = CFG().moneyMergedName;

  var docs = [];
  for (var i = 0; i < rows.length; i++) {
    var text = String(rows[i].text || '');
    var toks = clTok_(text);
    if (!toks.length) continue;
    var tf = new Map();
    for (var t = 0; t < toks.length; t++) tf.set(toks[t], (tf.get(toks[t]) || 0) + 1);
    var disp = String(rows[i].disposition || '');
    if (merge && money.indexOf(disp) >= 0) disp = mergedName;
    docs.push({ disposition: disp, tf: tf, len: toks.length,
                provenance: String(rows[i].label_provenance || 'silver') });
  }
  if (!docs.length) return null;

  var n = docs.length;
  var df = new Map();                                   // DOCUMENT frequency, not term frequency
  for (var d = 0; d < docs.length; d++) {
    docs[d].tf.forEach(function (_v, term) { df.set(term, (df.get(term) || 0) + 1); });
  }
  var idf = new Map();
  df.forEach(function (c, term) { idf.set(term, Math.log(1 + (n - c + 0.5) / (c + 0.5))); });
  var defaultIdf = Math.log(1 + (n - 1 + 0.5) / 1.5);
  var totalLen = 0;
  for (var k = 0; k < docs.length; k++) totalLen += docs[k].len;

  return { docs: docs, idf: idf, defaultIdf: defaultIdf, avgdl: totalLen / n, n: n,
           minScore: C.minScore, minMargin: C.minMargin, topK: C.topK, goldWeight: C.goldWeight };
}

/** The index, built once per execution. Null when the tab is empty or missing. */
function MATCHER() {
  if (_cache.matcher !== undefined) return _cache.matcher;
  _cache.matcher = buildMatcher_();
  return _cache.matcher;
}

/** BM25 for one query against one document. `norm` is ALREADY multiplied by k1 — do not
 *  multiply again. `q` is NOT deduplicated, so a repeated query term scores repeatedly. */
function scoreDoc_(M, q, d) {
  var s = 0.0;
  var norm = _CL_K1 * (1 - _CL_B + _CL_B * d.len / (M.avgdl || 1));
  for (var i = 0; i < q.length; i++) {
    var f = d.tf.get(q[i]) || 0;
    if (f) {
      var idf = M.idf.has(q[i]) ? M.idf.get(q[i]) : M.defaultIdf;
      s += idf * (f * (_CL_K1 + 1)) / (f + norm);
    }
  }
  return s;
}

/**
 * Best disposition for a message, or NOVEL, with everything needed to explain the answer.
 *
 * Aggregating the top-k by disposition means three moderate exemplars of one class beat a
 * single strong outlier of another — which is what a pure top-1 gets wrong.
 *
 * gold_weight makes a human-confirmed exemplar count for 3. Without it the creep loop does not
 * work: aggregation SUMS the top-k, so a disposition holding 81 machine labels fills four of
 * five slots while a just-confirmed class holds one, the human's label loses on volume, and the
 * queue becomes theatre — you label something and the next identical message is still NOVEL.
 */
function classifyText(text) {
  var M = MATCHER();
  if (!M) {
    // The index is MISSING, which is not the same as "everything is novel". Collapsing the two
    // would look like a taxonomy collapse in the reports. Callers leave intent blank instead.
    return { disposition: null, skipped: true, score: 0, runner_up: null, margin: 0,
             why: 'no exemplar index — the _exemplars tab is empty or missing' };
  }
  var q = clTok_(text);
  if (!q.length) {
    return { disposition: 'NOVEL', score: 0.0, runner_up: null, margin: 0.0,
             why: 'no scoreable tokens' };
  }

  var scored = [];
  for (var i = 0; i < M.docs.length; i++) {
    scored.push([scoreDoc_(M, q, M.docs[i]), M.docs[i]]);
  }
  scored.sort(function (x, y) { return y[0] - x[0]; });
  scored = scored.slice(0, M.topK);

  var agg = new Map();
  for (var s = 0; s < scored.length; s++) {
    var d = scored[s][1];
    var w = d.provenance === 'gold' ? M.goldWeight : 1.0;
    agg.set(d.disposition, (agg.get(d.disposition) || 0) + scored[s][0] * w);
  }
  var ranked = Array.from(agg.entries()).sort(function (x, y) { return y[1] - x[1]; });
  var best = ranked[0][0], bestScore = ranked[0][1];
  var runner = ranked.length > 1 ? ranked[1][0] : null;
  var runnerScore = ranked.length > 1 ? ranked[1][1] : 0.0;
  var total = (bestScore + runnerScore) || 1.0;
  var margin = (bestScore - runnerScore) / total;

  if (bestScore < M.minScore) {
    // runner_up is deliberately `best`, not `runner` — it surfaces what the answer WOULD have
    // been if the floor had not stopped it, which is the useful thing to show a human here.
    return { disposition: 'NOVEL', score: round_(bestScore, 2), runner_up: best,
             margin: round_(margin, 3),
             why: 'best score ' + bestScore.toFixed(2) + ' below floor ' + pyFloat_(M.minScore) };
  }
  if (margin < M.minMargin) {
    return { disposition: 'NOVEL', score: round_(bestScore, 2), runner_up: runner,
             margin: round_(margin, 3),
             why: best + ' and ' + runner + ' within ' + (margin * 100).toFixed(1) +
                  '% — too close to separate' };
  }
  return { disposition: best, score: round_(bestScore, 2), runner_up: runner,
           margin: round_(margin, 3), why: 'nearest exemplars agree on ' + best };
}
