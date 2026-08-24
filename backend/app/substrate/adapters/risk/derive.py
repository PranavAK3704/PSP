"""Rows → payload. Pure over the rows handed in; the reading happens in `loss_db`.

WHAT THIS COHORT IS, stated once here and carried on every payload:

    attribution ⋈ losses ON awb — shipments that ALREADY became losses.

Which means selection is on the OUTCOME, and that single fact determines the whole design of this
file. Three consequences, none of them optional:

**1. 100% of rows fire the rule.** A row is in this cohort BECAUSE it became a loss, so any hit
rate computed over it measures the query, not the monitor.

**2. There is no negative class.** Shipments that were at risk and did NOT become losses are not
in `losses` at all. So precision and recall here are **undefined, not merely unmeasured** — there
is no denominator to put them over. `PROVENANCE["why_no_prevention_rate"]` says this in words,
and `check_risk.py` asserts no field anywhere is a count divided by the total.

**3. Under the terminal clock the ladder collapses.** Measured: 5,005 of 5,413 BREACHED (92.5%),
and all three demo hubs 100% BREACHED. That is honest and it is boring, and BREACHED's own
upstream heading — *"Loss to be marked"* — is literally true for every one of them. The replay
clock exists so the other three rungs can be populated from real rows, and `clock_mode` +
`clock_rule` ride on every payload so a replay distribution can never be read as a live one.

The precedent, both already in this repo: `audit/calibration.py` builds the instrument and shows
it EMPTY rather than overclaim; `engine/algo/router.py` refuses to state an absorption rate the
corpus cannot support and ships shadow mode instead of a number.
"""
from __future__ import annotations

import datetime as _dt

from ....tri import Tri
from . import contract, ladder

#: Row cap, matching `loss_db.partner_losses`. Reported as `truncated` + `rows_available` rather
#: than silently applied — a cap that is not stated reads as "this is all of it".
RISK_ROW_CAP = 200

PROVENANCE = {
    "cohort": "attribution JOIN losses ON awb — shipments that ALREADY became losses",
    "keyed_on": "attribution.partner_id (the real Meesho partner-id namespace)",

    "is": [
        "Real rows from backend/data/valmo.db, keyed on a real partner_id.",
        "Every shipment here already became a loss. The outcome is ON RECORD, not predicted.",
        "Severity is the (leg, direction) SLA ladder evaluated at `as_of`, and nothing more.",
    ],
    "is_not": [
        "A live queue of shipments still in flight.",
        "A prevention rate, a saved amount, or a counterfactual of any kind.",
        "Evidence that this monitor would have changed any of these outcomes.",
    ],

    "why_no_prevention_rate": (
        "Selection is on the OUTCOME: a row is in this cohort BECAUSE it became a loss. So 100% "
        "of rows fire the rule, and any hit rate over them measures the query rather than the "
        "monitor. There is also no negative class — shipments that were at risk and did NOT "
        "become losses do not appear in `losses` at all — so precision and recall are UNDEFINED "
        "here, not merely unmeasured."),

    "what_is_measurable": (
        "losses.facility_inscan and losses.attribution_changed are the same two columns "
        "policy_exec._eval_real_loss reads as `reversal_signal`. So 'N of these carry a reversal "
        "signal on record' is a real count over real columns. It says the loss engine, asked "
        "reactively about one of these AWBs, would have had something on record to argue with. "
        "It does NOT say any debit was, or would be, reversed."),

    "clock": (
        "facility_inscan → as_of. NO FALLBACK: a blank in-scan is UNKNOWN, never created_date. "
        "Substituting created_date would band 166 rows on a date that does not start the SLA "
        "clock — the same discipline as log10/predicates.forward_connection_within returning "
        "UNKNOWN for 'no facility in-scan on the timeline'."),

    "sla_source": "substrate/seed.py SLA — (leg, direction) → within/hardstop/lost",
    "sla_reconciliation": (
        "seed.SLA decides the BAND because it is day-granular and so is the corpus "
        "(facility_inscan and lost_date are bare YYYY-MM-DD; a 48-hour rule cannot be evaluated "
        "against a date-only column without inventing a time of day). "
        "kt_lm_sla_hardstop_matrix decides the DEADLINE WE STATE: 48 hrs to connect on "
        "(FM,Forward), (FM,RTO) and (LM,RTO); 120 hrs to deliver-or-RTO on (LM,Forward). The two "
        "already agree on the D3/D5/D6 marking ladder for three of the four keys. Where they "
        "diverge, the KT is one day TIGHTER on `within`, so any captain-facing sentence quotes "
        "connect_sla_hours and never sla_within."),

    "precedent": [
        "audit/calibration.py — build the instrument and show it EMPTY rather than overclaim",
        "engine/algo/router.py — refuses to state an absorption rate the corpus cannot support",
    ],
    "generated_by": "substrate/adapters/risk/derive.py",
}


