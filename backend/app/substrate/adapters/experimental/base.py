"""The Captain Context data contract (BRD §6.1–6.2).

Production reality: we have root DB access via Metabase. Each SOP declares the data
it needs; that becomes a named query; the provider runs it and returns rows. There
is ONE query interface over the DB — not a connector per system.

    +----------------- Captain Context (one grounded view) ----------------+
    | get_profile / get_ledger / get_losses / get_scans / get_shipments /  |
    | get_cash   — each backed by a named Metabase query                   |
    +----------------------------------------------------------------------+
             DemoDataProvider (canned)   |   MetabaseProvider (real DB)

The resolution engine and the monitor depend ONLY on this contract. Adding a new
data need = a new query the SOP names, registered on the provider — not an engine
change (BRD §15.2). New SOPs haven't been drafted yet; when they are, the queries
they declare are all that's needed.
"""
from __future__ import annotations

from typing import Protocol


class DataProvider(Protocol):
    def get_profile(self, captain_id: str) -> dict: ...
    def get_ledger(self, captain_id: str) -> list[dict]: ...
    def get_scans(self, captain_id: str, awb: str) -> dict | None: ...
    def get_losses(self, captain_id: str) -> list[dict]: ...
    def get_shipments(self, captain_id: str) -> list[dict]: ...
    def get_cash(self, captain_id: str) -> dict: ...
