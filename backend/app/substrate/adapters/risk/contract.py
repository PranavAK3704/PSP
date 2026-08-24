"""The shipment-risk vocabulary, MIRRORED from the captain panel — not invented here.

UPSTREAM: valmo-partner-webview/src/modules/captain/loss-management/shipment-pendency/
          constants.ts  (ShipmentsRiskCategory, RISK_WISE_SHIPMENT_HEADING_CONFIG, and the
          screen's literal strings)
          …/risk-summary/risk-summary.d.ts  (ShipmentRiskColumns, RiskLossBuckets)

WHY MIRROR RATHER THAN INVENT
A captain has already been trained on this ladder. They have seen "Extreme-Risk", they have seen
"(3 days left)", and there is Hindi voice-over for each rung on the real screen. Inventing a
parallel severity vocabulary — "critical/warning/notice" — would mean the same shipment is
described two different ways by two Meesho surfaces, and the one they trust is the one they have
been using. So the words come from there, verbatim, and where we cannot supply something the
real screen shows, we say so rather than filling it in.

TWO DISCIPLINES CARRIED OVER FROM growth/contract.py

**Semantic colour tokens, not hex.** `COLOURS` names roles ("red_action"), and the frontend maps
them to its own CSS variables. Baking upstream's hex in would make this file a theme instead of a
contract mirror — and upstream's palette is designed for a WHITE page, so the literal values are
actively wrong on PSP's dark surface (its own `#43A92C` on `#E7FBE0` measures 2.77:1).

**`verified` on every derived literal.** `growth/contract.py` stamps `status_source:
"panel_client_rule"` so a reader can tell a server verdict from a client-side recomputation. Same
idea here: `RTO_BAGGED_NOT_CONNECTED` is a literal I read in the upstream enum, so
`verified=True`. The other buckets are OUR mapping from `losses.reason_l1`, so `verified=False`.
A reader must never have to guess which is which.
"""
from __future__ import annotations

# ── the ladder, most severe first ───────────────────────────────────────────────────────────
# Order is load-bearing: `ladder.classify` evaluates most-severe-first, which is what makes the
# bands exhaustive and disjoint without any explicit upper bounds.
CATEGORIES: tuple[str, ...] = ("BREACHED", "EXTREME", "HIGH", "MODERATE")

#: RISK_WISE_SHIPMENT_HEADING_CONFIG, verbatim. Note BREACHED's heading is not "Breached" — it
#: is a sentence about what happens next, which is the whole reason to use their word for it.
HEADINGS: dict[str, str] = {
    "BREACHED": "Loss to be marked",
    "EXTREME": "Extreme-Risk",
    "HIGH": "High-Risk",
    "MODERATE": "Moderate-Risk",
}

#: Semantic tokens — see the module note on why these are not hex.
COLOURS: dict[str, str] = {
    "BREACHED": "red_action",
    "EXTREME": "red_border",
    "HIGH": "orange_border",
    "MODERATE": "grey_mid",
}

#: The screen's literal strings. `AMOUNT_AT_RISK` is upstream's label for this figure and is kept
#: for recognition — but on OUR cohort it is money already debited, not live exposure, so every
#: surface that renders it has to re-tense it. See derive.PROVENANCE.
STRINGS: dict[str, str] = {
    "AMOUNT_AT_RISK": "Amount at Risk",
    "DAYS_LEFT": "Days Left to prevent",
    "DAYS_LEFT_FMT": "({n} days left)",
    "OVERDUE": "(Overdue)",
    "PENDENCY_CLEARED": "(Pendency Cleared)",
    "TOTAL_AMOUNT_AT_RISK": "Total Amount at Risk",
    "TOTAL_SHIPMENTS_FMT": "Total {n} Shipments",
}

# ── the columns the real screen shows, and the four we cannot fill ──────────────────────────
# `ShipmentRiskColumns`, with an honest availability flag.
#
# THE RULE THIS ENCODES: `local_db_provider`'s note — "a realistic-looking name attached to a real
# partner_id is not a harmless placeholder, it is a fabrication wearing real provenance." So
# `pilot_name` is not emitted as "" or "—" or a plausible name. It is an ABSENT KEY, declared
# here and reported in the summary as `columns_not_available`, so a UI can say "upstream shows
# this, we cannot" rather than rendering a blank that reads as "no pilot assigned".
COLUMNS: tuple[dict, ...] = (
    {"id": "awb", "label": "AWB", "available": True, "from": "attribution.awb"},
    {"id": "amount_at_risk", "label": "Amount at Risk", "available": True,
     "from": "attribution.attribution_amount"},
    {"id": "prevent_loss_before", "label": "Prevent Loss Before", "available": True,
     "from": "facility_inscan + SLA['lost'] days"},
    {"id": "attempt_count", "label": "Attempts", "available": False,
     "from": "not in valmo.db"},
    {"id": "pilot_name", "label": "Pilot", "available": False, "from": "not in valmo.db"},
    {"id": "bag_id", "label": "Bag ID", "available": False, "from": "not in valmo.db"},
    {"id": "trip_id", "label": "Trip ID", "available": False, "from": "not in valmo.db"},
)

