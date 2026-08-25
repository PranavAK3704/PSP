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

from ..llm import meter as llm_meter
from ..llm import registry as llm_registry
from ..substrate import captain_context as ctx
from . import dataplane, tools
from .algo import followups
from .algo import router as prerouter
from .algo.entities import extract as extract_entities
from .session import STORE

# Bounded agentic loop. 4, down from 6: each extra step resends the ENTIRE history plus the
# ~5,300-token stable prefix (system prompt + 5 tool schemas), so step 6 is the most expensive
# step in the turn and the least likely to be productive. A turn that has not converged in 4
# steps is a turn the deterministic router or an escalation should be handling.
MAX_STEPS = 4

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
  • get_captain_context — a SUMMARY of the captain's own account: how many debits, how many
    still open, totals, the loss-type mix, their hub. Use it to learn WHETHER they have debits
    and of what kind. It gives you counts, NOT individual records — there are no AWBs, dates
    or debit ids in it, so never try to list or cite a specific debit from it, and never
    volunteer debits they did not raise. To work one specific debit, ask for its AWB and call
    apply_policy.
  • LOAD / ORDER VOLUME ("mera load kam hai", "why am I getting fewer orders", "capacity cut"):
    call apply_policy with disposition "load_planning" and no identifier. It reads the captain's
    hub growth data, runs the Orders & Planning checks, and returns a decided answer with an
    evidence trail — no money is involved, so nothing is ever paid or reversed on this path.
    Relay its `reason` as written. Use run_data_query "load_status" only if you want the raw
    factual read without a decision.
  • run_data_query — for live/past data ("where is my shipment", "last payout"). It returns a
    composed `answer` computed from the real records: relay it faithfully in the captain's
    language, add nothing to it, and when it says a data source is not connected, believe it —
    say so and escalate rather than quoting a figure you do not have.
  • apply_policy — the ONLY sanctioned way to reach a decision on a money case (a wrongly
    applied loss/debit, a COD pendency). You may NOT state, promise, or imply a reversal/credit
    yourself. AND NEITHER DOES IT: there is no write endpoint, so even a favourable outcome is a
    recorded RECOMMENDATION the owning team actions — never say the money "has been" returned,
    say it has been confirmed and routed for credit. Relay the tool's `reason` as written. You need just ONE identifier for the case — an AWB, OR the amount, OR a txn id
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
- NEVER state that money has been paid, credited, reversed or refunded. This engine has no
  write path to any payment system. It decides, records and hands over; the team pays.
- Keep replies short, warm, and clear (2–5 sentences).
- If you genuinely lack information and no tool can resolve it, call escalate_case — never
  dead-end the captain or tell them to raise it themselves.
