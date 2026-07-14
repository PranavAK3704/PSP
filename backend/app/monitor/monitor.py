"""Proactive Monitoring Layer (BRD §5).

The SAME engine, entered from a data-change event instead of a chat message. A
cheap deterministic first-pass filters the event stream so an LLM runs ONLY when
a real risk fires (cost scales with risk-events, not captain count — BRD §5.2).
Warnings are partner-protective and shipped shadow-first (BRD §5.3).
"""
from __future__ import annotations

import uuid
from typing import Iterator

from ..ledger import concern_log
from ..llm import registry as llm_registry
from ..substrate import captain_context as ctx
from ..substrate.seed import SLA


def _evt(node, label, status, detail="", data=None):
    return {"node": node, "label": label, "status": status, "detail": detail, "data": data or {}}


def _cheap_first_pass(shipment: dict) -> dict | None:
    """Deterministic rules — no LLM. Returns a risk dict if a risk fires, else None."""
    leg = (shipment.get("leg"), shipment.get("direction"))
    sla = SLA.get(leg, {"within": 5, "hardstop": 7})
    days = shipment.get("days_since_inscan", 0)
    if not shipment.get("on_correct_manifest_path") and days >= sla["within"] - 1:
        return {"risk": "hardstop_breach_imminent", "awb": shipment["awb"],
                "days_since_inscan": days, "hardstop_day": sla["hardstop"],
                "detail": f"Not manifested; {days}d since in-scan, hardstop at D{sla['hardstop']}"}
    return None


def scan_captain(captain_id: str) -> Iterator[dict]:
    """Run the monitor over one captain's shipments (a data-change event stream)."""
    context = ctx.get_context(captain_id)
    shipments = context.get("shipments", [])
    yield _evt("stream", "Data-change events", "done",
               detail=f"{len(shipments)} shipment events on the stream",
               data={"count": len(shipments)})

    risks = []
    for s in shipments:
        r = _cheap_first_pass(s)
        if r:
            risks.append(r)
    yield _evt("firstpass", "Cheap first-pass (rules + vector)", "done",
               detail=(f"{len(risks)} risk(s) fired — LLM invoked only for these"
                       if risks else "No risk — discarded, no LLM"),
               data={"risks_fired": len(risks), "events_scanned": len(shipments)})

    if not risks:
        yield {"node": "clear", "label": "No risk", "status": "done",
               "detail": "Nothing to warn about — cost stayed at ~zero (no LLM).", "data": {}}
        return

    for r in risks:
        yield _evt("compose", "Compose nudge (Haiku)", "running",
                   detail=f"Risk fired for {r['awb']} — composing warning")
        nudge, meta = _compose_nudge(context["profile"], r)
        # Log the nudge to the shared Concern Log so monitoring is part of the same spine
        # (shows in Ledger/Audit/Insights). outcome="proactive_nudge" — NOT "escalated",
        # so it never enters the L3 inbox.
        concern = concern_log.append({
            "id": "CNC-" + uuid.uuid4().hex[:8].upper(), "captain_id": captain_id,
            "channel": "proactive", "intent": r["detail"][:80],
            "disposition": "proactive_nudge", "action_taken": "nudge_sent",
            "amount_inr": None, "outcome": "proactive_nudge", "reply": nudge,
            "evidence_trail": [{"label": "Risk", "value": r["risk"], "source": "monitor.first_pass"}],
        })
        yield {"node": "nudge", "label": "Proactive nudge (shadow-first)", "status": "done",
               "detail": nudge,
               "data": {"risk": r, "nudge": nudge, "model": meta["model"],
                        "shadow": True, "awb": r["awb"], "concern_id": concern["id"]}}


def _compose_nudge(profile: dict, risk: dict) -> tuple[str, dict]:
    provider, model = llm_registry.for_node("monitor_compose")
    prompt = (
        f"Compose a SHORT, warm, partner-protective nudge (1-2 sentences) for a Valmo delivery "
        f"partner named {profile.get('name')} in {profile.get('language', 'hinglish')}.\n"
        f"Risk: shipment {risk['awb']} is not on the correct manifest path — "
        f"{risk['detail']}. Warn them to fix it BEFORE the D{risk['hardstop_day']} hardstop breach "
        f"so they are not wrongly debited. Be helpful, not alarming. Reply with ONLY the nudge text."
    )
    res = provider.generate(prompt, model=model, node="monitor_compose", json_mode=False)
    return res.text.strip(), {"model": res.model, "provider": provider.name}
