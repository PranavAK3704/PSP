"""Captain Context service (BRD §6.1).

Assembles ONE grounded view of a captain by composing MULTIPLE upstream sources:
  • Meesho account data — profile, ledger, losses, cash/COD
  • Log10               — shipment scans, manifest path, shipment status
Both the resolution engine and the monitor read from this single service. Each
field keeps its source so nothing is hallucinated and provenance is auditable.

Today both providers serve canned (seed) rows so the platform runs without live
DB access. Future: a real Metabase-backed sync will supply the Meesho account data
(aspirational scaffolding lives in adapters/experimental/, not wired in yet), and
Log10Connector gains live calls once LOG10_* env is set — the contract here is
unchanged either way.
"""
from __future__ import annotations

from .adapters.log10_connector import Log10Connector
from .adapters.mock_connectors import DemoDataProvider

_data = DemoDataProvider()      # Meesho account data (canned today; real Metabase sync is future work)
_log10 = Log10Connector()       # Log10 scans/shipments (canned today; live calls when LOG10_* env is set)


def get_context(captain_id: str) -> dict:
    profile = _data.get_profile(captain_id)
    if not profile:
        return {}
    return {
        "captain_id": captain_id,
        "profile": profile,
        "ledger": _data.get_ledger(captain_id),        # Metabase
        "losses": _data.get_losses(captain_id),        # Metabase
        "cash": _data.get_cash(captain_id),            # Metabase
        "shipments": _log10.get_shipments(captain_id), # Log10
        "_sources": {"account": _data.source, "shipments": _log10.source},
    }


def get_scans(captain_id: str, awb: str) -> dict | None:
    return _log10.get_scans(captain_id, awb)           # Log10


def known_captains() -> list[dict]:
    out = []
    for cid in _data.known_captains():
        p = _data.get_profile(cid)
        out.append({"captain_id": cid, "name": p.get("name"), "hub_name": p.get("hub_name"),
                    "tier": p.get("tier"), "language": p.get("language")})
    return out
