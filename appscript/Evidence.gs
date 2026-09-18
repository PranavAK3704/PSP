/**
 * Evidence.gs — "is this a partner/ops issue at all?"
 *
 * ── WHY THIS EXISTS ─────────────────────────────────────────────────────────────────────────
 * Someone posted "can we go to play arena?" in a monitored channel and the pipeline raised a
 * ticket. That was not a missing noise rule — it was an architectural default. Every gate
 * before this one is NEGATIVE: it enumerates things to reject. Anything matching no rejection
 * rule was treated as an issue. You cannot enumerate every off-topic sentence a human might
 * type, so the default has to be inverted.
 *
 * ── THE RULE THAT DOES THE WORK ─────────────────────────────────────────────────────────────
 * "can we go to play arena?" and "can we get the payout released?" have the SAME grammar — a
 * request, a question mark, a first-person plural. The difference is that one of them is about
 * a thing we operate. So:
 *
 *     evidence requires at least one of { an identifier, an ops noun }.
 *     Grammar and urgency only MULTIPLY evidence that exists; they never create it.
 *
 * That one rule is what makes this deterministic instead of a vocabulary arms race.
 *
 * ── THE RISK, STATED ────────────────────────────────────────────────────────────────────────
 * This is the one tier where a real issue can die silently. So every rejection is written to
 * the sheet with its score and its components, and the rejections are meant to be sampled and
 * the threshold tuned against that sample. A threshold set by intuition and never audited is
 * how this fails.
 */

function _evMarkupRe_() {
  return /<@[UW][A-Z0-9]+(?:\|[^>]*)?>|<!subteam\^[A-Z0-9]+(?:\|[^>]*)?>|<[^>]+>/g;
}

/**
 * The replacement for \b, and the reason is load-bearing.
 *
 * Python's \b is defined through \w, and \w excludes Unicode combining marks. So `\bनहीं\b` can
 * NEVER match: नहीं ends in the anusvara ं, which is not a word character, so the trailing \b is
 * unsatisfiable. Hindi terms ending in a consonant worked; terms ending in a matra or anusvara
 * were silently invisible — the lexicon looked fine and matched nothing.
 *
 * So boundaries are explicit lookarounds over "any letter or digit, OR anything in the
 * Devanagari block U+0900–U+097F". The second branch is what catches the marks (\p{M}, not
 * \p{L}). JS \w would be ASCII-only and reintroduce the bug in a new form.
 */
var _WORDISH = '(?:[\\p{L}\\p{N}]|[\\u0900-\\u097F])';

function escapeRe_(s) { return String(s).replace(/[.*+?^${}()|[\]\\\-]/g, '\\$&'); }

/**
 * One alternation per lexicon key.
 *
 * Bounded-vs-literal is decided PER TERM by its first character: a term starting alphanumeric
 * gets word boundaries, anything else is matched literally with none. In the shipped config the
 * literal terms are exactly '@channel' and '@here' — bounding them would make them unmatchable,
 * since @ is not a word character and the left lookbehind would always be satisfied while the
 * term itself could never start at a boundary the way the others do.
 */
function compileTerms_(terms) {
  if (!terms || !terms.length) return null;
  var pats = [];
  for (var i = 0; i < terms.length; i++) {
    var t = String(terms[i]).toLowerCase();
    if (/^[\p{L}\p{N}]/u.test(t)) {
      pats.push('(?<!' + _WORDISH + ')' + escapeRe_(t) + '(?!' + _WORDISH + ')');
    } else {
      pats.push(escapeRe_(t));
    }
  }
  return new RegExp(pats.join('|'), 'giu');
}

/** The five compiled lexicons, built once per execution. */
function EVRE() {
  if (_cache.evre) return _cache.evre;
  var ev = LEX().ev;
  _cache.evre = {
    ops:      compileTerms_(ev.opsNouns),
    problem:  compileTerms_(ev.problemMarkers),
    request:  compileTerms_(ev.requestMarkers),
    urgency:  compileTerms_(ev.urgencyMarkers),
    followup: compileTerms_(ev.followupMarkers)
  };
  return _cache.evre;
}

