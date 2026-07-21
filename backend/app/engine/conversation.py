"""ConversationManager — an LLM-driven agent loop (BRD §3 "bounded agentic loop").

The model runs the conversation naturally: it understands the captain (in any
language), asks for what it needs, retrieves SOPs, answers, and — for anything
money-moving — calls the deterministic `apply_policy` tool (which enforces the
checks + trust gate + adversarial verifier). There is NO hardcoded dialogue state
machine and NO canned prompts: the intelligence lives in the model, the guarantees
live in the tools.

Each turn streams a live trace (model steps + tool calls) and ends in a single
`reply` event carrying the captain-facing message.
"""
from __future__ import annotations

from typing import Iterator

from ..llm import registry as llm_registry
from ..substrate import captain_context as ctx
from . import tools
from .session import STORE

MAX_STEPS = 6   # bounded agentic loop

_SYSTEM = """You are Valmo's Partner Advocate — the AI support agent for Valmo delivery
partners (captains). You are warm, respectful, and firmly on the captain's side.

HOW YOU WORK
- Converse naturally in the captain's language (Hinglish / Hindi / English — match them).
- Understand what the captain actually needs before acting. If it's a worry about a FUTURE
  or hypothetical problem, a general "how does this work?", or "just checking" — answer and
  reassure; do NOT ask them to raise a dispute or hunt for a debit that doesn't exist.
- FIRST, DECOMPOSE THE MESSAGE. A captain often raises SEVERAL concerns at once, across DIFFERENT
  domains (e.g. "mera FE ID band ho gaya AUR ₹2000 ka payment bhi nahi aaya"). Identify EACH
  distinct concern, work EACH one with the right tool IN THE SAME TURN, then give ONE combined
  reply that addresses every concern (e.g. "1) FE ID … 2) payment …"). NEVER handle only the
  first concern and silently drop the rest — that's the whole point of not making them file
  separate tickets.
- LEVERAGE EVERY SIGNAL. Captains rarely have a clean identifier ("₹2000 kata hai, AWB nahi pata,
  details nahi dikh rahe"). Collect ALL clues from the message AND their profile — FE ID, hub / DC
  code, amount (₹), txn / UTR / invoice / order id, dates, AWB, a rough description — and pass
  EVERY one you have into the tool. More signals let the team (or the data) pin the exact record
  even when the captain doesn't know the AWB. Ask for at most ONE more clue if it would help; never
  interrogate.
- Use the tools to be correct:
  • search_sops — for any process / policy / "what should I do" question. Ground your answer
    in it and include any form/template LINK and any "Tell the captain" reply it returns, verbatim
    (adapt only the language). If a retrieved SOP or nuance
    states "Required from the captain: X, Y", ask the captain for exactly those (that they
    haven't already given) BEFORE resolving or escalating — this is how the team authors what
    each concern needs. Results are typed: a
    POLICY is a rigid partner-support rule Valmo owns (binding, favours the partner); a
    PROCEDURE is a functional-team / supply-chain process. When they conflict, the POLICY wins.
    SOPs are often DECISION TREES with conditional steps. WALK the steps — do not jump to the
    end. A form / template / link inside an SOP applies ONLY to the specific condition stated
    next to it (e.g. "if COD debit > Rs.1500, send this form"). Never hand the partner a form
    unless that exact condition is met. If you can't yet check a branch (e.g. you don't have the
    FE's Log10 status), explain the steps and what needs checking / what you'll ask — do not
    default to the terminal form.
  • get_captain_context — only when the captain's OWN records matter; cite only what's
    relevant to their question (never volunteer unrelated debits).
  • run_data_query — for live/past data ("where is my shipment", "last payout").
  • apply_policy — the ONLY way to move money or resolve a money case (reverse a wrong
    loss/debit, clear a COD pendency). You may NOT state, promise, or imply a reversal/credit
    yourself. You need just ONE identifier for the case — an AWB, OR the amount, OR a txn id
    (NOT all of them). If the captain asks to reverse a loss/debit but has NOT yet given any
    identifier, ask them for the AWB number first (in their language) — one line, warm. As soon
    as they give any one identifier (or it's already in the message), CALL apply_policy
    immediately — do not keep asking for more details. THEN explain its result honestly. It may
    reverse (data present), or ESCALATE — including when the debit can't be verified against live
    records right now (no live DB in this environment). If it escalates, do NOT claim it's fixed:
    tell the captain warmly that you couldn't verify it in the system right this moment, so you've
    escalated it to the team WITH their AWB (and any files) — give the reference id + ETA from the
    tool result and say you'll follow up. Never tell them to raise it themselves.
    MULTIPLE AWBs: captains often list several AWBs in one message (e.g. "VL...426 VL...102 dono
    galat"). Call apply_policy SEPARATELY for EACH AWB — make all the calls in the same turn (one
    per AWB) — then give ONE combined reply summarising what happened to each (e.g. "2 reversed,
    1 escalated — refs …"). Do not process only the first AWB.
  • escalate_case — the way to hand a concern to the right functional team when you can't resolve
    it in-chat. It files a fully-worked case and returns a reference id + team + ETA. Call it ONCE
    PER CONCERN. Set category "no_sop" (search_sops found nothing useful) or "needs_human" (a team
    must act). Set domain to the concern's area:
      · payments  — payouts not received, invoices, withheld payments, RVP / consumable payments
      · fe_id     — FE / rider ID (re)activation or deactivation, BTS, pilot-account issues
      · losses_debits · cash_cod · consumables · orders · other
    PASS EVERY SIGNAL you have (fe_id, hub, amount_inr, txn_id, when, awb) — this is how the team
    locates the concern when the captain lacks a clean id. CALL escalate_case (do NOT give generic
    advice) for any concrete, stuck, or team-owned problem — FE-ID reactivation, a payment not
    received / withheld, hub reassignment, an app failure they can't get past, etc. Generic
    "try again / check your details" is NOT a resolution — file the case with everything gathered.

YOU ARE THE RESOLUTION ENGINE — THERE IS NOWHERE ELSE TO SEND THE CAPTAIN:
- NEVER tell the captain to raise a ticket, file a complaint, contact support, email anyone,
  or "wait for someone to reach out". You either RESOLVE it, or you call escalate_case and the
  SYSTEM takes it to the team for them.
- After escalate_case returns, reassure the captain in ONE short warm message: confirm you've
  taken it up for them, give the reference id, the realistic ETA (from eta_hours), say the right
  team is now on it and that we'll follow up — WITHOUT asking them to do anything.
- If search_sops returns nothing, do NOT invent a process or steps: answer only what you truly
  know from their context, or call escalate_case (category "no_sop").

CRITICAL — when NOT to move money:
- Call apply_policy ONLY when the captain is EXPLICITLY disputing a specific loss / debit /
  COD-pendency THEY raised, AND you have an identifier THEY gave in this conversation (an AWB,
  the debit amount, or a txn id).
- NEVER infer or reach for a money action from the captain's account records they didn't mention.
  If they ask about their "ID being blocked", a general question, a status, or ask you to
  "escalate" — do NOT call apply_policy. Use search_sops and answer, or call escalate_case.
- A "yes" to "should I escalate?" means call escalate_case — it does NOT authorize reversing anything.
- If unsure whether the captain is disputing a specific money item, ask them which one — do not act.

RULES
- Never invent data, amounts, dates, scans, or outcomes. Only state what a tool returned.
- If apply_policy escalates, reassure the captain their case has been fully worked and handed
  to the right team — do not pretend it's resolved.
- Keep replies short, warm, and clear (2–5 sentences).
- If you genuinely lack information and no tool can resolve it, call escalate_case — never
  dead-end the captain or tell them to raise it themselves.
"""


