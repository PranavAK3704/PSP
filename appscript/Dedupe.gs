/**
 * Dedupe.gs — duplicate detection. LINKED, NEVER MERGED.
 *
 * ── WHY NOTHING IS EVER MERGED ──────────────────────────────────────────────────────────────
 * The MX1 captain-panel issue was posted by the same author in BOTH in-scope channels 47
 * seconds apart with near-identical text — and then the threads forked: 4 replies on one side,
 * 2 on the other. Merging would have discarded one of the two forked threads. So both issues
 * stay in the register with their own threads and their own closure state; the later one just
 * carries duplicate_of pointing at the earlier, and the earlier one's occurrence_count includes
 * it. Nothing is deleted and no fields are combined.
 *
 * ── PORTING difflib.SequenceMatcher IS THE RISKIEST THING IN THIS PROJECT ───────────────────
 * It is not Levenshtein, not Jaccard, not Dice on bigrams. It is a recursive
 * longest-matching-block algorithm with one CPython behaviour that every JS port on npm omits:
 * AUTOJUNK. Once len(b) >= 200, any character occurring in b more than len(b)//100 + 1 times is
 * dropped from the match index. For natural language that is every space and every common
 * letter, so it guts the matcher on long strings — which Slack messages routinely are.
 *
 * Measured on one real pair:  ratio WITH autojunk = 0.119,  WITHOUT = 0.616.
 * The threshold is 0.72. Omitting autojunk does not shift the number slightly; it moves pairs
 * across the bar in both directions. It is reproduced exactly below.
 */

/** Lowercase, strip every angle-bracket run to a space, collapse whitespace, trim. Deliberately
 *  a SIMPLER stripper than Emit's, which preserves URLs; here a URL is noise to compare. */
function dedupeNorm_(t) {
  return String(t || '').toLowerCase().replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
}

/** Separator for composite Map keys. JS Sets compare arrays by reference, so every composite
 *  key in this project is a string joined on a character that cannot occur in the parts. */
var SEP = String.fromCharCode(0);

/** char to ascending indices in B, minus the autojunk purge. */
function buildB2J_(B) {
  var b2j = new Map();
  for (var i = 0; i < B.length; i++) {
    if (!b2j.has(B[i])) b2j.set(B[i], []);
    b2j.get(B[i]).push(i);
  }
  var n = B.length;
  if (n >= 200) {
    var ntest = Math.floor(n / 100) + 1;
    var popular = [];
    b2j.forEach(function (idxs, elt) { if (idxs.length > ntest) popular.push(elt); });
    for (var p = 0; p < popular.length; p++) b2j.delete(popular[p]);
  }
  return b2j;
}

/**
 * CPython's find_longest_match, including the two extension loops.
 *
 * The extensions are why autojunk does not simply destroy all matching: a purged "popular"
 * character can still be absorbed as an extension of a match that started elsewhere, it just
 * can never SEED one. Dropping the extension loops would change every long-string ratio.
 *
 * `k > bestsize` is strict, so on a tie the earliest-in-a then earliest-in-b match wins.
 */
function findLongestMatch_(A, B, b2j, alo, ahi, blo, bhi) {
  var besti = alo, bestj = blo, bestsize = 0;
  var j2len = new Map();
  for (var i = alo; i < ahi; i++) {
    var newj2len = new Map();
    var idxs = b2j.get(A[i]) || [];
    for (var x = 0; x < idxs.length; x++) {
      var j = idxs[x];
      if (j < blo) continue;
      if (j >= bhi) break;
      var k = (j2len.get(j - 1) || 0) + 1;
      newj2len.set(j, k);
      if (k > bestsize) { besti = i - k + 1; bestj = j - k + 1; bestsize = k; }
    }
    j2len = newj2len;
  }
  while (besti > alo && bestj > blo && A[besti - 1] === B[bestj - 1]) {
    besti--; bestj--; bestsize++;
  }
  while (besti + bestsize < ahi && bestj + bestsize < bhi &&
         A[besti + bestsize] === B[bestj + bestsize]) {
    bestsize++;
  }
  return [besti, bestj, bestsize];
}

/**
 * difflib's ratio(): 2*M/T, M = total size of all matching blocks, T = len(a)+len(b).
 *
 * NOT SYMMETRIC once autojunk engages — the purge is computed on `b` only. Callers must keep
 * the argument order (a = the EARLIER message), which is what dedupeIssues does.
 *
 * Compared by CODE POINT, not UTF-16 code unit: an emoji is one element to Python and two to
 * JavaScript, which would change both T and M on any message containing one.
 */
function seqRatio(a, b) {
  var A = cp_(a), B = cp_(b);
  var T = A.length + B.length;
  if (T === 0) return 1.0;
  var b2j = buildB2J_(B);
  var M = 0;
  var queue = [[0, A.length, 0, B.length]];
  while (queue.length) {
    var q = queue.pop();                          // LIFO, matching CPython's explicit stack
    var r = findLongestMatch_(A, B, b2j, q[0], q[1], q[2], q[3]);
    var i = r[0], j = r[1], k = r[2];
    if (k) {
      M += k;                                     // ratio only needs the SUM of block sizes
      if (q[0] < i && q[2] < j) queue.push([q[0], i, q[2], j]);
      if (i + k < q[1] && j + k < q[3]) queue.push([i + k, q[1], j + k, q[3]]);
    }
  }
  return 2.0 * M / T;
}

