"""DemoDataProvider — the LIVE data provider today (canned query results).

Returns query-shaped rows from seed data so the platform runs without DB access. This is the
DEFAULT provider in captain_context.py; the real data-lake provider is prism_provider.py, opted
into with PSP_DATA_PROVIDER=prism.
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