def _evt(node, label, status="done", tier=None, detail="", data=None):
    return {"node": node, "label": label, "status": status, "tier": tier,
            "detail": detail, "data": data or {}}


def _blueprint_guidance() -> str:
    """Turn approved Domain Blueprints into a system-prompt addendum so the engine follows the
    AUTHORED brain: the signals to gather, how they resolve to one key (so it never re-asks), the
    decision branches, and the ask_if_missing prompts (in the captain's language). Purely ADDITIVE
    and DEFENSIVE — any failure or an empty store returns "" and the loop behaves exactly as today."""
    try:
        from ..knowledge import blueprints
        approved = blueprints.approved()
        if not approved:
            return ""
        blocks = []
        for bp in approved:
            dom = bp.get("label") or bp.get("domain", "")
            lines = [f"\n■ DOMAIN BRAIN — {dom} (authored; follow it for this domain):"]
            sig = ", ".join(f"{s.get('key')} ({s.get('source')})"
                            for s in (bp.get("signals") or []) if s.get("key"))
            if sig:
                lines.append(f"  Signals to gather: {sig}.")
            for d in (bp.get("derivations") or []):
                frm = " + ".join(d.get("from") or [])
                if frm and d.get("to"):
                    lines.append(f"  Derive {d['to']} from {frm} — {d.get('how', '')} "
                                 f"(so never re-ask if you can derive it).")
            for br in (bp.get("decision") or []):
                if br.get("condition"):
                    lines.append(f"  If {br['condition']} → {br.get('action', '')}"
                                 + (f" ({br['note']})" if br.get("note") else "") + ".")
            for a in (bp.get("ask_if_missing") or []):
                if a.get("prompt"):
                    lines.append(f"  If {a.get('need')} is missing and can't be derived, ask exactly: "
                                 f"\"{a['prompt']}\"")
            if bp.get("escalation_team"):
                lines.append(f"  Escalation owner: {bp['escalation_team']}.")
            blocks.append("\n".join(lines))
        if not blocks:
            return ""
        return ("\n\nAUTHORED DOMAIN BRAINS — when a concern matches one of these domains, follow its "
                "brain: gather its signals, resolve them to the one canonical key (do NOT re-ask for "
                "something you can derive), apply its decision branches, and when a needed key is "
                "missing ask ONLY the true gap using its prompt (in the captain's language). This is "
                "additive to the SOP/policy path above; the money guarantees still run through the tools."
                + "\n".join(blocks))
    except Exception:  # noqa: BLE001 — never break the live loop
        return ""


