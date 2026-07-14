"""Tools the conversation agent can call.

The LLM drives the conversation freely; these tools are the ONLY way it touches
data or money. `apply_policy` is the deterministic money path — it runs the
Executable Policy checks + trust gate + adversarial verifier and either acts
idempotently or escalates. The model proposes; policy disposes (BRD §4.3, §11).

Each dispatch returns (result_for_model, trace_events, concern_or_none, action).
"""
from __future__ import annotations

import uuid

from ..knowledge import store
from ..ledger import concern_log
from ..substrate import captain_context as ctx_svc
from ..trust import gate as trust_gate
from ..trust import verifier
from . import data_queries, policy_exec


# ── Function declarations (Gemini schema; maps 1:1 to Claude tools) ──────────
DECLARATIONS = [
    {
        "name": "search_sops",
        "description": "Search Valmo SOP/KT knowledge for how a process works, what the "
                       "policy is, or what a captain should do. Returns SOP snippets and any "
                       "form/template links. Use this to ground every process/how-to answer.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "what to look up"}}, "required": ["query"]},
    },
    {
        "name": "get_captain_context",
        "description": "Fetch THIS captain's own grounded records (profile, ledger debits, "
                       "losses, shipments). Call only when the captain's own account is relevant. "
                       "Only cite records that relate to what they asked — do not volunteer "
                       "unrelated debits.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "run_data_query",
        "description": "Run a read-only query for live/past data. query_name is one of: "
                       "shipment_status, scan_history, payout_status, loss_summary, cod_status.",
        "parameters": {"type": "object", "properties": {
            "query_name": {"type": "string"},
            "awb": {"type": "string", "description": "optional AWB for scan_history"}},
            "required": ["query_name"]},
    },
    {
        "name": "apply_policy",
        "description": "The ONLY way to move money or resolve a money case (reverse a wrong "
                       "debit/loss). Runs deterministic checks + trust gate + adversarial verifier "
                       "against the REAL loss record, then acts idempotently or escalates. Call ONLY "
                       "after the captain has given the AWB of the disputed shipment. You must NOT "
                       "state or promise any reversal/credit yourself — call this and explain its "
                       "result. For ANY loss/debit dispute (hardstop, shortage, damage, wrong RVP, "
                       "in-transit, dual-scan mismatch, etc.) pass disposition 'hardstop_loss' and the "
                       "awb — the engine looks up the real loss record by AWB, classifies it from the "
                       "data, and returns the outcome: it may reverse, tell the captain the loss "
                       "wasn't charged to them / is already revoked, or escalate to the owning team. "
                       "For COD / cash / payment-pendency issues do NOT use this — follow the SOP via "
                       "search_sops (gather CMS/bank source, txn id, date, attachment) and respond or escalate. "
                       "ONE identifier is enough to locate the case — an awb OR amount_inr OR txn_id — "
                       "but it MUST be one the captain actually raised in this conversation. "
                       "Never pass a debit/amount pulled from their account records that they did not "
                       "mention. Do NOT use this for non-money issues (ID blocked, general questions, "
                       "'please escalate') — answer or escalate those instead.",
        "parameters": {"type": "object", "properties": {
            "disposition": {"type": "string"},
            "awb": {"type": "string"},
            "amount_inr": {"type": "number"},
            "txn_id": {"type": "string"}}, "required": ["disposition"]},
    },
    {
        "name": "escalate_case",
        "description": "Hand a case to the right functional team when it genuinely cannot be "
                       "resolved in this conversation: no SOP/policy covers it, it needs a human / "
                       "functional team, or a non-money request you cannot action. This does NOT move "
                       "money. It builds a fully-worked case from the captain's context + what you "
                       "gathered this turn, files it to the accountable team inbox, and returns a "
                       "reference id, the team, and a realistic ETA so you can reassure the captain. "
                       "NEVER tell the captain to raise a ticket, file a complaint, or go elsewhere — "
                       "call this instead. Do NOT call this for questions you can already answer from "
                       "search_sops, and do NOT use it to move money (use apply_policy for disputes "
                       "with an identifier).",
        "parameters": {"type": "object", "properties": {
            "intent": {"type": "string", "description": "one-line description of THIS concern, plain English"},
            "reason": {"type": "string", "description": "why this needs a human/team"},
            "category": {"type": "string", "enum": ["no_sop", "needs_human"],
                         "description": "no_sop = no knowledge exists yet (also captures a knowledge gap); needs_human = a team must act"},
            "domain": {"type": "string",
                       "enum": ["payments", "fe_id", "losses_debits", "cash_cod", "consumables", "orders", "other"],
                       "description": "functional area for team routing: payments (payouts/invoices/withheld/RVP/consumable pay), "
                                      "fe_id (FE/rider ID (re)activation, BTS, pilot account), losses_debits, cash_cod, consumables, orders, other"},
            "fe_id": {"type": "string", "description": "any FE/rider ID the captain gave"},
            "hub": {"type": "string", "description": "the captain's hub / DC code, if known"},
            "awb": {"type": "string", "description": "any AWB the captain gave"},
            "amount_inr": {"type": "number", "description": "any ₹ amount the captain gave"},
            "txn_id": {"type": "string", "description": "any transaction / UTR / invoice / order id the captain gave"},
            "when": {"type": "string", "description": "any date / time period the captain referenced (e.g. 'last week', '25 Jun')"}},
            "required": ["intent", "reason", "category", "domain"]},
    },
]

