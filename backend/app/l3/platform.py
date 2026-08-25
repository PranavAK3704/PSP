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

import threading
from datetime import datetime, timezone

from ..ledger import concern_log

_resolve_lock = threading.Lock()   # make resolve()'s check-then-append atomic (no double-resolve)

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


def inbox(include_test: bool = False) -> list[dict]:
    """Escalated Concerns as L3 work items with SLA + breach status. Cases that have been
    resolved-back (a follow-up concern links to them) drop out of the active queue.

    Harness rows are EXCLUDED by default. A test run is not somebody's escalation, and a desk
    whose queue is padded with them is a desk nobody trusts — the same reasoning as the captain's
    case strip, which was showing 98 cases for one captain.

    `unclassified` is KEPT, and that is a deliberate compromise worth naming: it is the bulk of
    the backlog (rows written before provenance existed), so the queue still looks long. Hiding
    them would be inventing an empty desk, and a real one may be in there. What fixes the number
    is live traffic, not a filter.
    """
    all_concerns = concern_log.all_concerns()
    if not include_test:
        all_concerns = [c for c in all_concerns
                        if c.get("source") not in concern_log.NON_INBOUND_SOURCES]
    resolved_ids = {c["resolves_concern_id"] for c in all_concerns if c.get("resolves_concern_id")}
    items = []
    for c in all_concerns:
        if c.get("outcome") != "escalated":
            continue
        if c.get("id") in resolved_ids:
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


#: What an L3 member can conclude. `resolved` closes the case; `need_input` sends it back to the
#: captain WITHOUT closing it (they were missing a photo, an AWB, a date); `rejected` closes it
#: with a reason the captain can read. The middle one is the reason this is not a boolean: a case
#: that cannot be resolved yet is not the same as one that has been refused, and collapsing them
#: is how a captain ends up waiting on a case nobody is working.
OUTCOMES = ("resolved", "need_input", "rejected")

#: Refused as a partner-facing message. Every one of the four real resolutions in the ledger
#: is the literal string "done" — which is what a `window.prompt` box gets you, and it is not
#: something you can send a person who has been waiting a day for their money.
_NON_ANSWERS = {"done", "ok", "okay", "fixed", "resolved", "closed", "na", "n/a", "-", "yes",
                "no", "completed", "complete", "sorted", "handled", "actioned", "yep", "k"}
MIN_REPLY_CHARS = 25


def validate_reply(text: str) -> str:
    """"" if the message is fit to send, else why not. Enforced server-side, not just in the UI —
    the UI is one client and the ledger is forever."""
    t = (text or "").strip()
    if not t:
        return "A message for the captain is required."
    if t.lower().rstrip(".!") in _NON_ANSWERS:
        return (f"{t!r} is not an answer. Say what was found and what happens next — this is "
                f"what the captain reads.")
    if len(t) < MIN_REPLY_CHARS:
        return f"Too short ({len(t)} chars). At least {MIN_REPLY_CHARS} — say what you did."
    return ""


def resolve(concern_id: str, note: str = "", resolver: str = "L3", *,
            internal_note: str = "", outcome: str = "resolved",
            attachments: list | None = None) -> dict:
    """L3 resolves an escalated case → append a linked follow-up concern (the log is
    append-only) that (a) drops the case from the active inbox and (b) becomes the
    captain-facing follow-up. Idempotent: a second call for an already-resolved case no-ops.

    ── FOUR THINGS A RESOLUTION NEEDS THAT ONE STRING CANNOT CARRY ───────────────────────────
    `note` is the PARTNER-FACING message and is validated. `internal_note` never reaches the
    captain — it is what one L3 member tells the next one, and without somewhere to put it the
    partner-facing message becomes the dumping ground for both. `attachments` records the
    evidence relied on (metadata only: filename, mime, size — the file itself is never stored
    here, matching the captain-upload path). `outcome` distinguishes closing a case from sending
    it back for more information.

    `need_input` deliberately does NOT set `resolves_concern_id`, so the case STAYS in the active
    inbox. It has been answered, not closed — and a queue that loses a case the moment somebody
    types into it is how things go quiet.
    """
    import uuid
    with _resolve_lock:   # check-then-append must be atomic, else two clicks double-resolve one case
        all_concerns = concern_log.all_concerns()
        orig = next((c for c in all_concerns if c.get("id") == concern_id), None)
        if not orig:
            return {"error": "concern not found"}
        if any(c.get("resolves_concern_id") == concern_id for c in all_concerns):
            return {"ok": True, "already_resolved": True, "resolved_concern_id": concern_id}
        problem = validate_reply(note)
        if problem:
            return {"error": problem}
        if outcome not in OUTCOMES:
            return {"error": f"outcome must be one of {', '.join(OUTCOMES)}"}
        followup = concern_log.append({
            "id": "CNC-" + uuid.uuid4().hex[:8].upper(),
            "captain_id": orig.get("captain_id"), "channel": "l3",
            # An L3 resolution SHADOWS an original concern (see `resolves_concern_id`); it is
            # an operator action, not an inbound one.
            "source": "l3",
            "intent": f"Resolved: {orig.get('intent', '')}"[:80],
            "disposition": orig.get("disposition"), "action_taken": "resolved_by_l3",
            "amount_inr": orig.get("amount_inr"), "outcome": "l3_resolved",
            # `need_input` does NOT link the original, so the case stays open in the queue.
            **({"resolves_concern_id": concern_id} if outcome != "need_input" else {}),
            "resolution_note": note, "resolver": resolver,
            "l3_outcome": outcome,
            # Never sent to the captain, never rendered on their panel. Kept on the record so the
            # next person to touch this case can see what the last one found.
            "internal_note": internal_note,
            "reply": note,
            "attachments": attachments or [],
            "evidence_trail": orig.get("evidence_trail", []),
        })
    return {"ok": True, "followup": followup, "resolved_concern_id": concern_id}


