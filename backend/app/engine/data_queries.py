"""Data-query path (BRD §4.4 tier (b) — live/past data questions).

The LLM SELECTS which named query to run (it never writes or touches SQL); the
query runs deterministically against the grounded context (Metabase + Log10 in
prod), and the rows are handed back for a grounded conversational answer.
This is how "where is my shipment / truck timing / payout status" is answered.
"""
from __future__ import annotations

from ..substrate import captain_context as ctx

# Named, whitelisted queries. New data need = a new entry here (or emitted by the
# SOP), NOT free-form SQL. Each maps to a deterministic function over the context.
QUERIES = {
    "shipment_status": "Current status + manifest path of the captain's open shipments (Log10)",
    "scan_history": "Full scan trail for a specific AWB (Log10) — needs an awb",
    "payout_status": "The captain's most recent payout/credit (Metabase)",
    "loss_summary": "Summary of losses/debits marked against the captain (Metabase)",
    "cod_status": "COD pendency + latest CMS deposit (Metabase)",
}


def _run(name: str, params: dict, context: dict) -> list[dict]:
    cid = context["captain_id"]
    if name == "shipment_status":
        return [{"awb": s["awb"], "status": s["status"], "on_manifest_path": s.get("on_correct_manifest_path"),
                 "days_since_inscan": s.get("days_since_inscan"), "hub": s.get("hub")}
                for s in context.get("shipments", [])]
    if name == "scan_history":
        awb = params.get("awb") or (context.get("shipments") or [{}])[0].get("awb")
        scans = ctx.get_scans(cid, awb) if awb else None
        if not scans:
            return []
        return [{"awb": awb, "scan": e["scan"], "at": e["at"], "node": e["node"]} for e in scans.get("events", [])]
    if name == "payout_status":
        credits = [l for l in context.get("ledger", []) if l.get("type") == "credit"]
        return [{"id": c["id"], "amount_inr": c["amount_inr"], "date": c["date"], "narration": c["narration"]}
                for c in credits[-3:]]
    if name == "loss_summary":
        return [{"awb": l["awb"], "loss_type": l["loss_type"], "date": l["loss_date"],
                 "reason": l["reason_l1"], "attributed_node": l["attributed_node"]}
                for l in context.get("losses", [])]
    if name == "cod_status":
        cash = context.get("cash", {})
        return [{"cod_pendency_inr": cash.get("cod_pendency_inr", 0),
                 "deposits": cash.get("deposits", [])}]
    return []