# Functional-team routing for escalate_case (ETA comes from l3.TEAM_SLA — single source of truth).
_DOMAIN_TEAM = {
    "payments": "Payments (L2)",
    "fe_id": "FE Onboarding / Ops (L2)",
    "consumables": "Consumables (L2)",
    "orders": "Orders & Planning (L2)",
    "losses_debits": "Losses & Debits (L2)",
    "cash_cod": "Cash / COD (L2)",
    "other": "Functional team (L2/L3)",
}


def _evt(node, label, status="done", tier=None, detail="", data=None):
    return {"node": node, "label": label, "status": status, "tier": tier,
            "detail": detail, "data": data or {}}


def _act(decision: dict) -> dict:
    a = decision["action"]
    if a == "reverse_debit":
        return {"applied": True, "idempotency_key": f"rev::{decision.get('debit_id')}",
                "detail": f"Reversed ₹{decision.get('amount_inr')} on {decision.get('debit_id')} (idempotent)"}
    if a == "clear_pendency":
        return {"applied": True, "detail": f"Cleared COD pendency ₹{decision.get('amount_inr')} (idempotent)"}
    return {"applied": True, "detail": "Responded (no money movement)"}


def _attachment_evidence(attachments: list | None) -> list:
    """Turn captain-uploaded attachment metadata into evidence rows for the worked case."""
    return [{"label": f"Attachment: {a.get('filename', 'file')}",
             "value": a.get("mime", "file") + (f" · {round((a.get('size', 0) or 0) / 1024)}KB" if a.get("size") else ""),
             "source": "captain_upload"} for a in (attachments or [])]


