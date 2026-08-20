"""The data-plane boundary: what code may send to the model, and what it never may.

THE RULE
    Code queries the data. Code composes the answer. The model receives the composed
    answer and a row count — never the rows.

The model's job here is to ROUTE (which named query, which tool, which SOP) and to
WORD a reply. Neither job needs a ledger row. Handing it rows anyway costs three
things at once, which is why this boundary is worth enforcing rather than merely
intending:

  1. Governance — a real `partner_id` and a real Meesho employee name (the
     `metadata_attribution_marked_by` column is populated with first names) crossing to
     a third-party API is the same class of exposure PRODUCTION_DELTA already tracks for
     the loss build. Projecting at the source closes it; redacting downstream does not.
  2. Prompt injection — prism_provider already reasons about the outbound direction
     ("no SQL anywhere in the request path, so prompt injection can't reach the data
     lake"). This is the mirror image: text pulled from a ticket remark or a metadata
     column cannot carry instructions INTO the model if it never reaches the model.
  3. Cost — a tool result lands in `sess.contents` PERMANENTLY and is resent on every
     later step and every later turn. Raw rows are the single largest contributor to
     that growth. Composing removes the cause rather than trimming the symptom.

WHAT COUNTS AS A LEAK — the precise formulation
    Not "no identifier may ever appear". An AWB the captain typed is already in the
    model's context: it is in their own message. Echoing it back in an answer is not new
    exposure, and a rule that forbade it would make an honest reply ("the ₹244 debit on
    VLR0823… has a credit note") impossible to write.

    The leak is an identifier the model would NOT otherwise have had. So the check is a
    SUBSET test: every identifier-shaped token in an outbound tool result must already
    appear in something the captain themselves said this conversation. Anything else —
    another shipment's AWB pulled from a row, an 11-digit partner id, a phone number
    from a profile — is a violation.

WHAT THIS MODULE DOES AND DOES NOT CATCH
    It catches STRUCTURAL shapes: AWBs, Indian mobile numbers, 11-digit partner ids.
    Those are the ones that can slip through composed prose by accident.

    It cannot catch a human NAME — `naveen` is indistinguishable from any other word.
    Names are excluded by CONSTRUCTION instead: the projections in tools.py return a
    fixed, hand-written key set computed from aggregates, so a name has no route to the
    payload at all. The offline harness (scripts/check_dataplane.py) closes the loop by
    asserting that real `marked_by` values sampled from the DB are absent from a real
    tool result — a corpus check belongs offline, where DB access is free.

Hub codes (`entity_id`, e.g. "KDK") ARE permitted. A hub is a facility, not a person; it
is what routes an escalation to the owning team, and the Data Foundation panel already
publishes hub codes on exactly this reasoning ("no partner is identifiable from this
view"). Nothing about a 3-letter facility code identifies a captain.
"""
from __future__ import annotations

import re

# ── the identifier shapes ────────────────────────────────────────────────────────────
# Deliberately the SAME awb pattern as algo/entities.py (both shapes: VL+13 and VLR+12).
# If the two ever drift, this guard silently stops seeing a quarter of all AWBs, which is
# precisely the bug the lexer had — so the shared shape is a comment-level contract:
# change one, change both. (They are not imported from one another on purpose: the guard
# must keep working even if the lexer is mid-edit.)
_PATTERNS = (
    ("awb", re.compile(r"\b(?:VL\d{13}|VLR\d{12})\b", re.I)),
    # Indian mobile. Same left-boundary lookbehind as the lexer, for the same reason: without
    # it, the 10-digit tail of a 13-digit AWB reads as a phone number that nobody ever typed.
    ("phone", re.compile(r"(?<![0-9A-Za-z])[6-9]\d{9}(?![0-9])")),
    # partner_id is 11 digits in the real ledger (all sampled rows: 11, all-digit, '2001…').
    # Bounded on both sides so it cannot match a slice of a longer run.
    ("partner_id", re.compile(r"(?<!\d)\d{11}(?!\d)")),
)

# Keys whose values are structurally identifiers and are checked, but which are NOT leaks
# when they carry the captain's own id — the conversation is already scoped to them.
_SELF_KEYS = {"captain_id", "partner_id"}


def identifiers(obj, _out: dict | None = None, _key: str = "") -> dict:
    """Every identifier-shaped token anywhere in a nested structure → {token: kind}.

    Walks dicts, lists, tuples and strings; numbers are stringified first, because an
    11-digit partner id arriving as an int is the same exposure as one arriving as text.
    """
    out = {} if _out is None else _out
    if isinstance(obj, dict):
        for k, v in obj.items():
            identifiers(v, out, str(k))
        return out
    if isinstance(obj, (list, tuple)):
        for v in obj:
            identifiers(v, out, _key)
        return out
    if obj is None or isinstance(obj, bool):
        return out
    text = obj if isinstance(obj, str) else str(obj)
    for kind, pat in _PATTERNS:
        for m in pat.finditer(text):
            tok = m.group(0).upper()
            # a self-id under its own key is the conversation's own subject, not a disclosure
            if not (kind == "partner_id" and _key in _SELF_KEYS):
                out.setdefault(tok, kind)
    return out


def supplied(text: str) -> set[str]:
    """Identifier tokens the CAPTAIN supplied — the allow-list for the subset test."""
    return set(identifiers(text or "").keys())


def violations(payload, allowed: set[str]) -> list[dict]:
    """Identifiers in an outbound payload that the captain never supplied.

    Returns a list of {token, kind} — empty means the payload is clean. Pure function:
    no I/O, no logging, no raising, so a caller can run it on the hot path.
    """
    found = identifiers(payload)
    return [{"token": t, "kind": k} for t, k in found.items() if t not in (allowed or set())]


def redact(payload, allowed: set[str]):
    """Replace unsupplied identifiers with a shape-preserving placeholder.

    The LAST line of defence, not the design. The design is that the projections in
    tools.py never produce these values; this exists so that if one ever does — a new
    tool, a widened query, a changed provider — the failure mode is a visibly redacted
    token rather than a real identifier crossing the wire. Shape is preserved
    (`<awb-redacted>`) so a reader can tell what was removed and why.

    Driven by `violations()`, deliberately, so what gets masked is exactly what got
    flagged. Two bugs came from not doing that:
      · `identifiers()` stringifies before matching, so it FLAGS an 11-digit int; the old
        redact bailed out on anything non-str and silently left it in place, while the
        caller logged "redacted 3 identifiers". A guard that reports a redaction it did
        not perform is worse than no guard.
      · `identifiers()` allow-lists the captain's own id under its own key; the old redact
        did not know that and masked the conversation's own subject.
    """
    leaked = {v["token"] for v in violations(payload, allowed)}
    if not leaked:
        return payload
    return _mask(payload, leaked)


def _mask(obj, leaked: set[str]):
    if isinstance(obj, dict):
        return {k: _mask(v, leaked) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask(v, leaked) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_mask(v, leaked) for v in obj)
    if obj is None or isinstance(obj, bool):
        return obj
    if not isinstance(obj, str):
        # An int/float that IS a leaked identifier becomes the placeholder string. The type
        # changes, which is correct: a redacted value is not a number any more, and leaving
        # it numeric to preserve the shape would leave the identifier intact.
        return f"<redacted>" if str(obj).upper() in leaked else obj
    out = obj
    for kind, pat in _PATTERNS:
        out = pat.sub(lambda m: f"<{kind}-redacted>" if m.group(0).upper() in leaked
                      else m.group(0), out)
    return out
