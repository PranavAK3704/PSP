"""Deterministic entity extraction — the part of `intent` that never needed a model.

Intent extraction bundles two jobs: *what does the partner want* (genuinely hard, needs a
model) and *which identifiers did they give us* (strict formats, needs a regex). Only the
first is worth an LLM call. Valmo identifiers are rigidly shaped, so pulling them out is a
lexer problem, and a lexer is exact, instant, free, and — the part that matters for an audit
trail — reproducible.

Every pattern here was derived from the real ticket corpus rather than invented, and each
carries the false-positive trap that made it necessary. The traps are the whole point: the
naive version of each of these is wrong on real data.
"""
from __future__ import annotations

import re
from typing import Any

# ── AWB ────────────────────────────────────────────────────────────────────────
# Valmo AWBs come in TWO shapes: "VL" + 13 digits and "VLR" + 12 digits. Anchored on a word
# boundary and length-bounded because free text contains long digit runs (transaction refs,
# UTRs) that a loose \d+ would eat.
# THE TRAP, and it was live: this used to be `\bVL\d{13}\b` alone, which is *structurally*
# unable to match a VLR awb — the character after "VL" is "R", not a digit, so the pattern
# fails at the third character every time. Measured against the full 1,000,001-row ledger:
# 743,770 VL (74.38%) matched, 256,229 VLR (25.62%) could never match. Worse, VLR is the
# shape partners actually TYPE — the surviving awb tokens in the scrubbed ticket corpus run
# 24 VLR to 2 VL — so the miss was concentrated exactly on live traffic. The two shapes
# together reach 999,999 of 1,000,001 rows (99.9998%); the residue is one 'VALGS'+11 and one
# 'VL'+10, both single rows, and neither is worth loosening a fixed-length pattern for.
# No false-positive risk is added: both branches are a fixed prefix plus a fixed digit run,
# and the alternation is ordered so VL is tried first (VLR can only match via its own branch).
_AWB = re.compile(r"\b(?:VL\d{13}|VLR\d{12})\b", re.I)

# ── hub / location codes ───────────────────────────────────────────────────────
# THE TRAP: hub codes are 3-letter uppercase tokens, and matching that shape against free
# text pulls in ordinary words — an earlier version of this matched "OLD" and "SIR" as hub
# codes and produced 16.7% pure noise. So a bare 3-letter token is NEVER accepted. A hub is
# only recognised with an explicit cue ("hub ABC", "DC: ABC") or from a known-code list the
# caller supplies.
# THE SECOND TRAP, found by measuring: `re.I` applies to the CAPTURE GROUP too, so
# `[A-Z]{2,4}` happily matched lowercase and "hub code is" captured "code" — 32 hits of
# 'CODE' as a hub code. The cue must be case-insensitive while the captured token stays
# case-SENSITIVE. Inline `(?i:...)` scopes the flag to the cue only.
_HUB_CUED = re.compile(
    r"(?i:\b(?:hub|dc|dc\s*code|hub\s*code|branch)\b)[\s:#-]*([A-Z]{2,4}\d{0,4})\b")

# ── phone ──────────────────────────────────────────────────────────────────────
# Indian mobile: optional +91/91/0 prefix, then 6-9 followed by 9 digits. Rejects a 10-digit
# run starting 0-5, which is usually an ID, not a number.
# THE TRAP: without a LEFT boundary this matches the tail of any longer digit run. A Valmo AWB
# is "VL" + 13 digits, so `VL9999999999999` yielded the phone `9999999999` — a number no partner
# ever typed, fabricated out of a shipment id and then written into the concern trace. The
# trailing `\b` alone cannot catch it (the 10-digit tail genuinely ends at a boundary); the fix
# is the lookbehind, which refuses a match preceded by a digit or letter.
_PHONE = re.compile(r"(?<![0-9A-Za-z])(?:\+?91[\s-]?|0)?([6-9]\d{9})\b")

# ── money ──────────────────────────────────────────────────────────────────────
# Requires a currency cue. A bare number is not an amount — "500" in "Transaction ID:500"
# is an id, and treating it as rupees is exactly the misread that fired a false gate in an
# earlier audit run.
_AMOUNT = re.compile(
    r"(?:₹|rs\.?|inr|rupees)\s*([\d][\d,]*(?:\.\d{1,2})?)"
    r"|([\d][\d,]*(?:\.\d{1,2})?)\s*(?:₹|rs\.?|inr|rupees)\b", re.I)

