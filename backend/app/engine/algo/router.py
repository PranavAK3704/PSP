"""The deterministic pre-router — answer without a model where an algorithm is as good.

WHY
Every turn today costs a model call. A captain typing "ok thanks" costs the same ~₹4 as a loss
dispute, because the loop has no way to answer anything itself. That is the wrong shape twice
over: it is expensive at 46,000 tickets a month, and a model is a worse instrument than a lookup
table for a fixed phrase. The point is not to make the model cheaper — it is to stop asking it
questions that have exact answers.

WHAT IT IS NOT
It is not a classifier and it does not compete with the model. Each tier is a **conjunction of
booleans**: every precondition must hold, or the tier declines and the turn proceeds to the LLM
exactly as it does today. There is no scalar score across tiers and no new global threshold —
`trust/gate.CONFIDENCE_THRESHOLD` is untouched, because nothing here decides money.

THE THREE MODES, and why `shadow` is the default
    off      the router is not consulted. Byte-identical to pre-router behaviour.
    shadow   compute the verdict, EMIT it as a trace event, then return None anyway — so the
             LLM still answers while live traffic reports what the router would have done.
    on       the verdict is used.

Shadow exists because the corpus cannot answer the question that matters. 81.6% of tickets are
WhatsApp and the repo holds **no WhatsApp message text** — so the absorption rate of any tier is
unmeasurable offline. Claiming a number before shadow mode has reported one would be inventing
it. Shadow also lets the router's would-be reply be diffed against the LLM's actual reply on the
same turn, which is the only honest way to find a tier that is confidently wrong.

BULLETPROOFING — the rules every tier obeys
  · Pure. No I/O inside a matcher; anything read from a DB is read before and passed in.
  · Fail to None, never to a guess. `route()` wraps everything in try/except and returns None on
    any exception, degrading to today's behaviour rather than to a wrong answer.
  · Frozen inputs. Whitelists and thresholds are module-level constants with their calibration
    recorded beside them. Nothing is tuned at runtime.
  · Every pattern carries its false-positive trap as a comment, matching the style in
    algo/entities.py — where three documented traps (a bare 3-letter hub matching "OLD" and
    "SIR" for 16.7% noise, `re.I` leaking into a capture group and matching "CODE" as a hub,
    and a phone regex eating an AWB's digit tail) are the reason those bugs got caught.
"""
from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field

OFF, SHADOW, ON = "off", "shadow", "on"
_MODES = (OFF, SHADOW, ON)


def mode(tier: str | None = None) -> str:
    """`PSP_PREROUTER` = off | shadow | on. Unknown values fall back to `shadow`.

    Shadow, not `on`: an unrecognised value must not be the thing that starts answering
    captains deterministically. Same reasoning as WRITE_MODE defaulting to simulated.

    WHY A TIER CAN OVERRIDE THE GLOBAL MODE — `PSP_PREROUTER_<TIER>`
    The tiers do not all earn trust at the same rate, and a single switch forces the most
    cautious one to hold the least cautious one back — or the reverse, which is worse.

    Greetings are a closed whitelist over a fixed phrase list: there is nothing to learn about
    them from live traffic, so holding them in shadow buys nothing and costs ~Rs 4.54 per
    "ok thanks". Follow-ups match real questions inside a scope, and the one thing the corpus
    cannot tell us — WHICH follow-ups captains actually ask — is exactly what shadow mode
    collects. So the right configuration is genuinely mixed, and this makes that expressible:

        PSP_PREROUTER=shadow  PSP_PREROUTER_GREETING=on

    Follows the `LLM_PROVIDER_<NODE>` idiom already in llm/registry.py — same shape, same
    env-wins precedence — rather than inventing a second convention for the same job.
    """
    raw = (os.environ.get("PSP_PREROUTER") or "").strip().lower()
    glob = raw if raw in _MODES else SHADOW
    if not tier:
        return glob
    per = (os.environ.get(f"PSP_PREROUTER_{tier.upper()}") or "").strip().lower()
    return per if per in _MODES else glob


