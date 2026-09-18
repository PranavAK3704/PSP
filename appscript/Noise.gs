/**
 * Noise.gs — the noise gate and the informational classifier. Two INDEPENDENT axes.
 *
 * ── GATED IS NOT DELETED ────────────────────────────────────────────────────────────────────
 * Every rule here MARKS a message. Nothing is removed from raw_messages, because "what did the
 * filter throw away" has to be answerable — and because a gate rule that is quietly wrong is
 * indistinguishable from one that is right unless you can list what it caught.
 *
 * ── THE ONE HARD EXCEPTION ──────────────────────────────────────────────────────────────────
 * An empty-text message carrying an attachment is NEVER gated, whatever the rules say. ~5% of
 * records are exactly this: the whole issue is a screenshot and the text is "". Without the
 * exception an emptiness rule silently deletes real issues, and the report shows a healthy
 * filtered count while the register is missing rows.
 */

// Four branches: user mention, broadcast, usergroup, then a catch-all for any <…> with no >
// inside — which eats links, <tel:…> and anything else angle-bracketed. The first three are
// redundant with the catch-all for MATCHING purposes; they are kept because isMentionOnly()
// uses the same expression and reads as its own definition.
function _markupRe_() {
  return /<@[UW][A-Z0-9]+(?:\|[^>]*)?>|<!(?:channel|here|everyone)>|<!subteam\^[A-Z0-9]+(?:\|[^>]*)?>|<[^>]+>/g;
}

// JS \w is ASCII-only; Python's is Unicode. A naive port of Python's [^\w\s]|_ therefore strips
// every Devanagari character out of normalise() — the message survives as an empty string and
// gets gated as emoji_only. \p{L}\p{N} with the u flag is the fix, and it needs the V8 runtime.
function _nonProseRe_() { return /[^\p{L}\p{N}\s]|_/gu; }

/**
 * Lowercased, markup-free, punctuation-free, whitespace-collapsed. Exactly three substitutions.
 *
 *   "Okk!!" → "okk"      "+1" → "1"       "fyi." → "fyi"
 *   "???"   → ""         "👍"  → ""        "<@U09HH8QKZ43>" → ""
 *
 * Markup becomes a SPACE, not an empty string — "<@U1>down" must not become "down" attached to
 * nothing, and word counting downstream depends on the separator surviving.
 */
function normalise(text) {
  var t = String(text || '').replace(_markupRe_(), ' ');
  t = t.replace(_nonProseRe_(), ' ');
  return t.replace(/\s+/g, ' ').trim().toLowerCase();
}

/** Raw text is non-blank but becomes blank once markup alone is removed. Punctuation is NOT
 *  removed here, so "<@U1> ?" is false — the ? survives and it is a real (if terse) ping. */
function isMentionOnly(text) {
  var raw = String(text || '');
  if (!raw.trim()) return false;
  return !raw.replace(_markupRe_(), ' ').trim();
}

/**
 * The earliest-starting term in `terms` that appears anywhere in `low`, as {index, term}.
 *
 * PLAIN SUBSTRING SEARCH, deliberately. This means `rain` matches inside `training` and `app`
 * inside `Happy` — both are real, measured behaviour of the shipped classifier, and the share
 * numbers in the reports were computed with them. Do NOT "improve" this to a word-bounded
 * regex: it would change the numbers without anybody asking for them to change.
 *
 * Ties go to whichever term appears first in the list, because only a strictly smaller index
 * replaces the incumbent.
 */
function firstHit_(low, terms) {
  var best = null;
  for (var i = 0; i < terms.length; i++) {
    var idx = low.indexOf(terms[i]);
    if (idx >= 0 && (best === null || idx < best.index)) best = { index: idx, term: terms[i] };
  }
  return best;
}

/**
 * Both axes for one message.
 *
 * Returns {gated, gateRule, informational, informationalRule, borderline}.
 *
 * The two axes are independent and the informational one runs UNCONDITIONALLY — a gated
 * message still gets classified, because the informational share is reported over a
 * denominator the gate also feeds and the two counts have to be derivable from one pass.
 */
function noiseClassify(text, subtype, hasMedia) {
  var L = LEX().noise;
  var raw = String(text || '');
  var norm = normalise(raw);
  var out = { gated: false, gateRule: null, informational: false,
              informationalRule: null, borderline: false };

  // ── axis A: the gate ──────────────────────────────────────────────────────────────────────
  if (raw.trim() === '' && hasMedia) {
    // The hard exception. Note it leaves gated FALSE and records a diagnostic instead — the
    // rule loop is skipped entirely so no rule can claim it.
    out.gateRule = 'kept:empty_text_with_attachment';

  } else if (subtype && L.subtypes.indexOf(subtype) >= 0) {
    out.gated = true;
    out.gateRule = 'subtype:' + subtype;

  } else {
    for (var r = 0; r < L.rules.length; r++) {
      var rule = L.rules[r];

      if (norm && rule.exact && rule.exact.indexOf(norm) >= 0) {
        out.gated = true; out.gateRule = rule.name; break;
      }
      if (rule.mentionOnly && isMentionOnly(raw)) {
        out.gated = true; out.gateRule = rule.name; break;
      }
      // Four conjuncts. The last one is the carve-out that keeps "???" OUT of the gate: a bare
      // punctuation ping is not an emoji reaction, it is somebody chasing a reply, and Evidence
      // calls it an orphan and routes it to a human.
      if (rule.emojiOnly && raw.trim() !== '' && norm === '' && !hasMedia &&
          !/^[?!.\s]+$/.test(raw.trim())) {
        out.gated = true; out.gateRule = rule.name; break;
      }
      if (rule.allWordsIn && norm) {
        var words = norm.split(' ');
        var cap = rule.maxWords === undefined ? 7 : rule.maxWords;
        if (words.length <= cap) {
          var all = true;
          for (var w = 0; w < words.length; w++) {
            if (rule.allWordsIn.indexOf(words[w]) < 0) { all = false; break; }
          }
          if (all) { out.gated = true; out.gateRule = rule.name; break; }
        }
      }
    }
  }

  // ── axis B: informational ─────────────────────────────────────────────────────────────────
  // Matched against the RAW lowercased text, not the normalised string — these are phrases
  // ("planned downtime", "no action required") and normalisation would not preserve them
  // against punctuation the way a plain substring search does.
  var low = raw.toLowerCase();
  var wHit = firstHit_(low, L.weather);
  var cHit = firstHit_(low, L.competing);
  var aHit = firstHit_(low, L.announce);

  if (wHit) {
    if (cHit && cHit.index < wHit.index) {
      // An ops topic was named BEFORE the weather. If the message also ASKS for something it is
      // not a callout at all — a weather callout does not ask for anything, and that is what
      // makes it a callout. Measured failure this fixes: "Dear losses team … please reverse -
      // rain has made it extremely difficult" was held back and never became a ticket.
      var ask = firstHit_(low, L.asks);
      if (ask) {
        out.informational = false;
        out.informationalRule = 'kept:' + cHit.term + '_then_' + wHit.term +
                                '_with_ask(' + ask.term + ')';
        return out;                       // early exit: nothing below can change this
      }
      out.informational = true;
      out.informationalRule = 'loose:' + wHit.term + '(after ' + cHit.term + ')';
      out.borderline = true;              // borderline is exactly loose-minus-strict
    } else {
      out.informational = true;
      out.informationalRule = 'strict:' + wHit.term;
    }
  } else if (aHit) {
    out.informational = true;
    out.informationalRule = 'announcement:' + aHit.term;
  }
  return out;
}