def dispatch(name: str, args: dict, captain_id: str, context: dict, channel: str = "chat",
             attachments: list | None = None):
    if name == "search_sops":
        hits = store.retrieve(args.get("query", ""), k=8)
        # NOTE: we intentionally do NOT extract form links separately — a form/link
        # in an SOP is usually conditional on a specific branch. Keep it inline in the
        # snippet so the model only offers it when its stated condition is met.
        result = {"results": [{"title": h["title"], "snippet": h["text"][:700],
                               "type": h.get("knowledge_type", "procedure"),  # policy=rigid rule we own; procedure=functional/supply-chain process
                               "source": h["source_repo"]} for h in hits]}
        ev = _evt("knowledge", "Retrieve SOP knowledge", tier="fast",
                  detail=f"{len(hits)} SOP/KT sources consulted",
                  data={"sources": [{"title": h["title"], "kind": h["kind"],
                                     "source_repo": h["source_repo"], "score": h["score"]} for h in hits]})
        return result, [ev], None, None

    if name == "get_captain_context":
        p = context.get("profile", {})
        result = {
            "profile": {"name": p.get("name"), "hub": p.get("hub_name"), "tier": p.get("tier")},
            "debits": [{"id": d["id"], "amount_inr": d["amount_inr"], "date": d["date"],
                        "reason": d.get("reason"), "awb": d.get("awb")}
                       for d in context.get("ledger", []) if d.get("type") == "debit"],
            "losses": context.get("losses", []),
            "shipments": [{"awb": s["awb"], "status": s["status"],
                           "on_manifest_path": s.get("on_correct_manifest_path")}
                          for s in context.get("shipments", [])],
            "cod_pendency_inr": context.get("cash", {}).get("cod_pendency_inr", 0),
        }
        ev = _evt("ground", "Ground in live data", detail="Assembled grounded Captain Context",
                  data={"profile": p, "source": context.get("_sources", {})})
        return result, [ev], None, None

    if name == "run_data_query":
        qn = args.get("query_name", "")
        rows = data_queries._run(qn, {"awb": args.get("awb")}, context)
        ev = _evt("query", "Data query (LLM picks, DB runs)", tier="fast",
                  detail=f"ran '{qn}' → {len(rows)} row(s)", data={"query": qn, "rows": rows})
        return {"query": qn, "rows": rows}, [ev], None, None

    if name == "apply_policy":
        return _apply_policy(args, captain_id, context, channel, attachments=attachments)

    if name == "escalate_case":
        return _escalate_case(args, captain_id, context, channel, attachments=attachments)

    return {"error": f"unknown tool {name}"}, [], None, None


def _escalate_case(args: dict, captain_id: str, context: dict, channel: str, attachments: list | None = None):
    """Structured hand-to-human: file a fully-worked case to the accountable team inbox
    and return a reference id + ETA so the agent can reassure the captain. Never a dead-end."""
    from ..l3 import platform as l3   # reuse TEAM_SLA (single source of ETA truth)
    intent = (args.get("intent") or "").strip() or "captain request"
    reason = (args.get("reason") or "").strip()
    category = args.get("category", "needs_human")
    domain = args.get("domain", "other")

    team = _DOMAIN_TEAM.get(domain, "Functional team (L2/L3)")
    eta_hours = l3.TEAM_SLA.get(team, l3.DEFAULT_SLA)["sla_hours"]

    # Collate EVERY signal the captain gave — each helps the team locate the concern.
    _LABELS = {"fe_id": "FE ID", "hub": "Hub / DC", "awb": "AWB", "amount_inr": "Amount ₹",
               "txn_id": "Txn / UTR / invoice", "when": "When"}
    entities = {k: args.get(k) for k in ("fe_id", "hub", "awb", "amount_inr", "txn_id", "when")
                if args.get(k) is not None and str(args.get(k)).strip()}
    p = context.get("profile", {})
    worked = {"profile": {"name": p.get("name"), "hub": p.get("hub_name"), "tier": p.get("tier")},
              "domain": domain, "reason": reason, "category": category}
    evidence = [{"label": "Worked case", "value": f"[{domain}] {reason or intent}", "source": "escalate_case"}]
    for k, v in entities.items():
        evidence.append({"label": _LABELS.get(k, k) + " (captain-provided)", "value": str(v), "source": "captain_input"})
    evidence += _attachment_evidence(attachments)
    concern = {
        "id": "CNC-" + uuid.uuid4().hex[:8].upper(), "captain_id": captain_id, "channel": channel,
        "intent": intent[:80], "entities": entities, "disposition": domain,
        "policy_id": None, "policy_version": None,
        "action_taken": "escalate", "amount_inr": args.get("amount_inr"), "confidence": None,
        "outcome": "escalated",
        "evidence_trail": evidence,
        "attachments": attachments or [],
        "escalation_team": team,
    }
    stored = concern_log.append(concern)

    events = [_evt("escalate", "Escalate — worked case", detail=f"Routed to {team} (ETA ~{eta_hours}h)",
                   data={"team": team, "category": category, "reference_id": stored["id"],
                         "worked_case": worked})]
    if category == "no_sop":
        events += _capture_gap(intent, reason, captain_id)

    result = {"reference_id": stored["id"], "team": team, "eta_hours": eta_hours,
              "one_line_summary": f"Filed to {team}; they act within ~{eta_hours}h."}
    return result, events, stored, "escalate"