def handle_turn(conversation_id: str, captain_id: str, message: str,
                channel: str = "chat", attachments: list | None = None) -> Iterator[dict]:
    """Stream the resolution trace for one turn. Every yielded event is also
    ACCUMULATED and, once the turn's concern_id is known, persisted to the Trace
    Log (data/traces.json) so the Concern Log can replay HOW the engine resolved
    it and the Auditing Studio can score it. Persistence is purely a sidecar:
    wrapped in try/except in a finally block so a trace-save failure can NEVER
    break the SSE stream or the turn (see _persist_trace)."""
    trace: list[dict] = []           # accumulate every yielded event this turn
    holder: dict = {}                # carries the terminal concern id/ids to `finally`
    try:
        yield from _run_turn(conversation_id, captain_id, message, channel, attachments, trace, holder)
    finally:
        _persist_trace(conversation_id, captain_id, trace, holder)


def _persist_trace(conversation_id: str, captain_id: str, trace: list[dict], holder: dict) -> None:
    """Persist the accumulated trace under the turn's concern_id. Defensive: any
    failure (or a turn with no concern_id) is swallowed silently — the live turn
    already completed streaming by the time this runs."""
    try:
        # every concern created this turn (multi-intent turns create more than one), else
        # the reply event's concern_id. The same worked trace attaches to each.
        ids = list(holder.get("concern_ids") or [])
        if holder.get("concern_id") and holder["concern_id"] not in ids:
            ids.append(holder["concern_id"])
        if not ids:
            for ev in reversed(trace):
                if ev.get("node") == "reply":
                    cid = (ev.get("data") or {}).get("concern_id")
                    if cid:
                        ids.append(cid)
                    break
        if not ids:
            return   # informational turns with no concern — skip silently
        from ..ledger import trace_log
        for cid in dict.fromkeys(ids):   # dedupe, preserve order
            trace_log.save(cid, captain_id, conversation_id, trace)
    except Exception:  # noqa: BLE001 — a trace-save failure must NEVER break the turn
        pass