# ── payment cycle ──────────────────────────────────────────────────────────────
_CYCLE = re.compile(
    r"\b(?:cycle|payment\s*cycle|payout\s*cycle)\b[\s:#-]*"
    r"(\d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?|\d{1,2}\s*(?:st|nd|rd|th)?\s*"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:\s*\d{2,4})?)", re.I)

# ── other Valmo identifiers ────────────────────────────────────────────────────
_ENBOLT = re.compile(r"(?i:\b(?:enbolt|enbolt\s*id)\b)[\s:#-]*(\d{8,12})\b")
_FE_ID = re.compile(r"(?i:\b(?:fe|fe\s*id|rider\s*id|pilot\s*id)\b)[\s:#-]*([A-Z0-9]{4,14})\b")
_CREDIT_NOTE = re.compile(r"(?i:\b(?:credit\s*note|cn)\b)[\s:#-]*([A-Z0-9/-]{4,20})\b")
_UTR = re.compile(r"(?i:\b(?:utr|rrn|txn\s*id|transaction\s*id)\b)[\s:#-]*([A-Z0-9]{6,22})\b")
_BAG = re.compile(r"(?i:\b(?:bag|bag\s*id)\b)[\s:#-]*([A-Z0-9-]{6,20})\b")

# Redaction placeholders left by the PII scrubber. Their PRESENCE is signal — it means the
# partner supplied an identifier that was removed downstream — so record it rather than
# silently reporting "no phone given".
_REDACTED = re.compile(r"<(name|num|phone|email)>", re.I)


# Even case-sensitively, a shouty partner writes "HUB CODE IS MISSING" and "IS" is uppercase
# and 2 chars. A capture must therefore also not be an ordinary word. This list is the
# measured residue, not speculation — every entry appeared in a real false positive.
_NOT_AN_ID = {
    "CODE", "IS", "HAS", "AND", "TO", "NAME", "NOT", "THE", "FOR", "ALL", "NEW", "OLD",
    "SIR", "YES", "PLZ", "DAY", "ID", "NO", "MY", "OF", "IN", "ON", "AT", "BE", "AM",
    "ACCOUNT", "PAYMENT", "MOBILE", "VALMO", "DETAILS", "NUMBER", "NUMBERS", "TEAM",
    "PLEASE", "KINDLY", "AMOUNT", "DATE", "CYCLE", "HUB", "PENDING", "STATUS", "ISSUE",
}


def _ok_id(v: str, *, label_anchored: bool = False) -> bool:
    """Reject ordinary words. A real identifier has a digit, or is a short all-caps code
    that is not a dictionary word.

    `label_anchored=True` drops the digit requirement, and ONLY the intake pipeline's Tier A
    passes it. The digit rule was measured on partner Hinglish chat, where it is the only rule
    that holds. Internal ops Slack is a different register: there, most real DC codes are
    purely alphabetic (NQS, IQU, CKH, MFC, PJR), so the digit rule rejects most of the true
    positives. What replaces it there is not shape but POSITION — an explicit "DC"/"LMDC"/
    "FMH"/"Hub" label adjacent to the token. `_NOT_AN_ID` still applies either way, which is
    what keeps "DC CODE IS MISSING" from yielding CODE.

    The default is False, so nothing on the live conversation path changes.
    """
    u = v.upper()
    if u in _NOT_AN_ID:
        return False
    if label_anchored:
        return True
    # THE THIRD TRAP: an "allow short all-caps codes" escape hatch let JISKA, DIDNT and BUT
    # through, because partners write in caps when annoyed and every 5-letter word then looks
    # like a code. Measured on 1,814 real messages, requiring a digit is the only rule that
    # holds — every genuine Valmo identifier has one. Purely alphabetic hub codes are
    # recoverable, but only via `known_hub_codes`, never by guessing from shape.
    return any(ch.isdigit() for ch in v)


def _clean_amount(s: str) -> float:
    return float(s.replace(",", ""))