@dataclass
class Verdict:
    """What a tier decided. `reply` is what the captain would see."""

    tier: str                       # "greeting" | "faq_confirm" | "awb_dispute" | "slot_fill"
    reply: str
    #: Why this fired, in one line, for the trace. Not shown to the captain.
    because: str = ""
    #: Terminal action for the concern record. Never a money action — the router does not decide
    #: money; anything money-shaped is delegated to `tools.dispatch("apply_policy")`.
    action: str = "respond"
    #: Confirmation chips, when the tier is asking rather than answering (Tier B).
    options: list = field(default_factory=list)
    #: Set when the tier wants the LLM to take over after recording something.
    handoff: bool = False
    #: Free-form, for the trace only.
    data: dict = field(default_factory=dict)


# ── hard refusals — any one of these forces fall-through, before any tier is tried ───────────
# These are the conditions under which a deterministic answer is not safe REGARDLESS of how well
# a tier matches. Checked once, centrally, so a new tier cannot forget one.

def _refusals(message: str, ents: dict, attachments: list | None,
              prev_action: str | None) -> list[str]:
    why: list[str] = []
    if attachments:
        # An attachment is evidence the captain went out of their way to provide. Answering from
        # a phrase match while ignoring a photo they uploaded is the rudest possible failure.
        why.append("attachments present")
    if prev_action == "escalate":
        # The previous turn handed to a human. Continuing to answer deterministically talks over
        # a case that is already with someone.
        why.append("previous turn escalated")
    if ents.get("redacted_present") and not ents.get("any"):
        # The PII scrubber removed an identifier the captain DID supply. We know something was
        # there and we cannot see it, which is precisely when not to guess.
        why.append("redaction placeholder with no extracted entity")
    if _multi_intent(message, ents):
        # Multi-intent decomposition is explicitly the model's job (conversation._SYSTEM spends
        # six lines on it). A router that answers the first of three concerns and drops two is
        # worse than one that declines.
        why.append("multi-intent")
    return why


# Conjunctions that signal a second concern. Hinglish and English, because captains code-switch
# mid-sentence — "mera load kam hai AUR payment bhi nahi aaya" is one message, two queues.
#
# THE TRAP: these are common words. "aur" appears inside "aurangabad"; "and" inside "band" and
# "understand"; "bhi" inside "abhi", which is extremely common in Hinglish ("abhi tak nahi
# aaya"). Substring matching here would refuse almost every message. So the check is on
# TOKENS, and "abhi" is explicitly excluded rather than left to luck.
_CONJUNCTIONS = frozenset({"aur", "and", "bhi", "also", "plus", "tatha", "ke saath"})
_CONJUNCTION_LOOKALIKES = frozenset({"abhi", "aurangabad", "band", "understand", "kabhi",
                                     "sabhi", "bhitar"})


def _multi_intent(message: str, ents: dict) -> bool:
    """Two concerns in one message. Deliberately conservative — a false NEGATIVE here costs
    nothing (the tier still has to pass its own preconditions), while a false positive refuses
    a turn the router could have answered."""
    toks = _words(message)
    if not any(t in _CONJUNCTIONS for t in toks):
        return False
    # THE LOOKALIKE CHECK IS ORDER-DEPENDENT, and it was the wrong way round.
    #
    # It used to run FIRST and return False on any lookalike — so "load kam hai aur abhi payment
    # nahi aaya" had the multi-intent refusal DISABLED by "abhi", and the turn was answered as a
    # load question with the payment half silently dropped. "abhi" is one of the most common
    # words in Hinglish ("abhi tak nahi aaya"), so that was not a rare path.
    #
    # The lookalikes exist to stop a SUBSTRING match — "aur" inside "aurangabad", "bhi" inside
    # "abhi" — but `_words` already tokenises, so a lookalike token can only be itself. It
    # therefore cannot mask a real conjunction, and it only matters when there is no real
    # conjunction present. Checking it after the conjunction test keeps its intent (do not guess
    # on an ambiguous token) without letting it veto an unambiguous one.
    if not (toks & _CONJUNCTIONS) and any(t in _CONJUNCTION_LOOKALIKES for t in toks):
        return False
    # A conjunction alone is not multi-intent — "load kam hai aur badhana hai" is one concern.
    # It only counts alongside an identifier or a second domain signal, which is what makes it
    # look like two separate asks.
    return bool(ents.get("any")) or len(_domain_signals(message)) > 1


