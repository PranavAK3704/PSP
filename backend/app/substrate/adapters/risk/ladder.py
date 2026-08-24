"""The severity ladder. A pure function over (leg, movement, two dates) — no I/O, ever.

Pure for the reason `algo/router.py` gives for its tiers: *"No I/O inside a matcher; anything
read from a DB is read before and passed in."* That is what lets the harness sweep every day of
every SLA key without a database, and what makes `derive()` deterministic.

── WHICH SLA MATRIX IS AUTHORITATIVE, because there are two and they look like they disagree ──

`substrate/seed.py:SLA` decides the **band**. The corpus's `kt_lm_sla_hardstop_matrix` decides
the **deadline we state to a captain**. They are not in conflict once you notice they measure
different things:

  · `seed.SLA` is DAY-granular, and so is the corpus — `facility_inscan`, `lost_date` and
    `actual_lost_date` are all bare `YYYY-MM-DD`. A 48-HOUR rule cannot be evaluated against a
    date-only column without inventing a time of day, and inventing one would put roughly half
    the rows in a band chosen by a coin flip.
  · The KT matrix already RECONCILES on three of the four keys: "48 hrs to connect, breach D3,
    loss-eligible D5, marked LOST D6" is exactly `{within: 3, hardstop: 5, lost: 6}`, which is
    `(FM,Forward)`, `(FM,RTO)` and `(LM,RTO)`. Only `(LM,Forward)` differs, where the KT gives
    5 days to deliver-or-RTO on the same D3/D5/D6 marking ladder.
  · Where they diverge they diverge in the direction that MATTERS. The KT `within` is 48 hours
    (D2) on three keys where `seed` says D3 — one day looser. Telling a captain they have until
    D3 on a 48-hour leg is precisely the bug `engine/algo/followups.py` was rewritten to fix:
    *"A captain working to a 5-day deadline on a 48-hour leg has already breached on D3."*

So: `seed.SLA` bands the row; `SLA_CONNECT_HOURS` rides on every row and is what any
captain-facing sentence must quote; and `days_left_to_prevent` counts to `lost`, the marking day
that BOTH matrices agree on.
"""
from __future__ import annotations

import datetime as _dt

from ....tri import Tri
from ...seed import SLA

#: The connect SLA in HOURS, from `kt_lm_sla_hardstop_matrix`. Never used to band a row (see the
#: module note) — only to state the real deadline. `(LM, Forward)` is 5 days = 120h to deliver or
#: RTO; every other leg is 48h to connect to the next node.
SLA_CONNECT_HOURS: dict[tuple[str, str], int] = {
    ("FM", "Forward"): 48,
    ("FM", "RTO"): 48,
    ("LM", "Forward"): 120,
    ("LM", "RTO"): 48,
}

#: `losses.current_movement_type` → the direction half of an SLA key.
#:
#: 'return' and 'unknown' map to '' — which is NOT a key, which is UNKNOWN. Deliberately not
#: folded into 'RTO' despite the obvious temptation: 256 `return` rows exist corpus-wide and
#: guessing which leg they belong to is a decision about money.
_DIRECTION = {"forward": "Forward", "rto": "RTO"}

#: Why a row could not be banded. Every one of these keeps the row and its rupees in the payload.
REASONS = ("no_clock_start", "leg_not_in_sla", "date_unparseable", "negative_interval", "no_awb")


def parse_date(v) -> _dt.date | None:
    """A bare `YYYY-MM-DD` → date, or None. Never today's date, never epoch, never 0.

    Same discipline as `growth/contract.parse_metric` returning None rather than 0: a failure
    signal that collides with a legitimate value is how an unreadable input silently becomes a
    passing check.
    """
    if isinstance(v, _dt.date):
        return v
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return _dt.date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