def _run_turn(conversation_id: str, captain_id: str, message: str, channel: str,
              attachments: list | None, trace: list[dict], holder: dict) -> Iterator[dict]:
    """The agentic loop. Wrapped by handle_turn so every event is accumulated for
    the Trace Log. `_y` yields AND records; `holder` carries the terminal concern
    id out to the persist step."""
    def _y(event: dict) -> dict:
        trace.append(event)
        return event

    sess = STORE.get_or_create(conversation_id, captain_id)
    sess.turns += 1
    context = ctx.get_context(captain_id)
    if not context:
        yield _y(_evt("error", "Unknown captain", detail=f"No context for {captain_id}"))
        return

    provider, model = llm_registry.for_node("classify")   # tool-use tier
    if not hasattr(provider, "chat"):
        yield _y(_evt("error", "Provider lacks tool-calling", detail="Use Gemini/Claude provider"))
        return

    # Approved Domain Blueprints steer the loop additively (never break it — see _blueprint_guidance).
    system_prompt = _SYSTEM + _blueprint_guidance()

    attachments = attachments or []
    att_note = ""
    if attachments:
        names = ", ".join(f"{a.get('filename')} ({a.get('mime', 'file')})" for a in attachments)
        att_note = f"\n\n[The captain attached {len(attachments)} file(s): {names}. Acknowledge them and note they've been included with the case.]"
        yield _y(_evt("capture", "Attachments received", tier="fast",
                   detail=f"{len(attachments)} file(s): " + ", ".join(a.get("filename", "file") for a in attachments)))

    yield _y(_evt("capture", "Capture", detail=f"Turn {sess.turns} · reading the message"))

    sess.contents.append({"role": "user", "parts": [{"text": message + att_note}]})
    terminal_action, terminal_concern = "respond", None

    for step in range(MAX_STEPS):
        try:
            content, _ = provider.chat(sess.contents, model=model, system=system_prompt,
                                       tools=tools.DECLARATIONS)
        except Exception as e:  # noqa: BLE001 — graceful degradation (BRD §11)
            yield _y(_evt("explain", "Model provider unavailable", status="blocked", tier="fast",
                       detail=f"{type(e).__name__}: {str(e)[:280]}"))
            yield _y({"node": "reply", "label": "Reply", "status": "done",
                   "detail": "", "data": {"reply": "I'm having trouble reaching my reasoning service from "
                             "this environment right now — the knowledge base, resolution engine, and trust "
                             "spine are all live. Please try again in a moment. (This deploy reaches the LLM "
                             "gateway through a network path that's currently blocked upstream.)",
                             "decision_action": "respond", "concern_id": None}})
            return
        parts = content.get("parts", [])
        calls = [p["functionCall"] for p in parts if "functionCall" in p]

        if not calls:
            reply = "".join(p.get("text", "") for p in parts).strip()
            sess.contents.append(content)
            yield _y(_evt("explain", "Answer warmly", tier="fast", detail="Composed reply",
                       data={"reply": reply}))
            concern = terminal_concern or _log_info_concern(conversation_id, captain_id, message,
                                                            reply, channel)
            holder["concern_id"] = concern["id"]
            if concern.get("id"):
                holder.setdefault("concern_ids", []).append(concern["id"])
            yield _y({"node": "reply", "label": "Reply", "status": "done", "detail": reply,
                   "data": {"reply": reply, "decision_action": terminal_action,
                            "concern_id": concern["id"]}})
            return

        # execute tool calls, feed results back. EVERY call gets a functionResponse — even on
        # error — so the history never has a dangling tool_call (which would poison every later turn).
        sess.contents.append(content)
        resp_parts = []
        for fc in calls:
            name, cargs = fc.get("name", ""), fc.get("args", {}) or {}
            try:
                result, events, concern, action = tools.dispatch(name, cargs, captain_id, context, channel,
                                                                 attachments=attachments)
            except Exception as e:  # noqa: BLE001 — a tool bug must not poison the conversation
                result, events, concern, action = {"error": f"{type(e).__name__}: {str(e)[:150]}"}, [], None, None
                yield _y(_evt("explain", f"Tool {name} failed", status="blocked", tier="fast", detail=str(e)[:200]))
            for e in events:
                yield _y(e)
            if concern is not None:
                terminal_concern, terminal_action = concern, action
                holder["concern_id"] = concern.get("id")
                if concern.get("id"):   # a turn can create >1 concern (multi-intent) — keep them all
                    holder.setdefault("concern_ids", []).append(concern["id"])
            resp_parts.append({"functionResponse": {"name": name, "response": result}})
        sess.contents.append({"role": "user", "parts": resp_parts})

    # safety: exceeded step budget
    yield _y(_evt("explain", "Answer warmly", tier="fast", detail="Composed reply",
               data={"reply": "Main ispe thoda aur check kar raha hoon — ek moment dijiye."}))
    holder["concern_id"] = (terminal_concern or {}).get("id")
    yield _y({"node": "reply", "label": "Reply", "status": "done",
           "detail": "", "data": {"reply": "Main ispe thoda aur check kar raha hoon.",
                                  "decision_action": terminal_action,
                                  "concern_id": (terminal_concern or {}).get("id")}})


def _log_info_concern(conversation_id, captain_id, message, reply, channel) -> dict:
    """Log a non-money (informational) turn to the Concern Log for audit."""
    from ..ledger import concern_log
    import uuid
    concern = {"id": "CNC-" + uuid.uuid4().hex[:8].upper(), "captain_id": captain_id,
               "channel": channel, "conversation_id": conversation_id,
               "intent": message[:80], "disposition": "conversation", "action_taken": "respond",
               "outcome": "resolved_in_conversation", "reply": reply, "evidence_trail": []}
    return concern_log.append(concern)
