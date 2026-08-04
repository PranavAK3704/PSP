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

import os

from .adapters.log10_connector import Log10Connector
from .adapters.mock_connectors import DemoDataProvider


def _select_provider():
    """Pick the account-data provider. DEFAULTS TO DEMO — the live pipeline is unchanged unless
    PSP_DATA_PROVIDER=prism is set explicitly, so enabling the data lake is an opt-in deploy step
    and can be rolled back with one env var.

    Deliberately does NOT fall back to canned data if Prism is selected but unreachable: silently
    serving seed rows under a real provenance label would let the engine make money decisions on
    fake data. Prism raises instead (see prism_provider.PrismError)."""
    if os.environ.get("PSP_DATA_PROVIDER", "demo").strip().lower() == "prism":
        from .adapters.prism_provider import PrismProvider
        return PrismProvider()
    return DemoDataProvider()


_data = _select_provider()      # Meesho account data — demo (canned) by default; Prism data lake opt-in
_log10 = Log10Connector()       # Log10 scans/shipments (canned today; live calls when LOG10_* env is set)


def data_provider():
    """The active account-data provider (for /api/health and diagnostics)."""
    return _data


def get_context(captain_id: str) -> dict:
    profile = _data.get_profile(captain_id)
    if not profile:
        return {}
    # The real loss table (gold.valmo_lost_awb_2k24_v1) keys losses by HUB, not captain, so the
    # Prism provider takes the hub code from the profile. The canned provider keys by captain_id —
    # hence the capability check rather than a signature change that would break it.
    losses = (_data.get_losses(captain_id, profile.get("hub_code") or "")
              if getattr(_data, "accepts_hub_code", False) else _data.get_losses(captain_id))
    return {
        "captain_id": captain_id,
        "profile": profile,
        "ledger": _data.get_ledger(captain_id),        # Metabase
        "losses": losses,                              # Metabase
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