COLUMNS_NOT_AVAILABLE: tuple[str, ...] = tuple(
    c["id"] for c in COLUMNS if not c["available"])

# ── loss buckets ────────────────────────────────────────────────────────────────────────────
# `RTO_BAGGED_NOT_CONNECTED` is the one I can quote from the upstream `RiskLossBuckets` enum —
# and it happens to be exactly the "connect it today or it becomes a loss" case. The rest are OUR
# mapping from `losses.reason_l1`, so they carry `verified: False`.
LOSS_BUCKETS: tuple[dict, ...] = (
    {"id": "RTO_BAGGED_NOT_CONNECTED", "label": "RTO Bagged Not Connected", "verified": True},
    {"id": "FORWARD_NOT_CONNECTED", "label": "Forward Not Connected", "verified": False},
    {"id": "SHIPMENT_SHORTAGE", "label": "Shipment Shortage", "verified": False},
    {"id": "BAG_SHORTAGE", "label": "Bag Shortage", "verified": False},
    {"id": "IN_TRANSIT", "label": "In Transit", "verified": False},
    {"id": "DAMAGE", "label": "Damage", "verified": False},
    {"id": "OTHER", "label": "Other", "verified": False},
)

_BUCKET_BY_ID = {b["id"]: b for b in LOSS_BUCKETS}


def bucket_for(reason_l1: str, movement_type: str) -> dict:
    """(reason_l1, movement) → a bucket dict. Never raises; unknown → OTHER.

    The RTO split is the only place movement matters: upstream's verified literal is
    specifically the RTO-bagged case, so a forward hardstop must NOT borrow that label.
    """
    r = (reason_l1 or "").strip().lower()
    mv = (movement_type or "").strip().lower()
    if r == "hardstop":
        return _BUCKET_BY_ID["RTO_BAGGED_NOT_CONNECTED" if mv == "rto"
                             else "FORWARD_NOT_CONNECTED"]
    return _BUCKET_BY_ID.get({
        "shipment_shortage": "SHIPMENT_SHORTAGE",
        "bag_shortage": "BAG_SHORTAGE",
        "intransit": "IN_TRANSIT",
        "in_transit": "IN_TRANSIT",
        "damage": "DAMAGE",
    }.get(r, "OTHER"), _BUCKET_BY_ID["OTHER"])


# ── the endpoints this adapter stands in for ─────────────────────────────────────────────────
# Verbatim from the captain panel's api.constants.ts, already catalogued in
# substrate/connectors.py. Repeated here so `live` can name the exact path and timeout it would
# have called, the same way growth/contract.GROWTH_ENDPOINTS does.
RISK_ENDPOINTS: dict[str, dict] = {
    "risk_summary": {"path": "/v1/shipments/risk-summary", "timeout_ms": 10000},
    "risk_category_overview": {"path": "/v1/shipments/risk-category-overview",
                               "timeout_ms": 10000},
    "risk_details": {"path": "/v1/shipments/risk-details", "timeout_ms": 15000},
    "risk_details_filters": {"path": "/v1/shipments/risk-details-filters",
                             "timeout_ms": 10000},
}


def validate(payload: dict) -> list[str]:
    """Structural problems with a derived payload. For the harness, not for a request path.

    Deliberately returns a LIST rather than raising: a malformed cohort must degrade to "no risk
    data", never break a loss conversation, so the caller decides what to do. Same shape as
    growth/contract.validate.
    """
    problems: list[str] = []
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return ["summary missing or not a dict"]

    by_cat = summary.get("by_category") or {}
    unknown = (summary.get("uncategorised") or {}).get("n", 0)
    total = summary.get("total_shipments", 0)

    if set(by_cat) != set(CATEGORIES):
        problems.append(f"by_category keys {sorted(by_cat)} != {sorted(CATEGORIES)}")
    # THE invariant. Every shipment is banded or explicitly uncategorised — never dropped.
    if sum(by_cat.values()) + unknown != total:
        problems.append(f"banded {sum(by_cat.values())} + uncategorised {unknown} "
                        f"!= total {total}")
    rows = payload.get("shipments")
    if not isinstance(rows, list):
        problems.append("shipments missing or not a list")
    elif len(rows) != total:
        # A heading that disagrees with the list below it is the HKS bug: 9 AWBs under a
        # heading saying 18.
        problems.append(f"total_shipments {total} != len(shipments) {len(rows)}")

    if not summary.get("as_of"):
        problems.append("as_of missing — a ladder with no stated clock is not auditable")
    if not summary.get("clock_rule"):
        problems.append("clock_rule missing")
    for c in COLUMNS_NOT_AVAILABLE:
        for r in (rows or []):
            if c in r:
                problems.append(f"unavailable column {c!r} present on a shipment row")
                break
    return problems
