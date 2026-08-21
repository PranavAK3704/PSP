"""The Growth Dashboard contract, mirrored from the captain panel.

SOURCE OF TRUTH
`valmo-partner-webview` — the Next.js app captains actually use. Contract read from
`src/modules/captain/growth-dashboard/typings/growth-dashboard.d.ts`; the lever rules from
`.../utils/metrics.utils.ts`; the display strings and target explanations from
`.../constants.ts`. Read via `unzip -p`; no code copied, only shapes matched.

Two endpoints, both hub-keyed — which is why this replaced a Metabase CSV: PSP already knows
the hub (`loss_db.partner_profile()` → `entity_id`, e.g. `LZ5`), so there is no join to invent.
The panel's own tests use hub `FLA`, the same 3-character shape.

── WHAT IS SERVER-SIDE AND WHAT IS NOT ───────────────────────────────────────────────────────
This distinction decides what PSP is allowed to conclude, so it is spelled out rather than
assumed:

  SERVER-SIDE (read, never recomputed)
    your-metrics   : is_good
    order-summary  : banner_type · the five waterfall counts · right_panel.*.count ·
                     right_panel.*.metrics[].status · extra_earnings_loss · reasons[]

  CLIENT-SIDE (the panel computes it in the browser)
    the good/not_good verdict for the FOUR LEVERS. `TYourMetricsAPIResponse` carries only
    `{current, target}` per lever — no status field exists on the wire. `metrics.utils.ts`
    derives it with `getStatus()` over `parseNumeric()`, using the direction table below.

  DISPLAY STRINGS (never branched on)
    order_change_text · financial_summary · low_capacity_message · bar_tooltips ·
    right_panel.*.metrics[].label / .value / .comparison
    These are prose assembled for a UI. Passed through as text or omitted; a decision must
    never turn on one.

So `LEVER_STATUS` below is a faithful mirror of the panel's client rule, not a PSP invention —
which is the only honest way to name a failing lever when the wire gives no status for it.
Where a status DOES exist server-side (inside `right_panel`), that wins.

── THE ONE PLACE WE DELIBERATELY DIVERGE ─────────────────────────────────────────────────────
`parseNumeric` returns `0` for an unparseable string. For a lower-is-better lever that makes
`0 <= target` true, so a value the panel cannot read silently renders as **good**. Correct for
a UI that must not crash; wrong for an engine that would then tell a captain their RTO is fine.
Here an unparseable value is `Tri.UNKNOWN`, and UNKNOWN never satisfies a check.
"""
from __future__ import annotations

import re
from typing import Any

from ....tri import Tri

# ── the two endpoints, verbatim from src/modules/captain/common/api.constants.ts ──────────
# The client template says `:hubID`; the Next.js BFF route folder is `[hubCode]`. Both recorded
# because a reader chasing this upstream will meet whichever one they meet.
GROWTH_ENDPOINTS = {
    "your_metrics": {
        "url": "/v1/captain/growth-dashboard/:hubID/your-metrics",
        "timeout_ms": 1000,
        "bff_route": "/api/v1/captain/growth-dashboard/[hubCode]/your-metrics",
    },
    "order_summary": {
        "url": "/v1/captain/growth-dashboard/:hubID/order-summary",
        "timeout_ms": 1000,
        "bff_route": "/api/v1/captain/growth-dashboard/[hubCode]/order-summary",
    },
}

# ── required keys, for fixture validation ─────────────────────────────────────────────────
YOUR_METRICS_REQUIRED = (
    "success", "error_msg", "start_date", "end_date", "current_orders", "max_potential",
    "order_change_text", "is_good", "pilot_rate_card", "rto_performance", "day0_attempt",
    "pendency",
)
YOUR_METRICS_OPTIONAL = ("hub_code", "order_share", "order_share_delta")

ORDER_SUMMARY_REQUIRED = (
    "success", "error_msg", "start_date", "end_date", "max_potential", "missed_in_allocation",
    "current_eligible", "bar4_value", "final_manifested", "extra_earnings_loss", "reasons",
    "financial_summary", "right_panel",
)
ORDER_SUMMARY_OPTIONAL = ("bar_tooltips", "banner_type")

BANNER_TYPES = ("green", "grey", "red", "red_capacity_cut")

# The waterfall, in the order the panel renders it. `bar4_value` is SIGNED: positive means
# extra orders won, negative means orders lost to a capacity cut — so the same field is two
# different bars depending on its sign, which is why nothing here reads it as a magnitude.
WATERFALL = ("max_potential", "missed_in_allocation", "current_eligible", "bar4_value",
             "final_manifested")

# ── the four levers ───────────────────────────────────────────────────────────────────────
# id / title / direction, mirrored verbatim from METRIC_CONFIGS in metrics.utils.ts.
# `day0-attempt` is the ONLY higher-is-better lever; getting that inverted would tell a captain
# with 94% attempts against a 70% target that they are failing.
LEVERS: tuple[dict[str, Any], ...] = (
    {"key": "pilot_rate_card", "id": "pilot-rate-card", "title": "Pilot Rate Card (CPS)",
     "higher_is_better": False},
    {"key": "rto_performance", "id": "rto-performance", "title": "RTO Performance",
     "higher_is_better": False},
    {"key": "day0_attempt", "id": "day0-attempt", "title": "Day-0 Attempt %",
     "higher_is_better": True},
    {"key": "pendency", "id": "pendency", "title": "Pendency (DOH)",
     "higher_is_better": False},
)

