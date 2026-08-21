"""GrowthConnector — the captain panel's O&P endpoints, served from fixtures.

Same seam convention as every other adapter here (`PSP_DATA_PROVIDER`, `PSP_LOG10_SOURCE`):
one env var flips the source, and the contract does not change.

    PSP_GROWTH_SOURCE=fixture   (default) read backend/data/growth_fixtures/<HUB>.json
    PSP_GROWTH_SOURCE=live      raise, naming the endpoint that would be called

`source` reports **"growth-dashboard-fixture"**, never "growth-dashboard". That string is not
cosmetic: `captain_context.get_context()` puts it in `_sources`, and the Phase-1 composers
print `_sources` into every composed answer — so the fixture provenance reaches the captain-
facing sentence and the trace automatically, without anyone remembering to mention it. The one
rule this codebase will not bend is `prism_provider`'s: fake data never wears a real provenance
label.

`live` raises rather than falling back, for the same reason `PrismProvider` raises: silently
serving fixture rows under a live label would let the engine make a decision on data whose
origin nobody can reconstruct afterwards.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import contract

_FIXTURES = Path(__file__).resolve().parents[4] / "data" / "growth_fixtures"

FIXTURE = "fixture"
LIVE = "live"

GROWTH_ENDPOINTS = contract.GROWTH_ENDPOINTS


class GrowthConnector:
    # NOT "growth-dashboard". See the module note — this label is load-bearing.
    source = "growth-dashboard-fixture"

    def __init__(self):
        self.mode = (os.environ.get("PSP_GROWTH_SOURCE") or FIXTURE).strip().lower()
        if self.mode not in (FIXTURE, LIVE):
            self.mode = FIXTURE
        self.base = os.environ.get("GROWTH_API_BASE", "")
        if self.mode == LIVE:
            self.source = "growth-dashboard"

    # ── configuration / health ──
    def configured(self) -> bool:
        return self.mode == FIXTURE and _FIXTURES.is_dir()

    def status(self) -> dict:
        hubs = self.known_hubs()
        return {"provider": "growth", "mode": self.mode, "ok": bool(hubs) or self.mode == LIVE,
                "source": self.source, "hubs": len(hubs),
                "endpoints": [e["url"] for e in GROWTH_ENDPOINTS.values()],
                "detail": "" if hubs else f"no fixtures in {_FIXTURES}"}

    def known_hubs(self) -> list[str]:
        if self.mode != FIXTURE or not _FIXTURES.is_dir():
            return []
        return sorted(p.stem for p in _FIXTURES.glob("*.json"))

    # ── the two endpoints ──
    def your_metrics(self, hub_code: str) -> dict | None:
        """`GET /v1/captain/growth-dashboard/:hubID/your-metrics`"""
        return self._fetch(hub_code, "your_metrics")

    def order_summary(self, hub_code: str) -> dict | None:
        """`GET /v1/captain/growth-dashboard/:hubID/order-summary`"""
        return self._fetch(hub_code, "order_summary")

    def growth(self, hub_code: str) -> dict:
        """Both endpoints plus provenance — what the engine and the widget both read.

        Returned even when a hub is absent, with `available: False`, so a caller can tell
        "this hub has no growth data" apart from "the growth source is not configured". Those
        need different replies to a captain.
        """
        ym = self.your_metrics(hub_code)
        os_ = self.order_summary(hub_code)
        return {
            "hub_code": (hub_code or "").strip().upper(),
            "available": bool(ym and os_),
            "your_metrics": ym,
            "order_summary": os_,
            "source": self.source,
            "mode": self.mode,
            "endpoints": {k: v["url"].replace(":hubID", (hub_code or "").strip().upper())
                          for k, v in GROWTH_ENDPOINTS.items()},
        }

    # ── internals ──
    def _fetch(self, hub_code: str, kind: str) -> dict | None:
        if self.mode == LIVE:
            ep = GROWTH_ENDPOINTS[kind]
            raise NotImplementedError(
                f"PSP_GROWTH_SOURCE=live is not wired. At go-live, call "
                f"GET {ep['url'].replace(':hubID', hub_code)} (timeout {ep['timeout_ms']}ms) "
                f"through the captain panel's BFF route {ep['bff_route']}, which already holds "
                f"the partner session. Set PSP_GROWTH_SOURCE=fixture to run from files.")
        blob = self._load(hub_code)
        if not blob:
            return None
        payload = blob.get(kind)
        if not isinstance(payload, dict):
            return None
        # `_provenance` documents which fixture this is and why it exists. It is stripped here
        # so what the engine sees is byte-identical to the endpoint response — a key the real
        # API never sends must not reach a consumer that will one day read the real API.
        return {k: v for k, v in payload.items() if not k.startswith("_")}

    def provenance(self, hub_code: str) -> dict:
        """The `_provenance` block for a hub — for the trace and the fixture panel, not for
        the decision. Kept separate from `_fetch` precisely so a decision cannot read it."""
        blob = self._load(hub_code) or {}
        return blob.get("_provenance") or {}

    def _load(self, hub_code: str) -> dict | None:
        hub = (hub_code or "").strip().upper()
        if not hub or "/" in hub or "." in hub:      # no path traversal via a hub code
            return None
        path = _FIXTURES / f"{hub}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001 — a malformed fixture reads as "no data", never a crash
            return None