def _reversal_signal(row: dict) -> tuple[bool, str]:
    """The ONE measurable thing, built from exactly the two columns policy_exec reads.

    NOT `cn_number`. 4,074 of 5,614 cohort rows carry one, so keying on it would report a 73%
    "reversal signal" for entirely the wrong reason — and `loss_db` already documents the trap:
    *"NOT mere cn_number presence — CN accompanies active loss debits too."*
    """
    inscan = str(row.get("facility_inscan") or "").strip()
    changed = str(row.get("attribution_changed") or "").strip().lower() == "yes"
    bits = []
    if inscan:
        bits.append(f"facility in-scan {inscan}")
    if changed:
        bits.append("attribution changed")
    return bool(inscan or changed), "; ".join(bits)


def _shipment(row: dict, *, as_of: _dt.date, clock_mode: str, amt, i) -> dict:
    """One at-risk row. `amt`/`i` are loss_db's coercers, injected so this stays pure."""
    awb = str(row.get("awb") or "").strip()
    leg = str(row.get("leg") or "").strip()
    movement = str(row.get("current_movement_type") or "").strip()
    inscan = str(row.get("facility_inscan") or "").strip()
    terminal = (str(row.get("actual_lost_date") or "").strip()
                or str(row.get("lost_date") or "").strip())

    # Under `terminal` the clock stops on the day the loss was actually marked; under `replay`
    # every row is evaluated against one shared date.
    at = ladder.parse_date(terminal) if clock_mode == "terminal" else as_of
    category, verdict, detail = ("", Tri.UNKNOWN, {"reason": "no_awb"}) if not awb \
        else ladder.classify(leg, movement, inscan, at)

    # A loss that was ALREADY marked on or before `as_of` is BREACHED whatever the arithmetic
    # says. The record is not a prediction we can disagree with.
    terminal_date = ladder.parse_date(terminal)
    already = bool(terminal_date and at and terminal_date <= at)
    if already and verdict is Tri.YES and category != "BREACHED":
        category, detail = "BREACHED", {**detail, "forced_by": "already_terminal"}

    sla = detail.get("sla")
    left = ladder.days_left(sla, detail.get("days_elapsed"))
    signal, signal_detail = _reversal_signal(row)
    bucket = contract.bucket_for(row.get("reason_l1"), movement)
    prevent_before = ""
    start = ladder.parse_date(inscan)
    if start and sla:
        prevent_before = (start + _dt.timedelta(days=sla["lost"])).isoformat()

    out = {
        # identity — LOCAL/TRACE ONLY. `awb` and `partner_id` never reach a model; `hub` may,
        # because dataplane.py is explicit that a hub is a facility, not a person.
        "awb": awb,
        "partner_id": str(row.get("partner_id") or ""),
        "hub": str(row.get("entity_id") or ""),

        "amount_at_risk_inr": amt(row.get("attribution_amount")),
        "shipment_value_inr": amt(row.get("shipment_value")),
        "loss_value_inr": amt(row.get("loss_value")),
        "attribution_status": str(row.get("current_status") or ""),

        "risk_category": category,
        "risk_verdict": verdict.value,
        "risk_verdict_reason": detail.get("reason", ""),
        "heading": contract.HEADINGS.get(category, ""),
        "colour_token": contract.COLOURS.get(category, ""),

        "clock_start": inscan,
        "clock_start_field": "facility_inscan",
        "as_of": (at.isoformat() if at else ""),
        "clock_mode": clock_mode,
        "days_elapsed": detail.get("days_elapsed"),
        "leg": leg,
        "movement_type": movement,
        "direction": detail.get("direction", ""),
        "sla_within": (sla or {}).get("within"),
        "sla_hardstop": (sla or {}).get("hardstop"),
        "sla_lost": (sla or {}).get("lost"),
        "connect_sla_hours": detail.get("connect_sla_hours"),
        "prevent_loss_before": prevent_before,
        "days_left_to_prevent": left,
        "days_left_label": ladder.days_left_label(left),

        # the hindsight stamp — a literal, never conditional prose
        "already_terminal": already,
        "terminal_date": terminal,
        "outcome_on_record": "marked_lost" if terminal else "",

        "reversal_signal": signal,
        "reversal_signal_detail": signal_detail,
        "facility_inscan": inscan,
        "attribution_changed": str(row.get("attribution_changed") or "").lower() == "yes",
        "cn_number": str(row.get("cn_number") or ""),

        "reason_l1": str(row.get("reason_l1") or ""),
        "loss_bucket": bucket["id"],
        "loss_bucket_label": bucket["label"],
        "loss_bucket_verified": bucket["verified"],
        "losses_row_count": i(row.get("row_count") or 1),
        "source": "shipment-risk-derived",
    }
    # `attempt_count` / `pilot_name` / `bag_id` / `trip_id` are ABSENT KEYS, not empty strings.
    # An empty pilot name renders as "no pilot assigned"; an absent key renders as nothing, and
    # `summary.columns_not_available` tells a UI why.
    return out