/**
 * DISTINCT surface forms, lowercased, in order of first occurrence.
 *
 * The counts used downstream are counts of distinct forms, not of occurrences — saying
 * "payout" three times is one piece of evidence, not three.
 */
function hits_(re, text) {
  if (!re) return [];
  var seen = {}, out = [], m;
  re.lastIndex = 0;
  while ((m = re.exec(text)) !== null) {
    var v = m[0].toLowerCase();
    if (!seen[v]) { seen[v] = 1; out.push(v); }
    if (m.index === re.lastIndex) re.lastIndex++;
  }
  return out;
}

function isPunctuationOnly_(s) {
  var t = String(s || '').trim();
  return t !== '' && /^[^\p{L}\p{N}]+$/u.test(t);
}

/**
 * Score one message. Returns {decision, score, ops, problem, request, urgency, followup, reasons}.
 *
 * decision ∈ { issue, weak, orphan, not_an_issue } — plus `reply`, which is assigned upstream in
 * Pipeline.gs and never computed here.
 *
 * The score lattice, and why request is 0.4 rather than a round number:
 *
 *     identifier only .................. 1.0  issue
 *     1 ops noun ....................... 0.6  weak
 *     2+ ops nouns ..................... 1.2  issue
 *     1 ops noun + request ............. 1.0  issue   ← lands EXACTLY on the threshold
 *     grammar/urgency, no ops, no id ... 0.0  not_an_issue
 *
 * "can we get the payout released?" is a real request about a real thing and must qualify. It
 * scored 0.6 and came out `weak` until that fourth row was measured and the weight corrected.
 */
function evidenceScore(text, entityKinds) {
  var C = CFG().evidence, R = EVRE();
  var body = String(text || '').replace(_evMarkupRe_(), ' ');   // markup out, punctuation kept
  var kinds = entityKinds || [];

  var ops      = hits_(R.ops, body);
  var problem  = hits_(R.problem, body);
  var request  = hits_(R.request, body);
  var urgency  = hits_(R.urgency, body);
  var followup = hits_(R.followup, body);

  // ── BASE: the only two things that can make the score non-zero ────────────────────────────
  var base = 0.0, reasons = [];
  if (kinds.length) {
    base += C.w.identifier;                       // once, however many kinds are present
    reasons.push('identifier:' + kinds.slice().sort().join(','));
  }
  if (ops.length) {
    base += C.w.opsNoun * Math.min(ops.length, 2);
    reasons.push('ops:' + ops.slice(0, 3).join(','));
  }

  // ── MULTIPLIERS: only ever applied to evidence that already exists ────────────────────────
  var total = base;
  if (base > 0) {
    // This order is fixed: it decides the order of the reason string, which is diffed
    // between runs to see what changed about a decision.
    var mults = [['problem', problem], ['request', request], ['urgency', urgency]];
    for (var i = 0; i < mults.length; i++) {
      var name = mults[i][0], h = mults[i][1];
      if (h.length) {
        total += C.w[name] * Math.min(h.length, 2);
        reasons.push(name + ':' + h.slice(0, 2).join(','));
      }
    }
  } else if (request.length || problem.length || urgency.length) {
    // The "play arena" line ends up here: perfect request grammar, nothing we operate.
    reasons.push('grammar_only:no_identifier_and_no_ops_noun');
  }

  var decision;
  if (total >= C.issue) {
    decision = 'issue';
  } else if (total >= C.weak) {
    // Kept and flagged rather than dropped: a human glance is cheap, a dropped real issue is not.
    decision = 'weak';
  } else if (followup.length || isPunctuationOnly_(body)) {
    // An orphan carries no evidence but IS a follow-up — the context lives in a message the
    // pipeline never linked, which is exactly the case a human needs to see. Routed to the
    // review queue unassigned, never binned.
    decision = 'orphan';
    reasons.push(followup.length ? 'followup:' + followup.slice(0, 2).join(',')
                                 : 'followup:punctuation_only');
  } else {
    decision = 'not_an_issue';
  }

  return { decision: decision, score: round_(total, 3), ops: ops, problem: problem,
           request: request, urgency: urgency, followup: followup,
           reasons: reasons.join('; ') || 'no positive signal' };
}
