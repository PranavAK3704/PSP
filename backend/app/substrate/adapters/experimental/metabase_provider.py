"""Metabase provider — the PRODUCTION data layer (stub).

Per the confirmed production design: we get root DB access via Metabase. Each SOP
declares the data it needs; that maps to a named Metabase query (a saved "card" or
SQL), and this provider runs it and returns rows. There is no per-system connector
fiction — there is ONE query interface over the DB.

The demo ships DemoDataProvider (canned query results) so it runs without DB access.
Swap to this by pointing captain_context._provider here and setting METABASE_* env.
The pipeline is unchanged — it only ever asks for query results.
"""
from __future__ import annotations

import os

# Named queries the current SOPs need. New SOPs add entries here (or, better, the
# SOP Compiler emits the query name into required_evidence and it's registered).
QUERIES = {
    "get_payments_ledger":        "captain debits/credits",
    "get_shipment_scan_history":  "AWB scan trail (Log10)",
    "get_loss_attribution":       "loss type + attributed node + reason_l1",
    "get_cod_pendency":           "COD pendency + CMS deposits",
    "get_shipments_open":         "open shipments + manifest path",
    "get_captain_profile":        "profile / hub / tier",
}


class MetabaseProvider:
    source = "metabase"

    def __init__(self):
        self.base = os.environ.get("METABASE_URL", "")
        self.token = os.environ.get("METABASE_SESSION_TOKEN", "")

    def query(self, name: str, params: dict) -> list[dict]:
        if name not in QUERIES:
            raise KeyError(f"Unknown query '{name}'. Register it in QUERIES + the SOP.")
        if not self.base:
            raise RuntimeError(
                "MetabaseProvider selected but METABASE_URL not set. "
                "Use DemoDataProvider for the demo, or provision DB access."
            )
        # Production: POST to Metabase card/dataset endpoint with params, return rows.
        # e.g. requests.post(f"{self.base}/api/card/{card_id}/query/json",
        #                    headers={"X-Metabase-Session": self.token}, json={"parameters": ...})
        raise NotImplementedError("Wire the Metabase card/SQL calls here at go-live.")

    # Convenience accessors the Captain Context service uses — all backed by query().
    def get_profile(self, cid):   return (self.query("get_captain_profile", {"captain_id": cid}) or [{}])[0]
    def get_ledger(self, cid):    return self.query("get_payments_ledger", {"captain_id": cid})
    def get_scans(self, cid, awb): rows = self.query("get_shipment_scan_history", {"awb": awb}); return rows[0] if rows else None
    def get_losses(self, cid):    return self.query("get_loss_attribution", {"captain_id": cid})
    def get_shipments(self, cid): return self.query("get_shipments_open", {"captain_id": cid})
    def get_cash(self, cid):      return (self.query("get_cod_pendency", {"captain_id": cid}) or [{}])[0]
    def known_captains(self):     return []
