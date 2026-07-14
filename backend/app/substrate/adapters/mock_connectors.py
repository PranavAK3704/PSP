"""DemoDataProvider — the LIVE data provider today (canned query results).

Returns Metabase-shaped rows from seed data so the platform runs without DB
access. This is the provider wired into captain_context.py right now. A real
Metabase-backed provider is future work (aspirational scaffolding is kept under
adapters/experimental/ for reference) — it is NOT wired in yet.
"""
from __future__ import annotations

from ..seed import SEED


class DemoDataProvider:
    source = "demo (canned Metabase query results)"

    def get_profile(self, captain_id: str) -> dict:
        return SEED.get(captain_id, {}).get("profile", {})

    def get_ledger(self, captain_id: str) -> list[dict]:
        return SEED.get(captain_id, {}).get("ledger", [])

    def get_scans(self, captain_id: str, awb: str) -> dict | None:
        return SEED.get(captain_id, {}).get("scans", {}).get(awb)

    def get_losses(self, captain_id: str) -> list[dict]:
        return SEED.get(captain_id, {}).get("losses", [])

    def get_shipments(self, captain_id: str) -> list[dict]:
        return SEED.get(captain_id, {}).get("shipments", [])

    def get_cash(self, captain_id: str) -> dict:
        return SEED.get(captain_id, {}).get("cash", {})

    def known_captains(self) -> list[str]:
        return list(SEED.keys())
