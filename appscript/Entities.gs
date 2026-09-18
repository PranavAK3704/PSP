/**
 * Entities.gs — DC/hub codes in two tiers, plus the five pattern-matched identifier kinds.
 *
 * ── THE ONE THING THAT WILL BREAK THIS PORT ─────────────────────────────────────────────────
 * Python writes `(?i:…)` — a case-insensitive group INSIDE an otherwise case-sensitive pattern.
 * JavaScript has no scoped inline flags, in any engine. The tempting fix is to add the global
 * `i` flag instead. That is the exact measured bug: with `i` applied to the whole pattern,
 * "DC landing time" extracts LAN, and a run that should have found 10 real codes reported 18,
 * eight of them fictional.
 *
 * The rule is: the LABEL and the and/or separators are case-insensitive. The TOKEN CLASS IS
 * NOT. So every case-insensitive piece below is hand-expanded to [Dd][Cc]-style classes, and
 * no regex in this file carries an `i` flag. Tests.gs pins six cases, including the two that
 * only fail when somebody "simplifies" this.
 *
 * ── AND THE ONE THAT ALREADY BIT US ─────────────────────────────────────────────────────────
 * Token guards are (?<![A-Za-z0-9]) / (?![A-Za-z0-9]), never \b. `_` is both a Slack italics
 * marker and a regex word character, so \b[A-Z0-9]{3}\b can never match inside _UB1_. The
 * explicit alphanumeric lookarounds treat _ as a boundary while still refusing 353 inside 3531.
 * Lookbehind needs the V8 runtime — on the legacy Rhino runtime this file fails to compile,
 * which is the good failure mode.
 */

// Label: DC / LMDC / FMH / Hub, optionally plural, optionally followed by "Code(s)".
// Alternation order preserved from the source — leftmost-first matters at a shared prefix.
function _dcLabelRe_() {
  return /\b(?:[Dd][Cc]|[Ll][Mm][Dd][Cc]|[Ff][Mm][Hh]|[Hh][Uu][Bb])[sS]?\s*(?:[Cc][Oo][Dd][Ee])?[sS]?\b/g;
}

// Separator between codes in a run. The Slack emphasis characters * _ ~ ` are in here because
// real authors put bold markers around codes — two real codes, IQU and UB1, were missed until
// they were added. The trailing \- is last in the class so it is a literal hyphen.
var _DC_SEP_SRC = "(?:[\\s*_~`&,/+.:;#|\\-]|\\b(?:[Aa][Nn][Dd]|[Oo][Rr])\\b)";

// CASE-SENSITIVE. This is the load-bearing line of the whole file.
var _DC_TOKEN_SRC = "(?<![A-Za-z0-9])[A-Z0-9]{3}(?![A-Za-z0-9])";

// Leading separator is * (zero allowed), not +. _dcLabelRe_ ends with \s*(?:Code)?s?\b and
// CONSUMES the trailing space, so for "FMH MFC issue" the label match ends sitting directly on
// MFC. Requiring one separator made the forward scan silently extract nothing.
function _dcAfterRe_() {
  return new RegExp(_DC_SEP_SRC + '*' + _DC_TOKEN_SRC +
                    '(?:' + _DC_SEP_SRC + '+' + _DC_TOKEN_SRC + ')*', 'y');
}
// Python anchors this with \Z against text[:label.start]. JS: `$` with NO m flag, same slice.
function _dcBeforeRe_() {
  return new RegExp('(?:' + _DC_TOKEN_SRC + _DC_SEP_SRC + '+)+$');
}
function _dcTokenOnlyRe_() { return new RegExp(_DC_TOKEN_SRC, 'g'); }

/** Every match of a global regex, as an array of full-match strings. */
function findAll_(re, text) {
  var out = [], m;
  re.lastIndex = 0;
  while ((m = re.exec(text)) !== null) {
    out.push(m[0]);
    if (m.index === re.lastIndex) re.lastIndex++;   // zero-width guard
  }
  return out;
}

/**
 * Is this token allowed to be an identifier?
 *
 * Both DC tiers pass labelAnchored=true, which DROPS the digit requirement. On partner Hinglish
 * chat a digit is the only thing that holds, but on internal ops Slack most real DC codes are
 * purely alphabetic — NQS, IQU, CKH, MFC, PJR — so the digit rule would reject most true
 * positives. What replaces shape is POSITION (an adjacent label) or REGISTRY MEMBERSHIP.
 */
function okId_(v, labelAnchored) {
  if (LEX().notAnId.indexOf(String(v).toUpperCase()) >= 0) return false;
  if (labelAnchored) return true;
  return /[0-9]/.test(v);
}

/**
 * Tier A — label-anchored. Precision comes from POSITION, and no registry is needed.
 *
 * Scans BOTH directions, because real messages put the code on either side of the label
 * ("DC Code: NXG" and "The *NQS DC landing time"). The forward run stops at the first thing
 * that is neither a separator nor a 3-char uppercase token, which is why "DC landing time"
 * yields nothing — `landing` is lowercase and therefore not a token.
 *
 * Returns a Map of token → the label text that found it. First label wins.
 */
function dcTierA(text, denylist) {
  var found = new Map();
  var t = String(text || '');
  var label = _dcLabelRe_(), m;
  while ((m = label.exec(t)) !== null) {
    var runs = [];

    var after = _dcAfterRe_();
    after.lastIndex = m.index + m[0].length;        // sticky: anchored, not searched
    var a = after.exec(t);
    if (a) runs.push(a[0]);

    var b = _dcBeforeRe_().exec(t.slice(0, m.index));
    if (b) runs.push(b[0]);

    for (var i = 0; i < runs.length; i++) {
      var toks = findAll_(_dcTokenOnlyRe_(), runs[i]);
      for (var j = 0; j < toks.length; j++) {
        var tok = toks[j];
        if (denylist.has(tok)) continue;
        if (!okId_(tok, true)) continue;
        if (!found.has(tok)) found.set(tok, m[0]);
      }
    }
    if (m.index === label.lastIndex) label.lastIndex++;
  }
  return found;
}

