"""L3 functional-team platform (revised vision).

Escalations land here in an accountable inbox — NOT a chatbot. Each escalated
Concern carries its fully-worked context. An SLA timer starts on landing; if it
breaches, it climbs a governance ladder (team → Kaizen → GM/CXO). Each functional
team OWNS its experience (its own metrics), because these outcomes are theirs to
control, not partner-support's.

Governance (severity / recoverability / scalability scoring) is a PLACEHOLDER per
the product owner — a stub scorer is wired so the shape is real and swappable.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..ledger import concern_log

# Per-team SLA (hours) + escalation ladder. Config, not code (BRD §15.2).
TEAM_SLA = {
    "Payments (L2)": {"sla_hours": 24, "ladder": ["Team POC", "Kaizen", "GM", "Finance"]},
    "FE Onboarding / Ops (L2)": {"sla_hours": 12, "ladder": ["Team POC", "Kaizen", "GM"]},
    "Consumables (L2)": {"sla_hours": 36, "ladder": ["Team POC", "Kaizen", "GM"]},
    "Orders & Planning (L2)": {"sla_hours": 24, "ladder": ["Team POC", "Kaizen", "GM"]},
    "Losses & Debits (L2)": {"sla_hours": 24, "ladder": ["Team POC", "Kaizen", "GM"]},
    "Cash / COD (L2)": {"sla_hours": 12, "ladder": ["Team POC", "Kaizen", "GM"]},
    "RVP / Returns (L2)": {"sla_hours": 24, "ladder": ["Team POC", "Kaizen", "GM"]},
    "Quality / QC (L2)": {"sla_hours": 24, "ladder": ["Team POC", "Kaizen", "GM"]},
    "Seller Ops (L2)": {"sla_hours": 36, "ladder": ["Team POC", "Kaizen", "GM"]},
    "Tech / Data Platform (L3)": {"sla_hours": 48, "ladder": ["Team POC", "Kaizen", "GM", "CXO"]},
    "RTO / Logistics (L2)": {"sla_hours": 24, "ladder": ["Team POC", "Kaizen", "GM"]},
    "Functional team (L2/L3)": {"sla_hours": 48, "ladder": ["Team POC", "Kaizen", "GM", "CXO"]},
}
DEFAULT_SLA = {"sla_hours": 48, "ladder": ["Team POC", "Kaizen", "GM", "CXO"]}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hours_since(iso: str) -> float:
    try:
        t = datetime.fromisoformat(iso)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (_now() - t).total_seconds() / 3600.0
    except Exception:  # noqa: BLE001
        return 0.0


def _governance_placeholder(concern: dict) -> dict:
    """PLACEHOLDER scorer. Real severity/recoverability/scalability model TBD."""
    amt = concern.get("amount_inr") or 0
    severity = "high" if amt >= 3000 else "medium" if amt >= 500 else "low"
    return {"severity": severity, "recoverability": "unknown", "scalability": "unknown",
            "_placeholder": True}


def _team_of(concern: dict) -> str:
    # escalate_case stamps the routed team directly; money escalations derive it from policy.
    if concern.get("escalation_team"):
        return concern["escalation_team"]
    from ..knowledge import policies as pol
    p = pol.get_policy(concern.get("disposition", ""))
    return (p or {}).get("escalation", {}).get("team", "Functional team (L2/L3)")


def inbox(include_resolved: bool = False) -> list[dict]:
    """Escalated Concerns as L3 work items with SLA + breach status. Cases that have been
    resolved-back (a follow-up concern links to them) drop out of the active queue."""
    all_concerns = concern_log.all_concerns()
    resolved_ids = {c["resolves_concern_id"] for c in all_concerns if c.get("resolves_concern_id")}
    items = []
    for c in all_concerns:
        if c.get("outcome") != "escalated":
            continue
        if c.get("id") in resolved_ids and not include_resolved:
            continue
        team = _team_of(c)
        sla = TEAM_SLA.get(team, DEFAULT_SLA)
        age = _hours_since(c.get("logged_at", ""))
        breached = age > sla["sla_hours"]
        # ladder rung by how far past SLA (0 = within, then each extra SLA window climbs a rung)
        rung = 0 if not breached else min(int(age // sla["sla_hours"]), len(sla["ladder"]) - 1)
        items.append({
            "concern_id": c.get("id"), "captain_id": c.get("captain_id"),
            "disposition": c.get("disposition"), "intent": c.get("intent"),
            "amount_inr": c.get("amount_inr"), "team": team,
            "age_hours": round(age, 1), "sla_hours": sla["sla_hours"],
            "breached": breached, "escalation_rung": sla["ladder"][rung],
            "ladder": sla["ladder"], "governance": _governance_placeholder(c),
            "worked_case": {"evidence_trail": c.get("evidence_trail", []), "reply": c.get("reply")},
            "logged_at": c.get("logged_at"),
        })
    return items


def resolve(concern_id: str, note: str = "", resolver: str = "L3") -> dict:
    """L3 resolves an escalated case → append a linked follow-up concern (the log is
    append-only) that (a) drops the case from the active inbox and (b) becomes the
    captain-facing follow-up. Idempotent: a second call for an already-resolved case no-ops."""
    import uuid
    all_concerns = concern_log.all_concerns()
    orig = next((c for c in all_concerns if c.get("id") == concern_id), None)
    if not orig:
        return {"error": "concern not found"}
    if any(c.get("resolves_concern_id") == concern_id for c in all_concerns):
        return {"ok": True, "already_resolved": True, "resolved_concern_id": concern_id}
    followup = concern_log.append({
        "id": "CNC-" + uuid.uuid4().hex[:8].upper(),
        "captain_id": orig.get("captain_id"), "channel": "l3",
        "intent": f"Resolved: {orig.get('intent', '')}"[:80],
        "disposition": orig.get("disposition"), "action_taken": "resolved_by_l3",
        "amount_inr": orig.get("amount_inr"), "outcome": "l3_resolved",
        "resolves_concern_id": concern_id, "resolution_note": note, "resolver": resolver,
        "reply": note or "Your escalated case has been resolved by the team.",
        "evidence_trail": orig.get("evidence_trail", []),
    })
    return {"ok": True, "followup": followup, "resolved_concern_id": concern_id}


def cases(captain_id: str) -> list[dict]:
    """Captain-facing 'My Cases': every escalated case this captain has, with live status
    (open / resolved) and the resolution note once L3 resolves it. Powers the widget +
    polling on the Captain Panel. Newest first."""
    all_concerns = concern_log.all_concerns()
    resolutions = {c["resolves_concern_id"]: c for c in all_concerns
                   if c.get("resolves_concern_id")}
    out = []
    for c in all_concerns:
        if c.get("outcome") != "escalated":
            continue
        if c.get("captain_id") != captain_id:
            continue
        team = _team_of(c)
        res = resolutions.get(c.get("id"))
        out.append({
            "id": c.get("id"), "intent": c.get("intent"), "disposition": c.get("disposition"),
            "team": team, "amount_inr": c.get("amount_inr"),
            "entities": c.get("entities", {}), "attachments": c.get("attachments", []),
            "eta_hours": TEAM_SLA.get(team, DEFAULT_SLA)["sla_hours"],
            "logged_at": c.get("logged_at"),
            "status": "resolved" if res else "open",
            "resolution_note": (res or {}).get("resolution_note"),
            "resolved_at": (res or {}).get("logged_at"),
        })
    return out


def team_metrics() -> list[dict]:
    """Ownership view — each team's queue + breach count (they own their experience)."""
    by_team: dict[str, dict] = {}
    for it in inbox():
        t = it["team"]
        m = by_team.setdefault(t, {"team": t, "open": 0, "breached": 0, "sla_hours": it["sla_hours"]})
        m["open"] += 1
        m["breached"] += 1 if it["breached"] else 0
    return list(by_team.values())