"""


#: Distinguishes "no decision was made this turn" from "a decision was made and it escalated".
#: `None` cannot carry both meanings: the first must leave an existing scope alone (a plain
#: informational turn should not wipe the scope from the turn before it), the second must clear
#: it. A sentinel is the only way to keep those apart.
_UNSET = object()


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
                channel: str = "chat", attachments: list | None = None,
                selected_option: str | None = None, source: str = "") -> Iterator[dict]:
    """Stream the resolution trace for one turn. Every yielded event is also
    ACCUMULATED and, once the turn's concern_id is known, persisted to the Trace
    Log (data/traces.json) so the Concern Log can replay HOW the engine resolved
    it and the Auditing Studio can score it. Persistence is purely a sidecar:
    wrapped in try/except in a finally block so a trace-save failure can NEVER
    break the SSE stream or the turn (see _persist_trace)."""
    trace: list[dict] = []           # accumulate every yielded event this turn
    holder: dict = {}                # carries the terminal concern id/ids to `finally`
    try:
        yield from _run_turn(conversation_id, captain_id, message, channel, attachments, trace,
                             holder, selected_option, source)
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
              attachments: list | None, trace: list[dict], holder: dict,
              selected_option: str | None = None, source: str = "") -> Iterator[dict]:
    """The agentic loop. Wrapped by handle_turn so every event is accumulated for
    the Trace Log. `_y` yields AND records; `holder` carries the terminal concern
    id out to the persist step.

    `source` rides along to every `concern_log.append` this turn makes. It is a plain argument
    and not a ContextVar for a measured reason — see `concern_log.writing_as`."""
    def _y(event: dict) -> dict:
        trace.append(event)
        return event

    sess = STORE.get_or_create(conversation_id, captain_id)
    sess.turns += 1
    # Bound the history BEFORE this turn's content is added, so the trim never has to reason
    # about a half-built turn. Cuts only at a safe boundary — see session.Session.trim.
    dropped = sess.trim()
    context = ctx.get_context(captain_id)
    if not context:
        yield _y(_evt("error", "Unknown captain", detail=f"No context for {captain_id}"))
        return

    provider, model = llm_registry.for_node("classify")   # tool-use tier
    # `hasattr(provider, "chat")` no longer answers this: LLMProvider now declares a `chat`
    # stub so every provider inherits `chat_metered`, which made the old check always pass
    # and a genuinely tool-less provider fail as a "transport" error ("try again in a
    # moment") instead of as the config problem it is. Compare against the base method.
    from ..llm.base import LLMProvider
    if getattr(type(provider), "chat", None) is LLMProvider.chat:
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

    yield _y(_evt("capture", "Capture",
                  detail=f"Turn {sess.turns} · reading the message"
                         + (f" · trimmed {dropped} old history entr"
                            f"{'y' if dropped == 1 else 'ies'}" if dropped else "")))

    # ── deterministic identifier extraction, before the model sees the turn ──────────
    # Identifiers are strictly shaped (AWB = VL+13 or VLR+12 digits, UTR, hub code, ₹ amount),
    # so a lexer reads them exactly and a model can only approximate. Extracting first means
    # the model is handed facts instead of being asked to find them — it cannot mis-transcribe
    # a 15-digit AWB, and the values that end up in the evidence trail are the ones the partner
    # actually typed. This step makes no LLM call, which is why it carries no tier.
    ents = extract_entities(message)
    if ents.get("any"):
        found = {k: v for k, v in ents.items()
                 if v and k not in ("any", "redacted_present")}
        yield _y(_evt("extract", "Identifiers extracted", detail=", ".join(
            f"{k.replace('_', ' ')}: {', '.join(str(x) for x in v)}" for k, v in found.items()),
            data={"entities": found, "method": "deterministic"}))
        att_note += ("\n\n[Identifiers read from the message (exact, already parsed — use these "
                     "rather than re-reading them): "
                     + "; ".join(f"{k}={v}" for k, v in found.items()) + "]")

    # The allow-list for the data-plane subset test: every identifier this captain has typed,
    # this turn and every earlier one. Computed from the RAW message, before att_note is
    # appended, so the engine's own annotations can never widen it.
    sess.supplied |= dataplane.supplied(message)
    allowed_tokens = set(sess.supplied)

    # Per-turn spend accounting, held EXPLICITLY. Not a contextvar and not a thread-local:
    # sse_starlette drives this generator through iterate_in_threadpool, so successive
    # __next__ calls can land on different threads and ambient state would reset mid-turn.
    tm = llm_meter.TurnMeter()

    # ── THE DETERMINISTIC PRE-ROUTER ────────────────────────────────────────────────
    # One call site, placed AFTER entity extraction and context assembly (so a tier can read
    # both) and BEFORE sess.contents grows (so a router-answered turn appends its own pair
    # rather than leaving a hole). Defaults to shadow: the verdict is computed and traced, the
    # LLM still answers. See algo/router.py for why that default matters.
    # ── numbered-menu fallback, for every channel ────────────────────────────────────
    # WhatsApp's send() is text-only and carries 81.6% of tickets, so the option list is shown
    # there as "1. … 2. …" and a captain answers "2". Resolved HERE rather than in the WhatsApp
    # adapter so a panel user who types the number instead of tapping gets the same behaviour —
    # and so there is one place where a lone digit is interpreted, not two.
    #
    # A MENU IS ONLY LIVE FOR ONE TURN. `last_options` is captured and then CLEARED here, and
    # re-set below only on a turn the router actually answers with chips. Without the clear it
    # was write-only: a menu offered on turn 1 stayed live after the LLM answered turn 2, so a
    # "2" on turn 3 — meaning "2 days", answering the LLM's own question — resolved against a
    # menu two turns stale. That failure lands hardest on exactly the population this feature is
    # for: someone replying to a question with a bare number.
    offered_last_turn = list(getattr(sess, "last_options", []) or [])
    sess.last_options = []
    # ONLY WHERE THE NUMBERS WERE ACTUALLY SHOWN.
    #
    # `as_numbered_text` renders the menu as "1. … 2. …" on the buttonless transports. The panel
    # does not: CaptainPanel.jsx renders labelled buttons and no digits appear anywhere. So a "2"
    # typed in the panel is DATA — "2 din se pending hai", an amount, a count — and treating it
    # as an explicit chip tap answered a question the captain never asked. Resolving an ordinal
    # against a menu they were never shown is not a fallback, it is a guess.
    numbered_channel = (channel or "chat") != "chat"
    if not selected_option and numbered_channel:
        selected_option = followups.ordinal_choice(message, offered_last_turn)
        if selected_option:
            yield _y(_evt("firstpass", "Numbered option chosen", tier="fast",
                          detail=f"{message.strip()!r} → {selected_option}",
                          data={"selected_option": selected_option,
                                "offered": offered_last_turn}))

    pr_verdict, pr_trace = prerouter.route(prerouter.Ctx(
        message=message, entities=ents, context=context, session=sess,
        attachments=attachments, channel=channel,
        prev_action=getattr(sess, "last_action", None),
        selected_option=selected_option))
    # In SHADOW the event is emitted on EVERY turn, including a plain decline. Absorption is a
    # rate, and a trace that records only the hits gives you a numerator with no denominator —
    # which is precisely the number shadow mode exists to produce. In `on` mode the event is
    # emitted only when something happened, so an ordinary LLM turn stays uncluttered.
    if pr_trace.get("fired") or pr_trace.get("refusals") or prerouter.mode() == prerouter.SHADOW:
        # `firstpass` already has an icon in Pipeline.jsx. Nodes are collapsed by id there
        # (latest wins), so reusing an existing name avoids inventing one that renders blank.
        _fired = pr_trace.get("fired") or {}
        yield _y(_evt("firstpass", "Deterministic first pass", tier="fast",
                      status="done" if _fired else "blocked",
                      detail=(f"{_fired.get('tier')} — {_fired.get('because', '')}"
                              if _fired else
                              "declined: " + ", ".join(pr_trace.get("refusals")
                                                       or [f"no tier matched "
                                                           f"({len(pr_trace.get('tiers_tried') or [])} tried)"])),
                      data=pr_trace))
    if pr_verdict is not None:
        # A router-answered turn must append BOTH halves to the history. Appending only the
        # captain's message leaves a user turn with no model reply, and every later LLM turn
        # then reads a conversation where the assistant ignored someone.
        sess.contents.append({"role": "user", "parts": [{"text": message + att_note}]})
        sess.contents.append({"role": "model", "parts": [{"text": pr_verdict.reply}]})
        sess.last_action = pr_verdict.action
        # A follow-up that has been READ should not be re-offered as a chip on the next turn.
        # Recorded here rather than inside the tier because a tier is pure by contract — it may
        # not mutate the session it was handed.
        _node = (pr_verdict.data or {}).get("node")
        if _node:
            sess.offered = set(sess.offered) | {_node}
        # IN ORDER — the numbering the captain sees is positional, so this list is the contract
        # that makes their "2" mean the second thing they were shown.
        sess.last_options = [o["id"] for o in (pr_verdict.options or []) if o.get("id")]
        concern = _log_info_concern(conversation_id, captain_id, message,
                                    pr_verdict.reply, channel,
                                    disposition=f"router:{pr_verdict.tier}",
                                    action=pr_verdict.action)
        holder["concern_id"] = concern["id"]
        if concern.get("id"):
            holder.setdefault("concern_ids", []).append(concern["id"])
        # Cost is reported even though it is zero — that IS the point of this path, and a turn
        # with no cost event looks like a turn whose cost was not measured.
        cost = tm.summary()
        yield _y(_evt("cost", "Turn cost", tier="fast",
                      detail=f"${cost['cost_usd']:.4f} · 0 model calls · answered deterministically",
                      data={**cost, "deterministic": True}))
        yield _y({"node": "reply", "label": "Reply", "status": "done", "detail": pr_verdict.reply,
                  "data": {"reply": pr_verdict.reply, "decision_action": pr_verdict.action,
                           "concern_id": concern["id"], "cost": cost,
                           "deterministic": True, "tier": pr_verdict.tier,
                           **({"options": pr_verdict.options} if pr_verdict.options else {})}})
        return

    sess.contents.append({"role": "user", "parts": [{"text": message + att_note}]})
    terminal_action, terminal_concern = "respond", None
    #: The follow-up scope this turn WOULD arm, held until a reply actually reaches the captain.
    #: Three states: `_UNSET` (no decision was made — leave the session alone), `None` (a
    #: decision was made and it escalated — clear any scope), or (disposition, facts).
    pending_scope: object = _UNSET

    for step in range(MAX_STEPS):
        try:
            # chat_metered, not chat: it applies the dollar ceiling and BILLS THE USAGE.
            # This line used to be `content, _ = provider.chat(...)` — the underscore threw
            # away the only token count the loop ever sees, which is why nothing in the
            # platform could say what a turn cost.
            content, _usage = provider.chat_metered(
                sess.contents, model=model, node="classify", system=system_prompt,
                tools=tools.DECLARATIONS, turn=tm)
        except Exception as e:  # noqa: BLE001 — graceful degradation (BRD §11)
            yield _y(_evt("explain", "Model provider unavailable", status="blocked", tier="fast",
                       detail=f"{type(e).__name__}: {str(e)[:280]}"))
            # The reply must name the REAL cause, because the three causes need three different
            # actions. The old text blamed "an LLM gateway reached through a network path blocked
            # upstream" — that gateway was decommissioned, so the sentence was simply false and it
            # sent anyone debugging this looking for a firewall.
            #
            # The distinction that matters most: an exhausted-credits 400 is NOT retryable, and
            # telling someone to "try again in a moment" when the account has no balance is a lie
            # that wastes their time. It was the actual cause of the first Claude-provider failure
            # on the deployed build, and it arrives as a 400 (not a 401), so it does not look like
            # an auth error either. Each branch names what it is and who can fix it.
            emsg = str(e).lower()
            status = getattr(getattr(e, "response", None), "status_code", None)
            if isinstance(e, llm_meter.BudgetExhausted):
                # FIRST in the chain and matched by TYPE, not by string. It must not fall
                # through to the "credit balance" branch below: that one tells the reader the
                # Anthropic account is empty, when in fact OUR OWN ceiling stopped the call
                # and the account is fine. Two different people fix those two things.
                kind = "budget"
                # There are TWO ceilings and raising the wrong one changes nothing, so the
                # reply names the one that actually fired (meter.check tags the exception with
                # its scope). Saying "raise LLM_BUDGET_USD" after a PER-TURN stop sent the
                # operator to an env var that had no effect on the thing they just hit.
                which = getattr(e, "env_var", "LLM_BUDGET_USD")
                scope = ("this single turn" if getattr(e, "scope", "") == "per_turn"
                         else "this deployment")
                reply = ("I've stopped short of this one on purpose: the spend ceiling for "
                         f"{scope} has been reached, so I won't make another model call until "
                         "it's raised. Nothing is broken and no credit is lost — the ceiling "
                         "exists so a runaway loop can't drain the account. Whoever runs the "
                         f"deploy can raise {which}.")
            elif "credit balance" in emsg or "quota" in emsg or "billing" in emsg:
                kind = "billing"
                reply = ("I can't reason about this right now: the AI account behind me has run out "
                         "of credits. Nothing else is broken — the knowledge base, resolution engine "
                         "and trust spine are all live — and retrying won't help until the account is "
                         "topped up. Please tell the team to add credits.")
            elif not llm_registry.key_configured() or status == 401 or "authentication" in emsg:
                kind = "auth"
                reply = ("I can't reach my reasoning model because this deployment has no valid API "
                         "key for it. Everything else is live — knowledge base, resolution engine and "
                         "trust spine — so this is one config value away from working. "
                         "Whoever runs the deploy needs to set the API key for the active provider.")
            elif status == 429 or "rate limit" in emsg:
                kind = "rate_limit"
                reply = ("I'm being rate-limited by the AI provider right now, so I've had to stop "
                         "mid-thought. This one genuinely does clear on its own — please try again "
                         "in a few moments.")
            else:
                kind = "transport"
                reply = ("I'm having trouble reaching my reasoning model right now. The knowledge "
                         "base, resolution engine and trust spine are all live, so this should be "
                         "temporary — please try again in a moment.")
            # engine_error marks this as a FAILURE, not an answer, so the UI can style it as one.
            # Without the flag a "sorry, I can't reach my model" bubble is visually identical to a
            # resolved reply, which is the one thing it must never be mistaken for.
            yield _y({"node": "reply", "label": "Reply", "status": "done",
                   "detail": "", "data": {"reply": reply, "engine_error": True,
                             "error_kind": kind,
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
                                                            reply, channel, source=source)
            holder["concern_id"] = concern["id"]
            if concern.get("id"):
                holder.setdefault("concern_ids", []).append(concern["id"])
            # What the turn cost, measured rather than estimated. It goes into the TRACE,
            # which _persist_trace saves under this turn's concern id — so the Concern Log can
            # show cost-per-resolution by replaying the trace. It is not a field on the
            # concern record itself.
            cost = tm.summary()
            yield _y(_evt("cost", "Turn cost", tier="fast",
                          detail=f"${cost['cost_usd']:.4f} · {cost['calls']} model call(s) · "
                                 f"{cost['tokens_in']:,} in / {cost['tokens_out']:,} out",
                          data=cost))
            # Remembered so the router's "previous turn escalated" refusal can see it next turn.
            sess.last_action = terminal_action
            # THE COMMIT POINT. A follow-up scope is a promise that the previous answer explained
            # something, so it becomes live exactly here — where that answer is handed over —
            # and nowhere else. Every other exit from this loop (a provider failure, the step
            # budget) leaves the session's scope untouched or cleared.
            if pending_scope is not _UNSET:
                try:
                    if pending_scope is None:
                        sess.clear_disposition()
                    else:
                        sess.set_disposition(pending_scope[0], pending_scope[1])
                except Exception:  # noqa: BLE001 — scoping never breaks a turn
                    pass
            yield _y({"node": "reply", "label": "Reply", "status": "done", "detail": reply,
                   "data": {"reply": reply, "decision_action": terminal_action,
                            "concern_id": concern["id"], "cost": cost}})
            return

        # execute tool calls, feed results back. EVERY call gets a functionResponse — even on
        # error — so the history never has a dangling tool_call (which would poison every later turn).
        sess.contents.append(content)
        resp_parts = []
        for fc in calls:
            name, cargs = fc.get("name", ""), fc.get("args", {}) or {}
            try:
                result, events, concern, action = tools.dispatch(
                    name, cargs, captain_id, context, channel, attachments=attachments,
                    turn=tm, source=source, message=message)
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
            # ── the data-plane boundary, checked where it is actually crossed ────────
            # This line is the ONLY place a tool result becomes part of what gets sent to
            # the model, so it is the only place worth checking. The rule is a subset test,
            # not a blanket ban: an identifier the captain themselves typed is already in
            # the model's context and echoing it is not new exposure — anything else is.
            # See engine/dataplane.py for the full reasoning.
            #
            # Defensive by construction: the projections in tools.py mean this should never
            # fire, so a hit is a genuine regression (a new tool, a widened query, a changed
            # provider) and it surfaces as a trace event rather than silently crossing the
            # wire. Wrapped so the guard itself can never break a turn.
            try:
                leaks = dataplane.violations(result, allowed_tokens)
                if leaks:
                    result = dataplane.redact(result, allowed_tokens)
                    yield _y(_evt("guard", "Data-plane guard", status="blocked", tier="fast",
                                  detail=f"redacted {len(leaks)} unsupplied identifier(s) from "
                                         f"{name} before sending",
                                  data={"tool": name, "leaks": leaks}))
            except Exception:  # noqa: BLE001 — a guard must never be the thing that fails
                pass
            # ── the follow-up scope, taken from the ENGINE'S decision ────────────────
            # `policy_exec.execute` returns the disposition it actually acted on — for a loss
            # that is `reason_l1_to_disposition` over the real row, not the model's guess. That
            # provenance is the whole safety argument for answering the next turn from a lookup
            # table: the scope was established by code reading data, so a follow-up matched
            # within it cannot wander into a disposition nobody decided.
            #
            # Read from the tool RESULT rather than from the model's arguments on purpose. The
            # model proposes a disposition when it calls apply_policy; the engine may override
            # it from the row and frequently does. Trusting the argument would scope follow-ups
            # to a disposition that was never acted on.
            # ONLY WHEN THE DECISION ACTUALLY RESOLVED SOMETHING.
            # `policy_exec._out` returns disposition "load_planning" on all four of its ESCALATE
            # branches too — including the one that fires because the growth dashboard could not
            # be read at all. Stamping the scope from those armed a follow-up graph that answers
            # "your load is low because RTO and OCF" to a captain who had just been told the
            # engine could not read their data and was routing them to a human. The follow-up
            # scope is a promise that something was explained; an escalation explained nothing.
            #
            # STASHED, NOT ARMED. The scope is committed only where the captain is actually
            # handed a reply (see `pending_scope` below). Arming it here meant a turn whose tool
            # succeeded but whose NEXT model call died left a live follow-up scope — plus the
            # facts — for an answer that was never delivered: the captain saw only "I can't reach
            # my reasoning model", and their next question was then answered from a graph
            # premised on a diagnosis they never received.
            if isinstance(result, dict) and result.get("disposition"):
                pending_scope = (None if result.get("action") in (None, "", "escalate")
                                 else (result["disposition"], result.get("followup_facts")))
            resp_parts.append({"functionResponse": {"name": name, "response": result}})
        sess.contents.append({"role": "user", "parts": resp_parts})

    # safety: exceeded step budget
    yield _y(_evt("explain", "Answer warmly", tier="fast", detail="Composed reply",
               data={"reply": "Main ispe thoda aur check kar raha hoon — ek moment dijiye."}))
    holder["concern_id"] = (terminal_concern or {}).get("id")
    cost = tm.summary()
    yield _y(_evt("cost", "Turn cost", tier="fast",
                  detail=f"${cost['cost_usd']:.4f} · {cost['calls']} model call(s) · step budget hit",
                  data=cost))
    yield _y({"node": "reply", "label": "Reply", "status": "done",
           "detail": "", "data": {"reply": "Main ispe thoda aur check kar raha hoon.",
                                  "decision_action": terminal_action,
                                  "concern_id": (terminal_concern or {}).get("id"),
                                  "cost": cost}})


def _log_info_concern(conversation_id, captain_id, message, reply, channel,
                      disposition: str = "conversation", action: str = "respond",
                      source: str = "") -> dict:
    """Log a non-money (informational) turn to the Concern Log for audit.

    `disposition` is parameterised so a deterministically-answered turn is identifiable in the
    ledger as `router:<tier>` rather than indistinguishable from an LLM conversation. Without
    that, absorption can only be counted from traces, which are trimmed.
    """
    from ..ledger import concern_log
    import uuid
    concern = {"id": "CNC-" + uuid.uuid4().hex[:8].upper(), "captain_id": captain_id,
               "channel": channel, "conversation_id": conversation_id, "source": source,
               "intent": message[:80], "disposition": disposition, "action_taken": action,
               "outcome": "resolved_in_conversation", "reply": reply, "evidence_trail": []}
    return concern_log.append(concern)