def cases(captain_id: str, *, limit: int = 6, include_test: bool = False) -> list[dict]:
    """Captain-facing 'My Cases': the escalated cases this captain has, newest first, with live
    status (open / resolved) and the resolution note once L3 resolves it.

    ── TWO FILTERS THAT ARE NOT COSMETIC ────────────────────────────────────────────────────
    Measured on the real ledger: this returned **98 open cases** for captain 20020388788. A
    captain with 98 unresolved escalations is not a product, it is a bug rendering — and they
    were not their cases. They are harness rows (`check_op` and `check_followups_e2e` both drive
    that id) and rows from before provenance existed, sitting on a real partner id.

      · `source == "harness"` is EXCLUDED. A test run is not something a captain escalated, and
        showing it on their screen is the same category of error as counting it on the deck.
        `unclassified` is KEPT — it may genuinely be theirs, and silently hiding a real case
        from the person waiting on it is the worse failure of the two.
      · `limit` caps the list. This is a ~90px strip beside a chat widget, not a queue view;
        the L3 desk is where a backlog belongs. Newest first, so a case raised during a
        conversation is the top row.

    `include_test=True` restores the unfiltered list for the internal bench, which legitimately
    wants to see what it just wrote.
    """
    all_concerns = concern_log.all_concerns()
    resolutions = {c["resolves_concern_id"]: c for c in all_concerns
                   if c.get("resolves_concern_id")}
    out = []
    for c in all_concerns:
        if c.get("outcome") != "escalated":
            continue
        if c.get("captain_id") != captain_id:
            continue
        if not include_test and c.get("source") == "harness":
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
            # So the strip can label a row a captain did not raise. Rendered as a chip on the
            # bench and suppressed on the captain's own view.
            "source": c.get("source", "unclassified"),
        })
        if len(out) >= limit:
            break                     # all_concerns() is newest-first, so this keeps the newest
    return out


#: A proactive finding names a MODULE of the captain's own panel, not a support queue. That is
#: the difference between "you have a problem" and "your problem is on this screen" — and it is
#: the only way a nudge is actionable without a conversation. Keys match the module keys in
#: frontend/src/captain/CaptainPanelReplica.jsx.
_NUDGE_MODULE = {
    "risk_detected":    "loss-management",
    "proactive_nudge":  "loss-management",
    "capacity_risk":    "dc-capacity",
    "pendency_risk":    "cash-pendency",
    "payment_risk":     "payments",
}


def nudges(captain_id: str, limit: int = 5) -> list[dict]:
    """What the monitor noticed about this captain, newest first, addressed to them.

    ── WHY THIS ENDPOINT HAS TO EXIST ────────────────────────────────────────────────────────
    `monitor.py` scans the shipment ledger unasked, bands every row by severity, and composes a
    nudge. Then it writes a concern row and stops. Measured: the captain has never seen one — the
    only consumer of `channel: "proactive"` rows was an internal trace view on a page no captain
    opens. The most defensible thing the platform does (noticing before anyone complains) was
    invisible to the person it was for.

    A nudge is NOT a support case, so it is deliberately not in `cases()`: nobody escalated it,
    there is no SLA, and it must never appear as something the captain raised. It carries a
    `module` so the panel can badge the screen the problem is actually on, and `detected_only`
    when a model could not be reached — a detection with no composed text is still a real finding
    ("26 shipments at risk, ₹11,567") and burying it because the prose step failed would be the
    same mistake `monitor.py` used to make by not logging at all.
    """
    out = []
    for c in concern_log.all_concerns():          # newest first
        if c.get("channel") != "proactive" or c.get("captain_id") != captain_id:
            continue
        disp = c.get("disposition") or ""
        text = (c.get("reply") or "").strip()
        out.append({
            "id": c.get("id"),
            "module": _NUDGE_MODULE.get(disp, "loss-management"),
            "disposition": disp,
            "headline": c.get("intent") or "",
            "message": text,
            # No composed prose (no key, refused prompt) — the detection still stands.
            "detected_only": not text,
            "amount_inr": _amt(c.get("amount_inr")),
            "shipments": len(c.get("awbs") or []),
            "evidence": c.get("evidence_trail") or [],
            "at": c.get("logged_at"),
        })
        if len(out) >= limit:
            break
    return out


def _amt(v):
    """The ledger round-trips through Turso, which returns INTEGER/REAL as JSON strings."""
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def team_metrics() -> list[dict]:
    """Ownership view — each team's queue + breach count (they own their experience)."""
    by_team: dict[str, dict] = {}
    for it in inbox():
        t = it["team"]
        m = by_team.setdefault(t, {"team": t, "open": 0, "breached": 0, "sla_hours": it["sla_hours"]})
        m["open"] += 1
        m["breached"] += 1 if it["breached"] else 0
    return list(by_team.values())
