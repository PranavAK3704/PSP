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
from ..trust import gate as trust_gate
from ..trust import verifier
from . import data_queries, policy_exec, write_mode


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
        "description": "A SUMMARY of THIS captain's own account: how many debits are on record, "
                       "how many are still open, totals debited / recovered / reversed, the mix of "
                       "loss types, their hub, and whether COD data is available at all. Call it "
                       "when you need to know WHETHER the captain has debits and of what kind — "
                       "e.g. before asking them which one they mean. It returns COUNTS AND TOTALS, "
                       "not individual records: there are no AWBs, dates or debit ids in it, so do "
                       "not try to cite or list specific debits from it. To work one specific "
                       "debit, ask the captain for its AWB and call apply_policy, which looks the "
                       "real record up itself. If cod_data_available is false, the cash system is "
                       "not connected — never state a COD figure, escalate instead.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "run_data_query",
        "description": "Run a read-only, named query for live/past data and get back a composed "
                       "ANSWER (one or two sentences of plain fact) plus how many rows it matched. "
                       "query_name is one of: shipment_status, scan_history, payout_status, "
                       "loss_summary, cod_status, load_status. Use load_status for ANY question "
                       "about order volume, load, allocation, capacity or 'why am I getting fewer "
                       "orders' — it returns the hub's order waterfall, which stage lost the "
                       "orders, the performance levers against their targets and the ₹ at stake. "
                       "Relay the `answer` faithfully in the captain's "
                       "language — it is computed from the real records, so do not embellish it, "
                       "and do not claim anything it does not say. When the answer states that a "
                       "data source is not connected, that is the truth: say so and escalate "
                       "rather than guessing a figure.",
        "parameters": {"type": "object", "properties": {
            "query_name": {"type": "string"},
            "awb": {"type": "string", "description": "optional AWB for scan_history"}},
            "required": ["query_name"]},
    },
    {
        "name": "apply_policy",
        "description": "The ONLY sanctioned way to reach a decision on a money case (a wrongly "
                       "applied debit/loss). Runs deterministic checks + trust gate + adversarial "
                       "verifier against the REAL loss record, then records a decision or escalates. "
                       "IT DOES NOT MOVE MONEY — no write endpoint exists, so a favourable outcome is "
                       "a recorded RECOMMENDATION that the owning team actions. The result carries "
                       "write_mode and money_moved:false; relay its `reason` as written and never "
                       "tell the captain a payment has been made. Call ONLY "
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
                       "'please escalate') — answer or escalate those instead. "
                       "ONE NON-MONEY EXCEPTION: for a LOAD / ORDER-VOLUME / capacity question, "
                       "call this with disposition 'load_planning' and NO identifier. That runs "
                       "the Orders & Planning policy against the captain's hub growth data and "
                       "returns a decided answer with an evidence trail — use it INSTEAD of "
                       "answering from run_data_query alone when the captain is asking why their "
                       "load is low, since it is the path that gets checked.",
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


# ── history-size controls ────────────────────────────────────────────────────
# Tuned against the corpus, not guessed. See the note in the search_sops branch: these two
# numbers are multiplied by conversation length, which is what makes them worth tuning at
# all. The retrieval cutoff in store.retrieve() already discards tangential chunks, so k=4
# is 4 RELEVANT hits rather than a truncated top-8.
SEARCH_K = 4
SNIPPET_CHARS = 320


def _evt(node, label, status="done", tier=None, detail="", data=None):
    return {"node": node, "label": label, "status": status, "tier": tier,
            "detail": detail, "data": data or {}}


# What each money action WOULD do, phrased as unrealised. Deliberately not a sentence in the
# past tense: "Reversed ₹244" and "would reverse ₹244" read completely differently to whoever
# is looking at the trace, and only one of them is true.
_WOULD = {
    "raise_for_reversal": lambda d: (f"raise ₹{d.get('amount_inr')} on "
                                     f"{d.get('debit_id') or d.get('awb') or 'the disputed debit'} "
                                     f"for reversal"),
    "clear_pendency": lambda d: f"clear COD pendency of ₹{d.get('amount_inr')}",
    "credit":         lambda d: f"credit ₹{d.get('amount_inr')}",
}


def _act(decision: dict) -> dict:
    """Record what the decision would do. NOTHING IS WRITTEN — see engine/write_mode.py.

    `applied` is GONE from the return, not set to False. Absent, so a stale reader doing
    `act.get("applied")` gets None and degrades falsy; a reader that had been trusting
    `applied: True` now fails the check instead of silently believing a write happened. The
    key was the lie, so the key is removed.

    A non-money action is not "simulated" — nothing was ever going to be written for a
    `respond`, so claiming simulation there would be its own small dishonesty in the other
    direction. Those keep a plain, accurate line.
    """
    a = decision.get("action", "")
    if a not in trust_gate.MONEY_ACTIONS:
        return {"simulated": False, "write_mode": write_mode.mode(), "money_moving": False,
                "detail": "Responded — no money movement, so nothing to write."}

    # Money action. This DOES NOT RAISE — see write_mode.assert_writable for why. Raising here
    # ran after the verifier had already agreed and before the concern was logged, so it threw
    # away the case, the trace and the money the verifier call had cost. A refusal is reported
    # as data and the caller escalates, which loses nothing.
    would = _WOULD.get(a, lambda d: f"perform {a}")(decision)
    mode = write_mode.mode()
    if mode == write_mode.LIVE:
        return {
            "simulated": False,
            "blocked": True,
            "write_mode": mode,
            "money_moving": True,
            "would_have": would,
            "detail": (f"BLOCKED — WRITE_MODE=live asks for a real write and none is possible. "
                       f"{write_mode.NO_WRITE_PATH} The case is preserved and escalated, not "
                       f"dropped."),
        }
    return {
        "simulated": True,
        "write_mode": mode,
        "money_moving": True,
        "would_have": would,
        "idempotency_key": f"rev::{decision.get('debit_id')}" if a == "raise_for_reversal" else None,
        "detail": (f"NOT WRITTEN — would {would}. No write endpoint exists: LMS reversal is a "
                   f"Kafka message consumed by its scheduler, and PSP has no producer. Recorded "
                   f"as a recommendation for L2."),
    }


def _money(v) -> float:
    """A ledger amount → float, never raising. The aggregate SUMS these, and a single
    malformed cell (a provider quirk, a '1,450' with a comma, a None) must not turn the whole
    tool call into an error — the old pass-through shape never parsed them at all, so this is
    a new raise site unless it is guarded."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v or 0).replace(",", "").replace("₹", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def captain_aggregate(context: dict) -> dict:
    """The captain's own records, PROJECTED to counts and totals for the model.

    What changed and why: this used to return `profile.name`, every debit row with its id /
    amount / date / reason / awb, and — the widest hole — `context["losses"]` completely
    unprojected, which under LocalDbProvider carries real AWBs and
    `metadata_attribution_marked_by` (real Meesho employee first names). All of it then sat
    in `sess.contents` permanently and was resent on every later step and every later turn.

    None of it was needed. The model uses this tool to decide WHAT TO DO — is there a debit
    at all, is it still open, is money already back — and every one of those decisions is
    answerable from a count. Individual rows are only ever needed for a specific AWB the
    captain named, and that path is `apply_policy`, which looks the row up itself.

    `profile.name` is dropped outright: replies are in Hinglish and address the captain
    directly, the frontend already knows their name locally, and under LocalDbProvider there
    IS no name — the provider refuses to invent one.

    Provider-agnostic by construction: computed from the context dict, so it is identical
    under DemoDataProvider, PrismProvider and LocalDbProvider. If the active provider can
    supply a precomputed summary (LocalDbProvider reads the materialised `captain_summary`
    table), that is preferred for the money fields and the row-derived counts are used to
    fill what only the context knows.
    """
    p = context.get("profile", {}) or {}
    ledger = context.get("ledger", []) or []
    losses = context.get("losses", []) or []
    shipments = context.get("shipments", []) or []
    cash = context.get("cash", {}) or {}

    debits = [d for d in ledger if d.get("type") == "debit"]
    credits = [d for d in ledger if d.get("type") == "credit"]
    open_debits = [d for d in debits if str(d.get("status", "")).lower() in ("", "pending", "posted")]
    # NOT every credit is a loss reversal. Under LocalDbProvider a credit IS a reversal
    # (partner_ledger stamps reason="loss_reversal"), but the demo provider's credits are
    # WEEKLY PAYOUTS — counting those as reversals told the model a loss had been reversed
    # when the captain had simply been paid, which is the kind of wrong fact it would then
    # relay with confidence. Split them by reason and report both.
    reversals = [d for d in credits if "reversal" in str(d.get("reason", "")).lower()]
    payouts = [d for d in credits if d not in reversals]

    mix: dict[str, int] = {}
    for l in losses:
        k = str(l.get("loss_type") or "other")
        mix[k] = mix.get(k, 0) + 1

    out = {
        "hub": p.get("hub_name") or p.get("hub") or "",
        "debits_on_record": len(debits),
        "open_debits": len(open_debits),
        "total_debited_inr": round(sum(_money(d.get("amount_inr")) for d in debits)),
        "reversals": len(reversals),
        "reversed_inr": round(sum(_money(d.get("amount_inr")) for d in reversals)),
        "payout_credits": len(payouts),
        "credited_inr": round(sum(_money(d.get("amount_inr")) for d in payouts)),
        "loss_type_mix": mix,
        "open_shipments": len(shipments),
        "shipments_off_manifest_path": sum(
            1 for s in shipments if s.get("on_correct_manifest_path") is False),
    }
    # COD is present-or-absent, never defaulted to 0: LocalDbProvider returns {} on purpose
    # because valmo.db holds no cash data, and "₹0 pending" is a specific, checkable, false
    # claim about a captain's cash position that the model would go on to quote.
    out["cod_pendency_inr"] = cash.get("cod_pendency_inr") if "cod_pendency_inr" in cash else None
    out["cod_data_available"] = "cod_pendency_inr" in cash

    # A provider-supplied summary is authoritative for the money fields — it is computed in
    # SQL over the full ledger rather than over the (row-capped) context slice.
    summary = context.get("summary") or {}
    for k in ("debits_on_record", "open_debits", "total_debited_inr", "recovered_inr",
              "pending_inr", "failed_inr", "reversals", "reversed_inr", "loss_type_mix",
              "first_debit", "last_debit"):
        if summary.get(k) not in (None, "", {}):
            out[k] = summary[k]
    if summary.get("hub"):
        out["hub"] = summary["hub"]
    return out


def _attachment_evidence(attachments: list | None) -> list:
    """Turn captain-uploaded attachment metadata into evidence rows for the worked case."""
    return [{"label": f"Attachment: {a.get('filename', 'file')}",
             "value": a.get("mime", "file") + (f" · {round((a.get('size', 0) or 0) / 1024)}KB" if a.get("size") else ""),
             "source": "captain_upload"} for a in (attachments or [])]


def dispatch(name: str, args: dict, captain_id: str, context: dict, channel: str = "chat",
             attachments: list | None = None, turn=None):
    """`turn` is the caller's per-turn spend meter, threaded through to apply_policy because
    the adversarial verifier it runs is an LLM call INSIDE the turn — see verifier.verify."""
    if name == "search_sops":
        # k=4 and a 320-char snippet, down from k=8 / 700. This is the largest PERMANENT
        # contributor to history growth in the platform: a search_sops result is appended to
        # sess.contents and resent in full on every later step and every later turn, so its
        # size is multiplied by the length of the conversation, not paid once. Measured at
        # k=8 it was ~1,143 tokens carried forever per search.
        # The 700 was dead code besides — store.retrieve already truncates at 500, so the
        # slice never fired and the real cap was invisible at this call site.
        hits = store.retrieve(args.get("query", ""), k=SEARCH_K)
        # NOTE: we intentionally do NOT extract form links separately — a form/link
        # in an SOP is usually conditional on a specific branch. Keep it inline in the
        # snippet so the model only offers it when its stated condition is met.
        result = {"results": [{"title": h["title"], "snippet": h["text"][:SNIPPET_CHARS],
                               "type": h.get("knowledge_type", "procedure"),  # policy=rigid rule we own; procedure=functional/supply-chain process
                               "source": h["source_repo"]} for h in hits]}
        ev = _evt("knowledge", "Retrieve SOP knowledge", tier="fast",
                  detail=f"{len(hits)} SOP/KT sources consulted",
                  data={"sources": [{"title": h["title"], "kind": h["kind"],
                                     "source_repo": h["source_repo"], "score": h["score"]} for h in hits]})
        return result, [ev], None, None

    if name == "get_captain_context":
        result = captain_aggregate(context)
        # The ROWS go here — to the trace, which is local, auditable, and never sent to the
        # model. This is the correct home for them: a reviewer replaying the concern needs
        # to see exactly which debits the aggregate was computed from.
        ev = _evt("ground", "Ground in live data",
                  detail=f"{result['debits_on_record']} debit(s) on record · "
                         f"{result['open_debits']} open · aggregates only to the model",
                  data={"profile": context.get("profile", {}),
                        "source": context.get("_sources", {}),
                        "aggregate": result,
                        "rows": {"ledger": context.get("ledger", []),
                                 "losses": context.get("losses", [])}})
        return result, [ev], None, None

    if name == "run_data_query":
        qn = args.get("query_name", "")
        answer, rows = data_queries.run_and_compose(qn, {"awb": args.get("awb")}, context)
        ev = _evt("query", "Data query (LLM picks, DB runs, CODE composes)", tier="fast",
                  detail=f"ran '{qn}' → {len(rows)} row(s) → composed answer",
                  data={"query": qn, "answer": answer, "rows": rows})
        # The model gets the composed sentence and the row count. Not the rows — a tool
        # result is appended to sess.contents permanently and resent on every later step.
        return {"query": qn, "answer": answer, "rows_found": len(rows)}, [ev], None, None

    if name == "apply_policy":
        return _apply_policy(args, captain_id, context, channel, attachments=attachments, turn=turn)

    if name == "escalate_case":
        return _escalate_case(args, captain_id, context, channel, attachments=attachments)

    # An unknown tool name left NO trace event at all, so the one failure mode that means
    # "the model called something that doesn't exist" was the one invisible to a reviewer.
    return ({"error": f"unknown tool {name}", "available": [d["name"] for d in DECLARATIONS]},
            [_evt("explain", "Unknown tool requested", status="blocked", tier="fast",
                  detail=f"the model called '{name}', which is not a declared tool",
                  data={"tool": name})], None, None)


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


def _apply_policy(args: dict, captain_id: str, context: dict, channel: str,
                  attachments: list | None = None, turn=None):
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
        v = verifier.verify(decision, decision["evidence_trail"], decision["reason"], turn=turn)
        verifier_agrees = v["agrees"]
        events.append(_evt("verify", "Adversarial verifier", tier="deep",
                           detail=("AGREES" if v["agrees"] else "REFUTES") + f" — {v['reason']}", data=v))

    resolved = verdict["passed"] and decision["action"] != "escalate" and verifier_agrees is not False
    act = _act(decision) if resolved else None
    if act is not None and act.get("blocked"):
        # A write was required and is impossible. Fall through to the escalation branch so the
        # concern is still logged, the trace still reaches the panel, and the captain still gets
        # an answer — the one thing that must never happen here is the case disappearing.
        events.append(_evt("act", "ACT — BLOCKED (write_mode=live, no write path exists)",
                           status="blocked", detail=act["detail"], data=act))
        resolved = False
    if resolved:
        action = decision["action"]
        simulated = bool(act.get("simulated"))
        # The label and status TELL THE TRUTH. "ACT — idempotent write" on a step that writes
        # nothing is the single most misleading line in the trace, and status="blocked" makes
        # the existing dot styling render it as not-done without needing new CSS.
        events.append(_evt(
            "act",
            "ACT — simulated write (no write endpoint exists)" if simulated
            else "ACT — no write required",
            status="blocked" if simulated else "done",
            detail=act["detail"], data=act))
        # A NEW outcome, but only where a write was implied. A `respond` never had anything to
        # write, so relabelling it would be its own inaccuracy — and `concern_log.stats()`
        # branches on `action_taken`, not `outcome`, so this cannot skew the counts.
        outcome = "simulated_resolution" if simulated else "resolved_in_conversation"
        if simulated:
            # decision["reason"] says "this debit is reversed". Nothing was reversed. Same
            # hazard the escalate branch below already guards against, and the same fix:
            # relay what actually happened. The engine can recommend; it cannot pay.
            team = (decision.get("policy") or {}).get("escalation", {}).get(
                "team", "Losses & Debits (L2)")
            relay_reason = (
                f"{decision['reason']} On the system side I've recorded this as a confirmed "
                f"recommendation to {team} — I can't move the money myself, so please expect "
                f"the credit to come through them rather than from me.")
        else:
            relay_reason = decision["reason"]
    else:
        action = "escalate"
        team = (decision.get("policy") or {}).get("escalation", {}).get("team", "Functional team (L2/L3)")
        # Do NOT relay the (unexecuted) resolution reason on escalation — it says "this debit is
        # reversed", which would tell the captain money moved when nothing was written and the case
        # only went to L2. Relay an escalation-truthful line instead.
        relay_reason = (f"I couldn't confirm a fix from the records, so I've routed this to {team} "
                        f"for review — you'll be updated with the outcome.")
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
        # On the record, per concern: whether anything was actually written. Auditable later
        # without having to reconstruct which build was deployed at the time.
        "write_mode": write_mode.mode(),
    }
    if action == "escalate":
        concern["escalation_team"] = (decision.get("policy") or {}).get("escalation", {}).get(
            "team", "Losses & Debits (L2)")
    stored = concern_log.append(concern)

    result = {"action": action, "outcome": outcome, "amount_inr": decision.get("amount_inr"),
              "reason": relay_reason, "gate_passed": verdict["passed"],
              # So the model cannot narrate a payment that did not happen. `reason` above is
              # already escalation- and simulation-truthful; this is the belt to that braces.
              "write_mode": write_mode.mode(),
              "money_moved": False,
              "verifier_agrees": verifier_agrees, "concern_id": stored["id"],
              "evidence": [f"{e['label']}: {e['value']}" for e in decision.get("evidence_trail", [])],
              "escalation_team": (decision.get("policy") or {}).get("escalation", {}).get("team")
              if action == "escalate" else None}
    return result, events, stored, action