def extract(text: str, known_hub_codes: set[str] | None = None) -> dict[str, Any]:
    """Pull every identifier out of a partner message.

    `known_hub_codes` closes the one gap a regex cannot: an uncued bare code like "BLR07".
    Pass the hub list from the captain master and those become recognisable safely; without
    it, only cued hubs are returned. Never guess a hub from a bare 3-letter token.
    """
    t = text or ""
    out: dict[str, Any] = {
        "awbs": sorted({m.group(0).upper() for m in _AWB.finditer(t)}),
        "hub_codes": sorted({m.group(1).upper() for m in _HUB_CUED.finditer(t) if _ok_id(m.group(1))}),
        "phones": sorted({m.group(1) for m in _PHONE.finditer(t)}),
        "amounts": [],
        "payment_cycles": sorted({m.group(1) for m in _CYCLE.finditer(t)}),
        "enbolt_ids": sorted({m.group(1).upper() for m in _ENBOLT.finditer(t) if _ok_id(m.group(1))}),
        "fe_ids": sorted({m.group(1).upper() for m in _FE_ID.finditer(t) if _ok_id(m.group(1))}),
        "credit_notes": sorted({m.group(1).upper() for m in _CREDIT_NOTE.finditer(t) if _ok_id(m.group(1))}),
        "utrs": sorted({m.group(1).upper() for m in _UTR.finditer(t) if _ok_id(m.group(1))}),
        "bag_ids": sorted({m.group(1).upper() for m in _BAG.finditer(t) if _ok_id(m.group(1))}),
        "redacted_present": sorted({m.group(1).lower() for m in _REDACTED.finditer(t)}),
    }
    amounts = []
    for m in _AMOUNT.finditer(t):
        raw = m.group(1) or m.group(2)
        try:
            amounts.append(_clean_amount(raw))
        except ValueError:
            pass
    out["amounts"] = sorted(set(amounts))

    if known_hub_codes:
        upper = t.upper()
        found = {h for h in known_hub_codes
                 if re.search(rf"\b{re.escape(h.upper())}\b", upper)}
        out["hub_codes"] = sorted(set(out["hub_codes"]) | found)

    out["any"] = any(v for k, v in out.items() if k not in ("redacted_present", "any"))
    return out


def mandatory_fields_present(entities: dict, required: list[str]) -> dict:
    """Evaluate an SOP's 'mandatory inputs' list against what the partner actually supplied.

    This is SOP branch 1 for every payments disposition — "mandatory_fields_provided" — and
    it is pure set arithmetic. Doing it in code rather than asking a model is both cheaper
    and defensible: the answer is a list, not a judgement.
    """
    missing = [f for f in required if not entities.get(f)]
    return {"provided": not missing, "missing": missing,
            "present": [f for f in required if entities.get(f)]}


# ── DC / hub codes, two tiers — for the Slack intake pipeline ─────────────────────────────────
# `_HUB_CUED` above serves the LIVE conversation path and is deliberately left alone. The two
# functions below serve the batch intake pipeline over internal ops Slack, which is a different
# corpus with a different failure mode, and they fix three things measured on it:
#
#   1. `[A-Z]{2,4}\d{0,4}` is letters-then-digits, so it cannot match a code with a digit in
#      the MIDDLE. L9D, J93, R2F, K6L and T5X — 5 of the 25 codes observed in the channels —
#      are invisible to it. The token class here is `[A-Z0-9]{3}`, exactly three characters.
#   2. `[\s:#-]` is not the separator set people actually type. Slack emphasis characters
#      (* _ ~ `) and the words "and"/"or" sit between codes constantly; two real codes (IQU,
#      UB1) were missed purely because their authors put the bold marker in a different place.
#   3. The scan has to run in BOTH directions. Real messages put the code on either side of the
#      label: "DC Code: NXG", "The *NQS DC landing time", "HY9, IAM, VN4 hub not recive".
#
# THE TOKEN CLASS STAYS CASE-SENSITIVE, and that is the whole game. Applying (?i) to it makes
# "DC landing time" extract LAN — a bug that produced 8 fictional hits out of 18. Only the
# LABEL and the and/or separators are case-insensitive, each scoped with an inline `(?i: … )`
# exactly as `_HUB_CUED` does it.
_DC_LABEL = re.compile(r"(?i:\b(?:DC|LMDC|FMH|Hub)s?\s*(?:Code)?s?\b)")

#: Whitespace, Slack emphasis, ordinary punctuation, and the words and/or. The `(?i: … )` is
#: scoped to and/or ALONE so it can never leak into the token class.
#:
#: `:` and `#` are here even though the brief's separator list omits them — its own canonical
#: example is "DC Code: NXG", which that list cannot parse. `_HUB_CUED` above already used
#: `[\s:#-]`, so the colon was never in doubt in this repo.
_DC_SEP = r"(?:[\s*_~`&,/+.:;#|\-]|(?i:\b(?:and|or)\b))"

