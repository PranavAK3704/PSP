"""RiskConnector — the seam. `derived` reads real rows; `seed` reproduces the old monitor; `live`
refuses.

WHY `source` IS LOAD-BEARING, and why it never says the bare name unless it is live:

`growth/connector.py` states the rule this file inherits — a source label is not decoration. It
lands in `captain_context._sources`, and the Phase-1 composers print it into captain-facing
answers. So `"shipment-risk"` is reserved for a genuine upstream call, and every other mode says
what it actually is: `"shipment-risk-derived"` for real-but-reconstructed rows,
`"shipment-risk-seed"` for the fictional demo shipments.

The inverse matters just as much here, and it is why this is a SEPARATE endpoint from
`/api/growth/{hub}` rather than a key inside it: real `valmo.db` rows served under a payload
labelled `growth-dashboard-fixture` would make the provenance unreconstructable. Fake data must
never wear a real label — and real data must never wear a fixture one.

THREE MODES, not two, following `log10/connector.py`. `seed` is not dead weight: it is what keeps
today's demo captain (`VLMO-CPT-4471`, one fictional AWB) reachable byte-for-byte, so the monitor
can be shown on either footing and the difference is one env var.
"""
from __future__ import annotations

import os

from . import contract, derive

DERIVED, SEED, LIVE = "derived", "seed", "live"
_MODES = (DERIVED, SEED, LIVE)


def mode() -> str:
    """`PSP_RISK_SOURCE` = derived | seed | live. Unknown values fall back to `derived`.

    Never `live` on a typo. A deploy that sets `PSP_RISK_SOURCE=LIVE ` (trailing space) or
    `=true` must not silently start attempting Meesho calls, so anything unrecognised resolves to
    the safe local mode — the same defaulting `write_mode.mode()` and `router.mode()` use.
    """
    raw = (os.environ.get("PSP_RISK_SOURCE") or "").strip().lower()
    return raw if raw in _MODES else DERIVED