def derive(rows: list[dict], *, as_of=None, clock_mode: str = "terminal",
           limit: int = RISK_ROW_CAP, amt=None, i=None) -> dict:
    """Consolidated rows → {summary, shipments, provenance, vocabulary}.

    `amt`/`i` default to loss_db's coercers. Every column in both tables is declared TEXT even on
    the LOCAL path (`attribution_amount` arrives as '341.0'), so coercion is required, not
    defensive.
    """
    if amt is None or i is None:
        from ...loss_db import _amt, _i
        amt, i = amt or _amt, i or _i

    raw_rows = len(rows)
    # ── consolidate by AWB ────────────────────────────────────────────────────
    # 201 of 5,413 corpus AWBs carry more than one `losses` row, and multi-row IS the
    # attribution-changed signal. HKS is the whole reason this is mandatory: 9 AWBs × 2 rows,
    # one carrying the value and one at location='meesho' with 0. Without this the panel says
    # "18 shipments" over a list of 9, half of them ₹0.
    from ...loss_db import _consolidate
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(str(r.get("awb") or ""), []).append(dict(r))
    merged = [_consolidate(g) for g in groups.values()]
    merged.sort(key=lambda r: (str(r.get("lost_date") or ""), str(r.get("awb") or "")),
                reverse=True)

    truncated = len(merged) > limit
    rows_available = len(merged)
    merged = merged[:limit]

    as_of_date = ladder.parse_date(as_of)
    clock_mode = "replay" if (clock_mode == "replay" and as_of_date) else "terminal"

    ships, excluded_not_in_flight = [], 0
    for r in merged:
        if clock_mode == "replay":
            start = ladder.parse_date(r.get("facility_inscan"))
            if start and as_of_date and start > as_of_date:
                # Not yet in flight at `as_of`. Counted, not silently dropped.
                excluded_not_in_flight += 1
                continue
        ships.append(_shipment(r, as_of=as_of_date, clock_mode=clock_mode, amt=amt, i=i))

    by_cat = {c: 0 for c in contract.CATEGORIES}
    amt_by_cat = {c: 0.0 for c in contract.CATEGORIES}
    unc = {k: 0 for k in ladder.REASONS}
    total_amount = 0.0
    unc_n = 0
    signal_n = inscan_n = changed_n = already_n = 0
    status_counts: dict[str, int] = {}

    for s in ships:
        total_amount += s["amount_at_risk_inr"]
        cat = s["risk_category"]
        if cat:
            by_cat[cat] += 1
            amt_by_cat[cat] += s["amount_at_risk_inr"]
        else:
            unc_n += 1
            reason = s["risk_verdict_reason"] or "date_unparseable"
            if reason in unc:
                unc[reason] += 1
        if s["reversal_signal"]:
            signal_n += 1
        if s["facility_inscan"]:
            inscan_n += 1
        if s["attribution_changed"]:
            changed_n += 1
        if s["already_terminal"]:
            already_n += 1
        st = s["attribution_status"] or "UNKNOWN"
        status_counts[st] = status_counts.get(st, 0) + 1

    clock_rule = ("per row: the date the loss was actually marked (actual_lost_date or "
                  "lost_date)" if clock_mode == "terminal"
                  else f"one date for the whole cohort: {as_of_date.isoformat()}")

    summary = {
        "available": bool(ships),
        "as_of": (as_of_date.isoformat() if as_of_date else "per-row terminal date"),
        "clock_mode": clock_mode,
        "clock_rule": clock_rule,
        "total_shipments": len(ships),
        "raw_rows": raw_rows,
        "consolidated_awbs": raw_rows - rows_available,
        "excluded_not_in_flight": excluded_not_in_flight,
        "total_amount_at_risk_inr": round(total_amount, 2),
        "by_category": by_cat,
        "amount_by_category": {k: round(v, 2) for k, v in amt_by_cat.items()},
        "uncategorised": {"n": unc_n, **unc},
        "already_terminal": already_n,
        "reversal_signal": {
            "n": signal_n, "of": len(ships),
            "facility_inscan": inscan_n, "attribution_changed": changed_n,
            "means": ("the loss engine, asked reactively about this AWB, would have had "
                      "something on record to argue with"),
            "does_not_mean": "that any debit was, or would be, reversed",
        },
        "attribution_status": status_counts,
        "columns_not_available": list(contract.COLUMNS_NOT_AVAILABLE),
        "truncated": truncated,
        "rows_available": rows_available,
        "row_cap": limit,
        "source": "shipment-risk-derived",
    }

    return {
        "summary": summary,
        "shipments": ships,
        "provenance": dict(PROVENANCE),
        "vocabulary": {
            "categories": list(contract.CATEGORIES),
            "headings": dict(contract.HEADINGS),
            "colours": dict(contract.COLOURS),
            "strings": dict(contract.STRINGS),
            "columns": [dict(c) for c in contract.COLUMNS],
            "loss_buckets": [dict(b) for b in contract.LOSS_BUCKETS],
        },
    }
