"""Pre-emit validation — every field typed, every failure a flag. NO LLM, NO NETWORK.

A ticket that reaches a support system should not need re-reading to be acted on, and it
should never claim more than it can prove. So each identifier is emitted as a typed record
carrying its own verdict, and anything that does not check out becomes a FLAG on the ticket
rather than a silent pass or a dropped field.

── WHY FLAGS RATHER THAN REJECTIONS ──────────────────────────────────────────────────────────
A DC code absent from the registry is not necessarily wrong — the registry is a derived seed
that is missing 5 of the 25 codes seen in the channels. Dropping the code would lose real
information; raising nothing would hide a real problem. So the value stays, the flag says the
registry does not know it, and a human decides. The same logic applies throughout: this stage
annotates, it does not veto.

── WHAT IT CANNOT CHECK, AND SAYS SO ─────────────────────────────────────────────────────────
`kapture_id_exists` is deliberately left None rather than False. There is no Kapture API in this
repo and tickets.db is a stale offline dump, so "not in the dump" does not mean "not a real
ticket". A False there would be a confident wrong answer; None is an honest one.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]

_MOBILE = re.compile(r"^[6-9][0-9]{9}$")
_KAPTURE = re.compile(r"^[0-9]{12,13}$")
_WAYBILL = re.compile(r"^(?:VL[0-9]{13}|VLR[0-9]{12})$")
_PILOT = re.compile(r"^[0-9]{8}$")
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

#: Identifiers a disposition genuinely needs before anyone can act on it. Absence is a FLAG,
#: not a rejection — a partner who forgot their waybill still has a real problem, and the
#: flag is what tells the desk to ask for it instead of guessing.
REQUIRED_BY_DISPOSITION = {
    "payment_not_received":   ["mobile"],
    "payment_reconciliation": ["mobile"],
    "hardstop_loss":          ["waybill"],
    "shortage_loss":          ["waybill"],
    "cod_shortfall":          ["dc_code"],
    "cod_pendency":           ["dc_code"],
    "load_planning":          ["dc_code"],
    "capacity_panel_issue":   ["dc_code"],
    "invoice_request":        ["dc_code"],
}

_VALIDATORS = {
    "mobile":     lambda v: bool(_MOBILE.match(v)),
    "kapture_id": lambda v: bool(_KAPTURE.match(v)),
    "waybill":    lambda v: bool(_WAYBILL.match(v)),
    "pilot_id":   lambda v: bool(_PILOT.match(v)),
    "email":      lambda v: bool(_EMAIL.match(v)),
}


def typed_entities(tokens: dict[str, list[str]], registry: set[str],
                   denylist: set[str]) -> dict[str, list[dict]]:
    """Turn `{kind: [value]}` into `{kind: [{value, valid, ...}]}` — one fact per record."""
    out: dict[str, list[dict]] = {}
    for kind, values in sorted((tokens or {}).items()):
        recs = []
        for v in values:
            r: dict = {"value": v}
            if kind == "dc_code":
                r["in_registry"] = v in registry
                r["denylisted"] = v in denylist
                r["valid"] = len(v) == 3 and v.isalnum() and v.upper() == v
            else:
                r["valid"] = _VALIDATORS.get(kind, lambda _: True)(v)
            if kind == "kapture_id":
                # No Kapture API and a stale dump — None is the honest answer, not False.
                r["exists"] = None
            recs.append(r)
        out[kind] = recs
    return out


def run_checks(draft_fields: dict, entities: dict[str, list[dict]],
               known_dispositions: set[str] | None = None) -> list[str]:
    """Every failure as a flag. Order is stable so two runs diff cleanly."""
    flags: list[str] = []

    for kind, recs in sorted(entities.items()):
        for r in recs:
            if not r.get("valid", True):
                flags.append(f"malformed_{kind}:{r['value']}")
            if kind == "dc_code":
                if r.get("denylisted"):
                    flags.append(f"dc_code_denylisted:{r['value']}")
                elif not r.get("in_registry"):
                    # The registry is a derived seed missing 5 of 25 observed codes, so this
                    # means "we cannot confirm it", not "it is wrong".
                    flags.append(f"dc_code_not_in_registry:{r['value']}")

    disp = draft_fields.get("disposition")
    if disp and disp != "NOVEL":
        if known_dispositions is not None and disp not in known_dispositions:
            flags.append(f"disposition_unknown:{disp}")
        for need in REQUIRED_BY_DISPOSITION.get(disp, []):
            if not entities.get(need):
                flags.append(f"missing_required_{need}_for_{disp}")
    elif disp == "NOVEL":
        flags.append("disposition_novel_needs_human")

    if not any(entities.get(k) for k in ("dc_code", "mobile", "waybill", "kapture_id",
                                         "pilot_id", "email")):
        flags.append("no_actionable_identifier")

    if draft_fields.get("first_response_latency_s") is None and draft_fields.get("reply_count"):
        flags.append("replies_exist_but_no_qualifying_first_response")

    return flags


def kapture_ids_seen(ids: list[str], db: Path | None = None) -> dict[str, bool]:
    """Best-effort lookup against the offline dump. Absent file -> empty, never False."""
    db = db or (_BACKEND / "data" / "tickets.db")
    if not ids or not db.exists():
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        q = ",".join("?" * len(ids))
        found = {r[0] for r in con.execute(
            f"SELECT ticket_no FROM tickets WHERE ticket_no IN ({q})", ids)}
        return {i: (i in found) for i in ids}
    except sqlite3.Error:
        return {}
    finally:
        con.close()