class RiskConnector:
    """At-risk shipments for a partner or a hub."""

    @property
    def source(self) -> str:
        m = mode()
        if m == LIVE:
            return "shipment-risk"
        return "shipment-risk-seed" if m == SEED else "shipment-risk-derived"

    def configured(self) -> bool:
        return mode() != LIVE

    def status(self) -> dict:
        from ...loss_db import at_risk_corpus_stats, source as db_source
        m = mode()
        out = {"provider": "risk", "mode": m, "source": self.source,
               "ok": m != LIVE, "db_source": db_source(),
               "endpoints": {k: v["path"] for k, v in contract.RISK_ENDPOINTS.items()}}
        if m == DERIVED:
            try:
                out.update(at_risk_corpus_stats())
            except Exception as e:  # noqa: BLE001 — status must never raise
                out["detail"] = f"{type(e).__name__}: {e}"[:160]
        elif m == LIVE:
            out["detail"] = self._live_reason()
        return out

    # ── the refusal ─────────────────────────────────────────────────────────────
    @staticmethod
    def _live_reason() -> str:
        ep = contract.RISK_ENDPOINTS["risk_details"]
        return (f"PSP_RISK_SOURCE=live would call GET {ep['path']} "
                f"(timeout {ep['timeout_ms']}ms) on the captain-service host. No network path "
                f"and no service auth exist in this environment.")

    def _refuse_if_live(self) -> None:
        """`live` is ACCEPTED and REFUSES. It must not behave like `derived`.

        A deploy that sets `live` believing it now reads upstream, and silently keeps getting
        reconstructed rows, is the exact failure this raise prevents — the same reasoning as
        `write_mode`'s live refusal and `Log10Connector._live`'s NotImplementedError.
        """
        if mode() == LIVE:
            raise NotImplementedError(self._live_reason())

    # ── readers ─────────────────────────────────────────────────────────────────
    def for_partner(self, partner_id: str, *, as_of=None, clock_mode: str = "terminal",
                    limit: int = derive.RISK_ROW_CAP) -> dict:
        self._refuse_if_live()
        if mode() == SEED:
            return self._from_seed(partner_id)
        from ...loss_db import partner_at_risk_rows
        rows = partner_at_risk_rows(partner_id, limit=limit * 2)
        out = derive.derive(rows, as_of=as_of, clock_mode=clock_mode, limit=limit)
        out["summary"]["partner_id"] = partner_id
        out["summary"]["keyed_on"] = ("attribution.partner_id — index-served "
                                      "(idx_attribution_partner_id)")
        out["summary"]["mode"] = mode()
        return out

    def for_hub(self, hub_code: str, *, as_of=None, clock_mode: str = "terminal",
                limit: int = derive.RISK_ROW_CAP) -> dict:
        self._refuse_if_live()
        hub = (hub_code or "").strip()
        # Same guard GrowthConnector._load uses, and for the same reason: a hub code reaches this
        # from a URL path segment.
        if not hub or "/" in hub or "." in hub:
            return {"summary": {"available": False, "hub": hub_code,
                                "detail": "invalid hub code"},
                    "shipments": [], "provenance": dict(derive.PROVENANCE), "vocabulary": {}}
        if mode() == SEED:
            return self._from_seed("")
        from ...loss_db import hub_at_risk_rows
        rows = hub_at_risk_rows(hub, limit=limit * 2)
        out = derive.derive(rows, as_of=as_of, clock_mode=clock_mode, limit=limit)
        out["summary"]["hub"] = hub
        # The cost is STATED, not hidden. `attribution.entity_id` carries no index, so this is a
        # 10,000-row scan (~7ms measured) where the partner-keyed path is 0.3ms. Acceptable for a
        # panel; worth saying out loud so nobody later wonders why one route is slower.
        out["summary"]["keyed_on"] = ("attribution.entity_id — UNINDEXED, full scan of ~10,000 "
                                      "attribution rows (~7ms); partner-keyed is 0.3ms")
        out["summary"]["mode"] = mode()
        return out

    # ── seed mode: today's monitor, unchanged ───────────────────────────────────
    def _from_seed(self, captain_id: str) -> dict:
        """Reproduce the pre-existing seeded shipments through the new payload shape.

        The old first-pass rule was `not on_correct_manifest_path and days >= within - 1`, and it
        is preserved verbatim here so `PSP_RISK_SOURCE=seed` is a faithful fallback rather than an
        approximation of one.
        """
        from ...seed import SEED, SLA
        ships = []
        for s in (SEED.get(captain_id, {}) or {}).get("shipments", []) or []:
            key = (s.get("leg"), s.get("direction"))
            sla = SLA.get(key, {"within": 5, "hardstop": 7, "lost": 8})
            days = s.get("days_since_inscan", 0) or 0
            if s.get("on_correct_manifest_path") or days < sla["within"] - 1:
                continue
            cat = ("BREACHED" if days >= sla["lost"] else "EXTREME" if days >= sla["hardstop"]
                   else "HIGH" if days >= sla["within"] else "MODERATE")
            left = sla["lost"] - days
            ships.append({
                "awb": s.get("awb", ""), "partner_id": captain_id, "hub": "",
                "amount_at_risk_inr": 0.0, "shipment_value_inr": None, "loss_value_inr": 0.0,
                "attribution_status": "", "risk_category": cat, "risk_verdict": "YES",
                "risk_verdict_reason": "", "heading": contract.HEADINGS[cat],
                "colour_token": contract.COLOURS[cat], "clock_start": "",
                "clock_start_field": "days_since_inscan", "as_of": "", "clock_mode": "seed",
                "days_elapsed": days, "leg": s.get("leg", ""),
                "movement_type": (s.get("direction") or "").lower(),
                "direction": s.get("direction", ""),
                "sla_within": sla["within"], "sla_hardstop": sla["hardstop"],
                "sla_lost": sla["lost"],
                "connect_sla_hours": None, "prevent_loss_before": "",
                "days_left_to_prevent": left,
                "days_left_label": f"({left} days left)" if left > 0 else "(Overdue)",
                "already_terminal": False, "terminal_date": "", "outcome_on_record": "",
                "reversal_signal": False, "reversal_signal_detail": "", "facility_inscan": "",
                "attribution_changed": False, "cn_number": "",
                "reason_l1": "hardstop", "loss_bucket": "FORWARD_NOT_CONNECTED",
                "loss_bucket_label": "Forward Not Connected", "loss_bucket_verified": False,
                "losses_row_count": 1, "source": "shipment-risk-seed",
            })
        by_cat = {c: 0 for c in contract.CATEGORIES}
        for s in ships:
            by_cat[s["risk_category"]] += 1
        return {
            "summary": {
                "available": bool(ships), "partner_id": captain_id,
                "as_of": "seed fixtures carry no dates", "clock_mode": "seed",
                "clock_rule": "days_since_inscan on the seeded shipment",
                "total_shipments": len(ships), "raw_rows": len(ships),
                "consolidated_awbs": 0, "excluded_not_in_flight": 0,
                "total_amount_at_risk_inr": 0.0, "by_category": by_cat,
                "amount_by_category": {c: 0.0 for c in contract.CATEGORIES},
                "uncategorised": {"n": 0, **{k: 0 for k in
                                             ("no_clock_start", "leg_not_in_sla",
                                              "date_unparseable", "negative_interval", "no_awb")}},
                "already_terminal": 0,
                "reversal_signal": {"n": 0, "of": len(ships), "facility_inscan": 0,
                                    "attribution_changed": 0,
                                    "means": "not applicable to seeded shipments",
                                    "does_not_mean": "that any debit was, or would be, reversed"},
                "attribution_status": {}, "columns_not_available":
                    list(contract.COLUMNS_NOT_AVAILABLE),
                "truncated": False, "rows_available": len(ships),
                "row_cap": derive.RISK_ROW_CAP, "source": "shipment-risk-seed",
                "mode": SEED, "keyed_on": "substrate/seed.py SEED[captain_id]['shipments']",
            },
            "shipments": ships,
            "provenance": {**derive.PROVENANCE,
                           "cohort": "substrate/seed.py fixtures — FICTIONAL shipments",
                           "is": ["Invented demo shipments with invented AWBs.",
                                  "Kept so the pre-existing demo captain stays reachable."],
                           "is_not": ["Real data of any kind.",
                                      "A prevention rate, a saved amount, or a counterfactual."]},
            "vocabulary": {
                "categories": list(contract.CATEGORIES),
                "headings": dict(contract.HEADINGS),
                "colours": dict(contract.COLOURS),
                "strings": dict(contract.STRINGS),
                "columns": [dict(c) for c in contract.COLUMNS],
                "loss_buckets": [dict(b) for b in contract.LOSS_BUCKETS],
            },
        }
