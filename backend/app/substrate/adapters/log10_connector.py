"""Log10 connector — shipment + bag + scan data (a DISTINCT integration).

Log10 (console.valmo.in) is the source of truth for physical scans, manifest path,
and shipment status. It is a SEPARATE system from the Meesho payments/losses/cash DB
(which we read via Metabase). The Captain Context service composes both.

Demo returns canned Log10-shaped data from seed. Production: point BASE/API_KEY at
Log10 and implement the calls — the Captain Context contract is unchanged.
Integration contract to hand the Log10 team is in PRODUCTION_DELTA.md.
"""
from __future__ import annotations

import os

from ..seed import SEED


class Log10Connector:
    source = "log10"

    def __init__(self):
        self.base = os.environ.get("LOG10_API_BASE", "")
        self.key = os.environ.get("LOG10_API_KEY", "")

    def _live(self) -> bool:
        return bool(self.base and self.key)

    def get_scans(self, captain_id: str, awb: str) -> dict | None:
        if self._live():
            raise NotImplementedError("Wire GET {base}/shipments/{awb}/scans here at go-live.")
        return SEED.get(captain_id, {}).get("scans", {}).get(awb)

    def get_shipments(self, captain_id: str) -> list[dict]:
        if self._live():
            raise NotImplementedError("Wire GET {base}/captains/{id}/shipments here at go-live.")
        return SEED.get(captain_id, {}).get("shipments", [])
