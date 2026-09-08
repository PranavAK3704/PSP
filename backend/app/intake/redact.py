"""PII masking for anything that leaves the machine. Default ON.

── THE RULE ──────────────────────────────────────────────────────────────────────────────────
The local store keeps identifiers INTACT — grouping and dedupe join on them, so redacting at
rest would break the pipeline. Masking happens at the boundary: the xlsx, any export, anything
handed to a person or a service. `redact: true` is the default, so producing an unmasked
artefact requires saying so.

── WHAT IS AND IS NOT PII HERE ───────────────────────────────────────────────────────────────
Mobiles, emails and pilot ids are people. DC codes and Kapture ticket ids are not: a hub is a
facility and a ticket is a record. This matches `app/substrate/dataplane.py`, which states it
plainly — "a hub is a facility, not a person" — and it matters because `scripts/build_valmo_db.py`
takes the opposite view for `entity_id`, so the repo holds two positions. The data plane's is
the reasoned one and is what this follows.

Masks keep the last 4 characters so a human can still tell two numbers apart in a review sheet
without the number being readable.
"""
from __future__ import annotations

import re

_MOBILE = re.compile(r"(?<![A-Za-z0-9])([6-9][0-9]{9})(?![A-Za-z0-9])")
_EMAIL = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_PILOT = re.compile(r"(?i:\b(?:pilot|fe|rider|driver)\s*(?:id|code)?\s*[:#-]?\s*)"
                    r"(?<![A-Za-z0-9])([0-9]{8})(?![A-Za-z0-9])")

#: Entity kinds masked in artefacts. dc_code and kapture_id are deliberately absent.
PII_KINDS = ("mobile", "email", "pilot_id")


def mask_value(kind: str, value: str) -> str:
    if kind not in PII_KINDS or not value:
        return value
    if kind == "email":
        local, _, domain = value.partition("@")
        return f"{local[:1]}***@{domain}" if domain else "***"
    return f"{'*' * max(0, len(value) - 4)}{value[-4:]}"


def mask_text(text: str) -> str:
    """Mask identifiers inside free text — the message body shown in a review sheet."""
    t = _EMAIL.sub(lambda m: f"{m.group(1)[:1]}***@{m.group(2)}", text or "")
    t = _PILOT.sub(lambda m: m.group(0).replace(m.group(1), "****" + m.group(1)[-4:]), t)
    return _MOBILE.sub(lambda m: "******" + m.group(1)[-4:], t)


def mask_tokens(tokens: dict[str, list[str]]) -> dict[str, list[str]]:
    return {k: [mask_value(k, v) for v in vs] for k, vs in tokens.items()}