# Why each target is what it is — verbatim from METRIC_TARGET_TOOLTIPS in constants.ts. This is
# the authored explanation a captain never opens the tooltip to read, and it is exactly what
# PSP says back when they ask "why is my load low".
LEVER_WHY = {
    "pilot_rate_card": "The target rate card is calculated based on your neighbouring DCs rate",
    "rto_performance": "The target RTO is computed based on the best 3PL in your area",
    "day0_attempt": ("The target is to increase your delivery attempt, keep your performance "
                     "above 70% to avoid capacity cut"),
    "pendency": ("Target is set to avoid shipment pendency, please clear pendencies within "
                 "2.5 days to avoid capacity cut"),
}

_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def parse_metric(value: Any) -> float | None:
    """A lever's display string → number, or None when it genuinely cannot be read.

    The panel's `parseNumeric` strips non-numerics and returns 0 on failure. This returns
    **None**, because 0 is a legitimate value for these metrics and using it as the failure
    signal is what makes an unreadable value look good. Values seen on the wire: `"₹14"`,
    `"31%"`, `"0.9 Days"`, `"1.1 Days"`.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str):
        return None
    m = _NUM.search(value.replace(",", ""))
    return float(m.group(0)) if m else None


def lever_status(current: Any, target: Any, higher_is_better: bool) -> Tri:
    """YES = meeting the target, NO = missing it, UNKNOWN = unreadable.

    Mirrors the panel's comparison (`>=` when higher is better, `<=` otherwise) so PSP and the
    dashboard agree about a lever. Diverges only on unreadable input — see the module note.
    """
    c, t = parse_metric(current), parse_metric(target)
    if c is None or t is None:
        return Tri.UNKNOWN
    return Tri.of(c >= t if higher_is_better else c <= t)


def levers(your_metrics: dict) -> list[dict]:
    """Every lever with its parsed status. Order follows the panel's METRIC_CONFIGS."""
    out = []
    for spec in LEVERS:
        raw = your_metrics.get(spec["key"]) or {}
        cur, tgt = raw.get("current"), raw.get("target")
        out.append({
            **spec,
            "current": cur, "target": tgt,
            "status": lever_status(cur, tgt, spec["higher_is_better"]),
            "why": LEVER_WHY.get(spec["key"], ""),
            # `status_source` is recorded because it is load-bearing for honesty: a reader of
            # the evidence trail needs to know whether the verdict came off the wire or was
            # derived here with the panel's rule.
            "status_source": "panel_client_rule",
        })
    return out


def failing_levers(your_metrics: dict) -> list[dict]:
    """Levers definitively missing target. UNKNOWN is excluded — it is not evidence."""
    return [l for l in levers(your_metrics) if l["status"] is Tri.NO]


def dominant_reason(order_summary: dict) -> tuple[str | None, int]:
    """Which right-panel section accounts for the most missed orders, by server-side count.

    Read, never derived: the counts come off the wire. Ties go to `allocation_miss`, which is
    the only section the contract guarantees is present. `extra_orders` is deliberately not a
    candidate — it is orders GAINED, so it can never explain a shortfall.
    """
    rp = order_summary.get("right_panel") or {}
    candidates = []
    for name in ("allocation_miss", "capacity_loss"):
        section = rp.get(name)
        if isinstance(section, dict) and isinstance(section.get("count"), (int, float)):
            candidates.append((name, int(section["count"])))
    if not candidates:
        return None, 0
    candidates.sort(key=lambda kv: (-kv[1], kv[0] != "allocation_miss"))
    name, count = candidates[0]
    return (name, count) if count > 0 else (None, 0)


def validate(payload: dict, kind: str) -> list[str]:
    """Missing/mistyped keys against the contract. Empty list = valid.

    Used by the fixture loader and the harness. A fixture that has drifted from the panel's
    types must fail loudly here rather than produce a subtly wrong answer on stage.
    """
    required = YOUR_METRICS_REQUIRED if kind == "your_metrics" else ORDER_SUMMARY_REQUIRED
    problems = [f"missing '{k}'" for k in required if k not in payload]

    for k in ("start_date", "end_date"):
        v = payload.get(k)
        # dd/mm/YYYY strings on the wire — NOT ISO. A silent reformat here would desync the
        # widget's date range from the engine's evidence trail.
        if isinstance(v, str) and not re.fullmatch(r"\d{2}/\d{2}/\d{4}", v):
            problems.append(f"{k}={v!r} is not dd/mm/YYYY")

    if kind == "your_metrics":
        for spec in LEVERS:
            lv = payload.get(spec["key"])
            if not isinstance(lv, dict) or "current" not in lv or "target" not in lv:
                problems.append(f"{spec['key']} must be {{current, target}}")
    else:
        bt = payload.get("banner_type")
        if bt is not None and bt not in BANNER_TYPES:
            problems.append(f"banner_type={bt!r} not in {BANNER_TYPES}")
        rp = payload.get("right_panel")
        if not isinstance(rp, dict):
            problems.append("right_panel must be an object")
        elif not isinstance(rp.get("allocation_miss"), dict):
            problems.append("right_panel.allocation_miss is required by the contract")
        else:
            for name in ("allocation_miss", "capacity_loss", "extra_orders"):
                sec = rp.get(name)
                if sec is None:
                    continue
                if not isinstance(sec.get("count"), (int, float)):
                    problems.append(f"right_panel.{name}.count must be a number")
                for i, m in enumerate(sec.get("metrics") or []):
                    if m.get("status") not in ("good", "not_good"):
                        problems.append(
                            f"right_panel.{name}.metrics[{i}].status={m.get('status')!r} "
                            f"must be 'good' | 'not_good'")
        if not isinstance(payload.get("reasons"), list):
            problems.append("reasons must be a list")
    return problems