/**
 * Find duplicate links among `issues` (each: issue_id, raiser_id, ts_epoch, anchor_channel_id,
 * dc_code, text, kapture_ticket_ids[]). Returns [{issue_id, duplicate_of, method, confidence,
 * evidence}] where issue_id is always the LATER issue.
 *
 * ── WHY BUCKETING ───────────────────────────────────────────────────────────────────────────
 * The naive pairwise scan measured 5.21 s at 3,500 issues and grows with the square. Every
 * heuristic pair must share an author AND fall inside the hour, so bucketing on
 * (author, hour) skips almost every pair without changing a single result.
 */
function dedupeIssues(issues) {
  var D = CFG().dedupe;
  var sorted = issues.slice().sort(function (x, y) { return x.ts_epoch - y.ts_epoch; });
  var byAuthor = new Map(), byTicket = new Map();
  var bucketS = Math.max(D.windowS, 1);

  for (var i = 0; i < sorted.length; i++) {
    var it = sorted[i];
    // Math.floor, not |0 or Math.trunc — they differ for negative epochs.
    var b = Math.floor((it.ts_epoch || 0) / bucketS);
    var key = it.raiser_id + SEP + b;
    if (!byAuthor.has(key)) byAuthor.set(key, []);
    byAuthor.get(key).push(it);
    (it.kapture_ticket_ids || []).forEach(function (tid) {
      if (!byTicket.has(tid)) byTicket.set(tid, []);
      byTicket.get(tid).push(it);
    });
  }

  var pairs = [], seen = new Set();
  function pairKey_(a, b) { return [a, b].sort().join(SEP); }

  // ── pass 1: a shared Kapture ticket id, at ANY distance in time ───────────────────────────
  // A ticket number IS the issue. Two messages quoting one are about one thing however far
  // apart they are, so this pass is deliberately not time-bounded.
  byTicket.forEach(function (group, tid) {
    for (var a = 0; a < group.length; a++) {
      for (var b = a + 1; b < group.length; b++) {
        var k = pairKey_(group[a].issue_id, group[b].issue_id);
        if (seen.has(k)) continue;
        seen.add(k);
        pairs.push({ issue_id: group[b].issue_id, duplicate_of: group[a].issue_id,
                     method: 'kapture_id', confidence: 0.95,
                     evidence: 'shares Kapture ticket ' + tid });
      }
    }
  });

  // ── pass 2: same author, within the hour, compatible DC, similar text ─────────────────────
  var candidates = [];
  byAuthor.forEach(function (group, key) {
    var parts = key.split(SEP);
    var nxt = byAuthor.get(parts[0] + SEP + (Number(parts[1]) + 1)) || [];
    for (var a = 0; a < group.length; a++) {
      // Only bucket b+1 is checked, never b-1 — a pair straddling the boundary is emitted
      // once, from the earlier bucket's side.
      var rest = group.slice(a + 1).concat(nxt);
      for (var b = 0; b < rest.length; b++) candidates.push([group[a], rest[b]]);
    }
  });

  for (var c = 0; c < candidates.length; c++) {
    var A = candidates[c][0], B = candidates[c][1];
    var pk = pairKey_(A.issue_id, B.issue_id);
    if (seen.has(pk)) continue;
    var sameChannel = A.anchor_channel_id === B.anchor_channel_id;
    // Same-channel pairs used to be skipped entirely, which let an identifier-less repost —
    // same sentence, 40 minutes apart, same person — become a second ticket. 0.95 rather than
    // 0.72 because two DIFFERENT problems from one person in one hour is ordinary; near-
    // verbatim is not ambiguous, merely similar is.
    var floor = sameChannel ? D.sameChannelSimilarity : D.textSimilarity;
    var dt = Math.abs(B.ts_epoch - A.ts_epoch);
    if (dt > D.windowS) continue;
    // Only rejects when BOTH sides name a DC and they differ. A missing DC passes.
    if (A.dc_code && B.dc_code && A.dc_code !== B.dc_code) continue;
    var sim = seqRatio(dedupeNorm_(A.text), dedupeNorm_(B.text));
    if (sim >= floor) {
      seen.add(pk);
      pairs.push({
        issue_id: B.issue_id, duplicate_of: A.issue_id,
        method: sameChannel ? 'same_channel_repost' : 'dc_intent_author_window',
        confidence: round_(Math.min(0.9, sim), 3),
        evidence: 'same author, dc=' + (A.dc_code || 'None') + ', ' + Math.round(dt) +
                  's apart, text similarity ' + sim.toFixed(2) + ', across ' +
                  A.anchor_channel_id + '/' + B.anchor_channel_id
      });
    }
  }
  return pairs;
}