/**
 * Tier B — bare token anywhere, gated on registry membership.
 *
 * An EMPTY REGISTRY RETURNS NOTHING. It is never a fallback heuristic, and the rule it must not
 * break is "only the registry rejects it": in "342 Tids are coming in Hardstop loss", 342 is
 * word-bounded, exactly three characters and digit-bearing, so every shape heuristic extracts
 * it confidently. Only registry membership says no.
 *
 * Membership alone is not sufficient either — the registry genuinely contains ALL, AND, DAY,
 * NEW, OLD, SIR, TID and YES as real hub codes, which is what the denylist is layered on for.
 */
function dcTierB(text, registry, denylist, exclude) {
  var found = new Map();
  if (!registry || registry.size === 0) return found;
  var toks = findAll_(_dcTokenOnlyRe_(), String(text || ''));
  for (var i = 0; i < toks.length; i++) {
    var tok = toks[i];
    if (exclude && exclude.has(tok)) continue;      // tier A already claimed it
    if (denylist.has(tok)) continue;
    if (!registry.has(tok)) continue;
    if (!okId_(tok, true)) continue;
    if (!found.has(tok)) found.set(tok, 'registry');
  }
  return found;
}

/**
 * The five pattern kinds, in config/entities.yaml order.
 *
 * All three numeric kinds use alphanumeric lookarounds rather than \b, and that is what makes
 * them MUTUALLY EXCLUSIVE. Measured on one fixture line —
 *   "Registered mobile 9900000001, ticket 4788325630026, waybill VL0084870753799."
 * — a bare [6-9]\d{9} finds a "mobile" inside the 13-digit Kapture id, and a bare \d{12,13}
 * finds a "Kapture id" inside the waybill. Both produce a plausible count and a wrong answer.
 */
function PATTERNS() {
  if (_cache.patterns) return _cache.patterns;
  _cache.patterns = [
    { kind: 'mobile',     group: 0, re: /(?<![A-Za-z0-9])[6-9][0-9]{9}(?![A-Za-z0-9])/g },
    { kind: 'kapture_id', group: 0, re: /(?<![A-Za-z0-9])[0-9]{12,13}(?![A-Za-z0-9])/g },
    // Case-SENSITIVE here, matching the source config.
    { kind: 'waybill',    group: 0, re: /(?<![A-Za-z0-9])(?:VL[0-9]{13}|VLR[0-9]{12})(?![A-Za-z0-9])/g },
    // CONTEXTUAL: a bare 8-digit run is a date, an amount or half an order id far more often
    // than it is a pilot, so a cue word is required. The cue is the only case-insensitive part,
    // hand-expanded because (?i:…) does not exist here. [:#-] keeps the hyphen last = literal.
    { kind: 'pilot_id',   group: 1,
      re: /(?:\b(?:[Pp][Ii][Ll][Oo][Tt]|[Ff][Ee]|[Rr][Ii][Dd][Ee][Rr]|[Dd][Rr][Ii][Vv][Ee][Rr]|[Dd][Ee])\s*(?:[Ii][Dd]|[Cc][Oo][Dd][Ee])?\s*[:#-]?\s*)(?<![A-Za-z0-9])([0-9]{8})(?![A-Za-z0-9])/g },
    { kind: 'email',      group: 0, re: /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g }
  ];
  return _cache.patterns;
}

/**
 * Every identifier in one message, as [{kind, value, tier, matchedBy}].
 *
 * Tier A runs first and its tokens are EXCLUDED from tier B, so each code is attributed to the
 * evidence that actually found it — "label:DC Code" reads differently from "registry" when you
 * are asking why a ticket has the hub it has.
 */
function extractEntities(text, registry, denylist) {
  var out = [];
  var tierA = dcTierA(text, denylist);
  tierA.forEach(function (label, tok) {
    out.push({ kind: 'dc_code', value: tok, tier: 'A', matchedBy: 'label:' + String(label).trim() });
  });
  var exclude = new Set(Array.from(tierA.keys()));
  dcTierB(text, registry, denylist, exclude).forEach(function (why, tok) {
    out.push({ kind: 'dc_code', value: tok, tier: 'B', matchedBy: why });
  });

  var pats = PATTERNS();
  for (var i = 0; i < pats.length; i++) {
    var p = pats[i], m;
    p.re.lastIndex = 0;
    while ((m = p.re.exec(String(text || ''))) !== null) {
      out.push({ kind: p.kind, value: m[p.group], tier: null, matchedBy: 'pattern:' + p.kind });
      if (m.index === p.re.lastIndex) p.re.lastIndex++;
    }
  }
  return out;
}

/** The DC registry and denylist, read once per execution from their hidden tabs. */
function DCREG() {
  if (_cache.dcreg) return _cache.dcreg;
  var codes = new Set(), deny = new Set();
  readTab_(CFG().tabs.dcCodes).rows.forEach(function (r) {
    var v = String(r[0] || '').trim().toUpperCase(); if (v) codes.add(v);
  });
  readTab_(CFG().tabs.dcDeny).rows.forEach(function (r) {
    var v = String(r[0] || '').trim().toUpperCase(); if (v) deny.add(v);
  });
  _cache.dcreg = { registry: codes, denylist: deny };
  return _cache.dcreg;
}