# Domain vocabulary, one set per queue. Used ONLY to detect >1 domain in a message; never to
# route. Routing on keyword sets is what measured P 0.790 in the corpus (296 of 320 SOP trigger
# keywords are single common words like "me", "has", "date"), which is why the FAQ tier uses
# BM25 with IDF instead.
_DOMAIN_WORDS = {
    "losses": frozenset({"loss", "debit", "kata", "nuksan", "awb", "reversal", "revoke",
                         "hardstop", "shortage"}),
    "payments": frozenset({"payment", "payout", "paisa", "invoice", "salary", "vetan",
                           "earning", "credit"}),
    "cod": frozenset({"cod", "cash", "pendency", "deposit", "cms"}),
    "orders": frozenset({"load", "order", "volume", "capacity", "allocation", "polygon"}),
    "fe_id": frozenset({"fe", "rider", "pilot", "id", "block", "band", "deactivate"}),
    "consumables": frozenset({"consumable", "bag", "tape", "sticker", "packaging"}),
}


def _domain_signals(message: str) -> set[str]:
    toks = _words(message)
    return {d for d, words in _DOMAIN_WORDS.items() if toks & words}


def _words(text: str) -> set[str]:
    """Lowercased alphanumeric tokens. Set, not list — every caller wants membership."""
    return set(normalise(text).split())


# Unicode categories to KEEP alongside alphanumerics. Mn/Mc are combining marks — Devanagari
# matras and the virama.
#
# THE TRAP, and it is easy to miss because it looks like it works: `str.isalnum()` is False for
# a combining mark, because Mn is not in the alphanumeric set. So an `isalnum()`-only filter
# turns "नमस्ते" into "नमस त" — the virama and the vowel sign become spaces and SPLIT THE WORD
# INTO PIECES. Every Devanagari term in every whitelist then silently fails to match, while the
# Latin ones all pass, so the tier looks correct in testing right up until a captain types Hindi.
_KEEP_CATEGORIES = frozenset({"Mn", "Mc"})


def normalise(text: str) -> str:
    """Lowercase, strip punctuation and emoji to spaces, collapse whitespace.

    Shared by every tier so "OK, thanks!!" and "ok thanks" normalise identically, and so
    Devanagari survives intact — see the note on _KEEP_CATEGORIES.
    """
    out = []
    for ch in (text or "").lower():
        if ch.isalnum() or ch.isspace() or unicodedata.category(ch) in _KEEP_CATEGORIES:
            out.append(ch)
        else:
            out.append(" ")
    return " ".join("".join(out).split())


# ── the tier registry ───────────────────────────────────────────────────────────────────────
# Ordered most-specific first. Each entry is (name, fn) where fn(ctx) -> Verdict | None.
# Tiers are registered by later commits in this phase; the skeleton ships with none, so `route`
# returns None for every input and `off`/`shadow`/`on` are all byte-identical to today. That is
# deliberate: the frame lands and is proven inert BEFORE anything starts answering captains.
_TIERS: list = []


def _install_default_tiers() -> None:
    """Register the shipped tiers. Called at import of `engine.algo`, not here, so this module
    stays importable by a harness that wants an empty registry."""
    from . import followups, greetings
    # Follow-ups FIRST. Both tiers are conjunctions, so order only matters where both could
    # fire — and there the follow-up must win: once a disposition is in scope, "theek hai kitne
    # din?" is a question about the case, not a pleasantry. Greetings would otherwise swallow
    # the acknowledgement tokens and answer "anything else?" to a captain who asked something.
    register("followup", followups.tier)
    register("greeting", greetings.tier)


def register(name: str, fn) -> None:
    """Register a tier. Idempotent on name, so a module reload does not double-register."""
    global _TIERS
    _TIERS = [(n, f) for n, f in _TIERS if n != name] + [(name, fn)]