#: Exactly three chars, and standalone.
#:
#: The guards are explicit alphanumeric lookarounds, NOT `\b`, and the difference is load-bearing.
#: `_` is a Slack italics marker AND a regex word character, so `\b[A-Z0-9]{3}\b` can never match
#: inside `_UB1_` — there is no word boundary between `_` and `U`. The brief lists `_` as a
#: separator and also requires standalone-word guarding; under `\b` those two requirements are
#: simply incompatible, and the symptom is one silently missing code rather than an error.
#:
#: `(?<![A-Za-z0-9])` treats `_` as a boundary while still refusing `353` inside `3531`, because
#: `1` IS alphanumeric.
_DC_TOKEN = r"(?<![A-Za-z0-9])[A-Z0-9]{3}(?![A-Za-z0-9])"

#: A run of tokens AFTER the label.
#:
#: The leading separator is OPTIONAL, and that is not laxity — it is required for correctness.
#: `_DC_LABEL` ends with `\s*(?:Code)?s?\b`, so for "FMH MFC issue" the label match CONSUMES the
#: space and this run starts already sitting on the token. Requiring a separator first made the
#: forward direction silently extract nothing at all while the backward one worked perfectly —
#: which is exactly the kind of half-working regex that reports a plausible count.
#:
#: Allowing zero separators cannot over-match: the label's trailing `\b` means "DCNQS" never
#: matches as a label in the first place.
_DC_AFTER = re.compile(rf"{_DC_SEP}*{_DC_TOKEN}(?:{_DC_SEP}+{_DC_TOKEN})*")
#: The same run BEFORE the label, anchored to end exactly where the label begins.
_DC_BEFORE = re.compile(rf"(?:{_DC_TOKEN}{_DC_SEP}+)+\Z")
_DC_TOKEN_ONLY = re.compile(_DC_TOKEN)


def extract_dc_tier_a(text: str, denylist: set[str] | None = None) -> dict[str, str]:
    """Label-anchored DC codes. NO REGISTRY NEEDED, and high precision.

    Returns {token: the label text that anchored it}, so a false positive can be traced to the
    label that produced it rather than argued about.

    Precision comes from stopping at the first thing that is neither a separator nor a
    three-character uppercase token. "DC landing time" stops immediately, because "landing" is
    lowercase and therefore not a token.
    """
    deny = denylist or set()
    found: dict[str, str] = {}
    t = text or ""
    for m in _DC_LABEL.finditer(t):
        runs = []
        after = _DC_AFTER.match(t, m.end())
        if after:
            runs.append(after.group(0))
        before = _DC_BEFORE.search(t[:m.start()])
        if before:
            runs.append(before.group(0))
        for run in runs:
            for tok in _DC_TOKEN_ONLY.findall(run):
                if tok in deny or not _ok_id(tok, label_anchored=True):
                    continue
                found.setdefault(tok, m.group(0))
    return found


def extract_dc_tier_b(text: str, registry: set[str], denylist: set[str] | None = None,
                      *, exclude: set[str] | None = None) -> dict[str, str]:
    """Bare DC codes with no label anywhere. THE REGISTRY IS REQUIRED.

    Roughly a third of code-bearing messages carry no label at all, and one of them is the
    cross-posted MX1 message that proves cross-channel duplication — so this tier is on the
    critical path for dedupe, not an optional extra.

    With an EMPTY registry this returns nothing. It must never fall back to a bare-token or
    digit-bearing pass: "342 Tids are coming in Hardstop loss" is word-bounded, three
    characters and digit-bearing, so a digit-bearing heuristic extracts 342 with confidence.
    Only registry membership rejects it — see `candidates()` for reporting what was skipped.

    Registry membership alone is NOT sufficient either. The registry derived from this repo
    contains ALL, AND, DAY, FAD, NEW, OLD, PFB, RVP, SIR, TID and YES as genuine hub codes, so
    the denylist is applied on top.
    """
    if not registry:
        return {}
    deny = denylist or set()
    skip = exclude or set()
    found: dict[str, str] = {}
    for tok in _DC_TOKEN_ONLY.findall(text or ""):
        if tok in skip or tok in deny or tok not in registry:
            continue
        if not _ok_id(tok, label_anchored=True):      # _NOT_AN_ID still applies
            continue
        found.setdefault(tok, "registry")
    return found


def dc_tier_b_candidates(text: str, denylist: set[str] | None = None,
                         *, exclude: set[str] | None = None) -> set[str]:
    """Bare tokens that WOULD be Tier B candidates if a registry were loaded.

    Reported by the pipeline when `config/dc_codes.txt` is absent or empty, so "Tier B was
    skipped" comes with the number of messages it would have looked at instead of being a
    silent gap.
    """
    deny = denylist or set()
    skip = exclude or set()
    return {tok for tok in _DC_TOKEN_ONLY.findall(text or "")
            if tok not in skip and tok not in deny and _ok_id(tok, label_anchored=True)}