def sla_for(leg: str, movement_type: str) -> tuple[dict | None, str, str]:
    """(sla_dict | None, LEG, Direction). A missing key is UNKNOWN, never a default.

    THE TEMPTATION THIS REFUSES: `SLA.get(key, {"within": 5, "hardstop": 7})` — which the old
    monitor did. In this cohort `leg` is LM 5,349 / meesho 246 / FE 19 and **never FM**, so a
    default would silently band 265 `meesho`/`FE` rows on the LM/Forward clock. They are not LM
    shipments; they have no SLA on record; the honest answer is that we do not know.
    """
    lg = (leg or "").strip().upper()
    direction = _DIRECTION.get((movement_type or "").strip().lower(), "")
    if not direction:
        return None, lg, ""
    return SLA.get((lg, direction)), lg, direction


def classify(leg: str, movement_type: str, clock_start, as_of) -> tuple[str, Tri, dict]:
    """(category, verdict, detail). `category` is "" exactly when verdict is UNKNOWN.

    Bands are half-open with an INCLUSIVE lower bound, evaluated most-severe-first — which makes
    them exhaustive and disjoint by construction, with no upper bounds to keep in sync:

        days >= lost      -> BREACHED    'Loss to be marked'
        days >= hardstop  -> EXTREME     'Extreme-Risk'
        days >= within    -> HIGH        'High-Risk'
        days >= 0         -> MODERATE    'Moderate-Risk'

    `days == lost` is BREACHED and not EXTREME. One off there is the difference between "you have
    today" and "it already happened", which is the whole content of the message.
    """
    detail: dict = {"leg": (leg or "").strip().upper(), "movement_type": movement_type or "",
                    "direction": "", "days_elapsed": None, "sla": None,
                    "connect_sla_hours": None, "reason": ""}

    start, at = parse_date(clock_start), parse_date(as_of)
    if start is None:
        # NO FALLBACK to created_date. A blank in-scan means the SLA clock never started on
        # record; substituting the creation date would band 166 corpus rows on a date that does
        # not start the clock. Mirrors log10/predicates returning UNKNOWN for "no facility
        # in-scan on the timeline" rather than assuming the shipment never arrived.
        detail["reason"] = "no_clock_start"
        return "", Tri.UNKNOWN, detail
    if at is None:
        detail["reason"] = "date_unparseable"
        return "", Tri.UNKNOWN, detail

    sla, lg, direction = sla_for(leg, movement_type)
    detail["direction"] = direction
    if sla is None:
        detail["reason"] = "leg_not_in_sla"
        return "", Tri.UNKNOWN, detail
    detail["sla"] = dict(sla)
    detail["connect_sla_hours"] = SLA_CONNECT_HOURS.get((lg, direction))

    days = (at - start).days
    detail["days_elapsed"] = days
    if days < 0:
        # In-scan AFTER the evaluation date. Reachable under a replay clock, and clamping to 0
        # would put a shipment that was not yet in flight into MODERATE. Follows
        # log10/predicates.vehicle_arrival_to_ticket, which returns UNKNOWN when the ticket
        # predates the arrival rather than treating the interval as zero.
        detail["reason"] = "negative_interval"
        return "", Tri.UNKNOWN, detail

    if days >= sla["lost"]:
        return "BREACHED", Tri.YES, detail
    if days >= sla["hardstop"]:
        return "EXTREME", Tri.YES, detail
    if days >= sla["within"]:
        return "HIGH", Tri.YES, detail
    return "MODERATE", Tri.YES, detail


def days_left(sla: dict | None, days_elapsed: int | None) -> int | None:
    """Days until the loss is MARKED. Counted to `lost`, the day both matrices agree on."""
    if not sla or days_elapsed is None:
        return None
    return sla["lost"] - days_elapsed


def days_left_label(left: int | None, cleared: bool = False) -> str:
    """Upstream's own three labels. `(Overdue)` covers 0 as well as negative — on the marking
    day itself there is no time left to prevent anything."""
    from .contract import STRINGS
    if cleared:
        return STRINGS["PENDENCY_CLEARED"]
    if left is None:
        return ""
    if left <= 0:
        return STRINGS["OVERDUE"]
    return STRINGS["DAYS_LEFT_FMT"].format(n=left)