def tiers() -> list[str]:
    return [n for n, _f in _TIERS]


@dataclass
class Ctx:
    """Everything a tier may read. Assembled by the caller — no tier does I/O.

    Passing a frozen bundle rather than letting tiers reach for what they need is what makes
    them pure, and pure is what makes them testable offline against 1,814 real tickets.
    """

    message: str
    entities: dict
    context: dict                      # the grounded Captain Context
    session: object = None             # engine.session.Session — slots, disposition, turn count
    attachments: list = field(default_factory=list)
    channel: str = "chat"
    prev_action: str | None = None
    #: The id of a reply chip the captain TAPPED, if the client sent one. A tap is an exact
    #: choice: it needs no matching, no threshold and no spelling — which is the entire reason
    #: chips exist for a low-literacy user group. Free text still works; this just skips it.
    selected_option: str | None = None


def route(ctx: Ctx) -> tuple[Verdict | None, dict]:
    """Try every tier. Returns (verdict_or_None, trace_data).

    The trace data is returned even when nothing fires, because "the router declined, and here
    is why" is the observation shadow mode exists to collect. A tier that never fires and a tier
    that fires wrongly look identical without it.

    NEVER RAISES. Any exception inside a tier is caught, recorded, and degrades to None — the
    LLM then handles the turn exactly as it does today. A router that can break a conversation
    is worse than no router.
    """
    trace: dict = {"mode": mode(), "tiers_tried": [], "refusals": [], "declined": []}
    # A per-tier override can bring a tier up from a global `off`, so `off` is no longer a
    # global early return — it is checked per tier below. The fast path is kept for the common
    # case where nothing is overridden, so `off` stays byte-identical to pre-router behaviour.
    if mode() == OFF and not any(os.environ.get(f"PSP_PREROUTER_{n.upper()}")
                                 for n, _f in _TIERS):
        trace["skipped"] = "PSP_PREROUTER=off"
        return None, trace

    try:
        refusals = _refusals(ctx.message, ctx.entities or {}, ctx.attachments, ctx.prev_action)
    except Exception as e:  # noqa: BLE001
        return None, {**trace, "error": f"refusal check: {type(e).__name__}: {e}"[:200]}
    if refusals:
        trace["refusals"] = refusals
        return None, trace

    for name, fn in _TIERS:
        tier_mode = mode(name)
        if tier_mode == OFF:
            trace["declined"].append({"tier": name, "reason": "off"})
            continue
        trace["tiers_tried"].append(name)
        try:
            v = fn(ctx)
        except Exception as e:  # noqa: BLE001 — a tier bug must never break a turn
            trace["declined"].append({"tier": name, "error": f"{type(e).__name__}: {e}"[:160]})
            continue
        if v is None:
            trace["declined"].append({"tier": name, "reason": "preconditions not met"})
            continue
        # In shadow the verdict is computed and REPORTED but not used. The reply is carried in
        # the trace so it can be diffed against what the LLM actually said on the same turn —
        # a tier that is confidently wrong is invisible from absorption numbers alone.
        if tier_mode == SHADOW:
            # CONTINUE, do not return. Under one global mode these were equivalent; with
            # per-tier modes they are not, and returning here would let a tier held in shadow
            # silently suppress a later tier that is live — the shadowed tier would be deciding
            # turns by blocking them, which is the one thing shadow must never do.
            #
            # The FIRST shadow fire is the one recorded: tiers are ordered most-specific first,
            # so it is the verdict that would have been used had the tier been on.
            if "shadow_reply" not in trace:
                trace["shadow_reply"] = v.reply
                trace["shadow_only"] = True
                trace["fired"] = {"tier": v.tier, "because": v.because, "action": v.action,
                                  "options": len(v.options), "shadow": True,
                                  **({"data": v.data} if v.data else {})}
            continue
        trace["fired"] = {"tier": v.tier, "because": v.because, "action": v.action,
                          "options": len(v.options), **({"data": v.data} if v.data else {})}
        # A live tier fired, so nothing was suppressed and the shadow note (if any) is history.
        trace.pop("shadow_only", None)
        return v, trace

    return None, trace
