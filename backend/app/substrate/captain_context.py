"""Captain Context service (BRD §6.1).

Assembles ONE grounded view of a captain by composing MULTIPLE upstream sources:
  • Meesho account data — profile, ledger, losses, cash/COD
  • Log10               — shipment scans, manifest path, shipment status
Both the resolution engine and the monitor read from this single service. Each
field keeps its source so nothing is hallucinated and provenance is auditable.

By default both providers serve canned (seed) rows so the platform runs without live DB
access. Set PSP_DATA_PROVIDER=prism to serve Meesho account data from the real data lake
(adapters/prism_provider.py); Log10Connector gains live calls once LOG10_* env is set — the
contract here is unchanged either way.
"""
from __future__ import annotations

import os

from .adapters.growth import GrowthConnector
from .adapters.risk import RiskConnector
from .adapters.log10_connector import Log10Connector
from .adapters.mock_connectors import DemoDataProvider


def _select_provider():
    """Pick the account-data provider. DEFAULTS TO DEMO — the live pipeline is unchanged unless
    PSP_DATA_PROVIDER=prism is set explicitly, so enabling the data lake is an opt-in deploy step
    and can be rolled back with one env var.

    Deliberately does NOT fall back to canned data if Prism is selected but unreachable: silently
    serving seed rows under a real provenance label would let the engine make money decisions on
    fake data. Prism raises instead (see prism_provider.PrismError)."""
    choice = os.environ.get("PSP_DATA_PROVIDER", "demo").strip().lower()
    if choice == "prism":
        from .adapters.prism_provider import PrismProvider
        return PrismProvider()
    if choice == "localdb":
        # The real loss ledger (valmo.db / the same tables on Turso), keyed on real partner_id.
        # Needs no Meesho access, so unlike prism it can actually be switched on today. Like
        # prism it does NOT fall back to seed: if the DB is missing, get_profile returns {} and
        # the captain reads as unknown, which is the truthful outcome.
        from .adapters.local_db_provider import LocalDbProvider
        return LocalDbProvider()
    return DemoDataProvider()


_data = _select_provider()      # Meesho account data — demo (canned) by default; Prism data lake opt-in
_log10 = Log10Connector()       # Log10 scans/shipments (canned today; live calls when LOG10_* env is set)
# Orders & Planning, from the captain panel's own growth-dashboard endpoints. HUB-keyed, which
# is why it needs no join: the profile already carries the hub. Fixture-backed today; the
# source label says so, and the composers print it.
_growth = GrowthConnector()
# At-risk shipments, from the real loss ledger. PARTNER-keyed (indexed), unlike growth which is
# hub-keyed — see adapters/risk/connector.py. This is what replaced the monitor's seeded
# shipment source; `PSP_RISK_SOURCE=seed` restores it.
_risk = RiskConnector()


def data_provider():
    """The active account-data provider (for /api/health and diagnostics)."""
    return _data


def growth_provider():
    """The Growth Dashboard connector (for /api/health, the widget, and diagnostics)."""
    return _growth


def risk_provider():
    """The at-risk shipment connector (for /api/health, the monitor, and diagnostics)."""
    return _risk


def get_context(captain_id: str) -> dict:
    profile = _data.get_profile(captain_id)
    if not profile:
        return {}
    # The real loss table (gold.valmo_lost_awb_2k24_v1) keys losses by HUB, not captain, so the
    # Prism provider takes the hub code from the profile. The canned provider keys by captain_id —
    # hence the capability check rather than a signature change that would break it.
    losses = (_data.get_losses(captain_id, profile.get("hub_code") or "")
              if getattr(_data, "accepts_hub_code", False) else _data.get_losses(captain_id))
    # A provider that can aggregate in the DB supplies `summary` — counts and totals computed
    # over the FULL ledger rather than over the row-capped slice above. This is what the
    # engine sends to the model (see engine/tools.captain_aggregate); the rows stay local for
    # the trace and the panel. Capability-checked rather than added to the contract, the same
    # way `accepts_hub_code` is, so the two providers that cannot do it are untouched.
    summary = {}
    if hasattr(_data, "get_summary"):
        try:
            summary = _data.get_summary(captain_id) or {}
        except Exception:  # noqa: BLE001 — an aggregate is an optimisation, never a dependency
            summary = {}
    # Growth dashboard, keyed on the hub the profile already carries. Wrapped because O&P is
    # additive: a missing or malformed fixture must degrade to "no growth data for this hub",
    # never break a loss conversation that has nothing to do with load.
    hub = (profile.get("hub") or profile.get("hub_name") or "").strip()
    try:
        growth = _growth.growth(hub) if hub else {}
    except Exception:  # noqa: BLE001 — includes the deliberate NotImplementedError under live
        growth = {}
    # At-risk cohort, partner-keyed. Wrapped for the same reason growth is: it is ADDITIVE, and a
    # malformed cohort must degrade to "no risk data for this partner" rather than break a loss
    # conversation that has nothing to do with monitoring.
    try:
        risk = _risk.for_partner(captain_id)
    except Exception:  # noqa: BLE001 — includes the deliberate NotImplementedError under live
        risk = {}
    return {
        "captain_id": captain_id,
        "profile": profile,
        "growth": growth,                              # Orders & Planning (hub-keyed)
        "risk": risk,                                  # at-risk shipments (partner-keyed)
        "ledger": _data.get_ledger(captain_id),        # Metabase
        "losses": losses,                              # Metabase
        "cash": _data.get_cash(captain_id),            # Metabase
        "shipments": _log10.get_shipments(captain_id), # Log10
        "summary": summary,                            # aggregates for the model (never rows)
        "_sources": {"account": _data.source, "shipments": _log10.source,
                     "growth": _growth.source, "risk": _risk.source},
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