def _capture_gap(intent: str, reason: str, captain_id: str):
    """No-LLM knowledge-gap capture: queue a KT stub a human can author into an SOP, so the
    NEXT captain with this issue gets an instant answer (self-structuring knowledge, BRD §5)."""
    from ..kt import engine as kt_engine
    kt = kt_engine.log_gap(intent, reason, captain_id)
    return [_evt("learn", "Knowledge gap captured", tier="fast",
                 detail=f"Queued {kt['id']} for SOP authoring",
                 data={"kt_id": kt["id"], "auto_gap": True})]


def _apply_policy(args: dict, captain_id: str, context: dict, channel: str, attachments: list | None = None):
    disposition = args.get("disposition", "")
    entities = {k: args.get(k) for k in ("awb", "amount_inr", "txn_id") if args.get(k) is not None}
    events = []
    decision = policy_exec.execute(disposition, context, entities)
    events.append(_evt("policy", "Apply Executable Policy", tier="deep", detail=decision["reason"],
                       data={"checks_run": decision["checks_run"], "action": decision["action"],
                             "evidence_trail": decision["evidence_trail"],
                             "policy_version": (decision.get("policy") or {}).get("version"),
                             "confidence": decision["confidence"]}))

    verdict = trust_gate.evaluate(decision.get("policy") or {}, decision, context)
    events.append(_evt("gate", "Trust gate", tier="deep",
                       detail=("PASS" if verdict["passed"] else "BLOCK") +
                              f" · conf {verdict['confidence']:.2f}/{verdict['threshold']:.2f}",
                       data=verdict))

    verifier_agrees = None
    if verdict["passed"] and verdict["requires_adversarial_verify"]:
        v = verifier.verify(decision, decision["evidence_trail"], decision["reason"])
        verifier_agrees = v["agrees"]
        events.append(_evt("verify", "Adversarial verifier", tier="deep",
                           detail=("AGREES" if v["agrees"] else "REFUTES") + f" — {v['reason']}", data=v))

    resolved = verdict["passed"] and decision["action"] != "escalate" and verifier_agrees is not False
    if resolved:
        act = _act(decision)
        events.append(_evt("act", "ACT — idempotent write", detail=act["detail"], data=act))
        action = decision["action"]
        outcome = "resolved_in_conversation"
    else:
        action = "escalate"
        team = (decision.get("policy") or {}).get("escalation", {}).get("team", "Functional team (L2/L3)")
        events.append(_evt("escalate", "Escalate — worked case",
                           detail=f"Routed to {team}",
                           data={"team": team, "worked_case": {
                               "evidence_trail": decision.get("evidence_trail", []),
                               "checks_run": decision.get("checks_run", []), "reason": decision.get("reason")}}))
        outcome = "escalated"

    evidence = list(decision.get("evidence_trail", [])) + _attachment_evidence(attachments)
    concern = {
        "id": "CNC-" + uuid.uuid4().hex[:8].upper(), "captain_id": captain_id, "channel": channel,
        "intent": disposition, "entities": entities, "disposition": disposition,
        "policy_id": (decision.get("policy") or {}).get("id"),
        "policy_version": (decision.get("policy") or {}).get("version"),
        "action_taken": action, "amount_inr": decision.get("amount_inr"),
        "confidence": decision.get("confidence"), "outcome": outcome,
        "evidence_trail": evidence,
        "attachments": attachments or [],
    }
    if action == "escalate":
        concern["escalation_team"] = (decision.get("policy") or {}).get("escalation", {}).get(
            "team", "Losses & Debits (L2)")
    stored = concern_log.append(concern)

    result = {"action": action, "outcome": outcome, "amount_inr": decision.get("amount_inr"),
              "reason": decision["reason"], "gate_passed": verdict["passed"],
              "verifier_agrees": verifier_agrees, "concern_id": stored["id"],
              "evidence": [f"{e['label']}: {e['value']}" for e in decision.get("evidence_trail", [])],
              "escalation_team": (decision.get("policy") or {}).get("escalation", {}).get("team")
              if action == "escalate" else None}
    return result, events, stored, action
