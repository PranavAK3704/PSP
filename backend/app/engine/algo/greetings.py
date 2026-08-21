"""Tier A — greetings and pleasantries. The cheapest real absorption available.

WHY THIS IS WORTH A FILE
A captain typing "ok thanks bhai" costs the same model call as a loss dispute — measured at
₹4.00–₹5.29 a turn. A phrase list answers it exactly, instantly, for nothing, and does so more
reliably than a model: there is no temperature on a dictionary lookup.

MATCHING IS A TOKEN-MULTISET TEST, NOT A SUBSTRING TEST
Every token in the message must be in the whitelist. That single decision is what makes this
safe:

    "ok thanks sir"          -> every token known           -> FIRES
    "ok but my payment"      -> "but", "my", "payment"      -> declines
    "thanks, load kam hai"   -> "load", "kam", "hai"        -> declines

A substring or any-token match would fire on the second and third, answering "you're welcome"
to a captain reporting a problem. That is the whole failure mode, and it is why the rule is
`all()` rather than `any()`.

THE HONEST NOTE ON VOLUME
I measured 0 greetings in the 1,814 labelled tickets. That corpus is the EMAIL channel, which
is the wrong place to look — WhatsApp is 81.6% of tickets and the repo holds none of its text.
So this ships comprehensive on the operator's prior, and makes no absorption claim until shadow
mode reports one from live traffic.

── FALSE-POSITIVE TRAPS, each one real ─────────────────────────────────────────────────────
1. "ok" is a token in "ok mera payment nahi aaya". Handled by the all-tokens rule.
2. "ha"/"han"/"haan" mean yes — but a bare "haan" answering "should I escalate?" is CONSENT,
   not a pleasantry. `conversation._SYSTEM` is explicit that a yes to "should I escalate?"
   authorises escalation, so an acknowledgement after a question the engine asked must NOT be
   swallowed here. Guarded by `prev_asked_question`.
3. "thik hai" / "theek hai" can mean "fine, do it" as readily as "understood". Same guard.
4. "sir"/"madam"/"boss"/"bhai" are vocatives, not greetings. A message of ONLY vocatives is a
   captain trying to get attention — greeted, not answered. But they must never make an
   otherwise-unknown message look known, so they are whitelisted only as companions.
5. "gm" is also an abbreviation for General Manager in escalation talk ("gm ko bolo"). Two
   tokens means it is not a bare greeting, so the all-tokens rule covers it.
6. Numbers. "ok 500" normalises to tokens {"ok", "500"} — a digit token is never in the
   whitelist, so an amount or an AWB fragment always declines.
"""
from __future__ import annotations

from .router import Ctx, Verdict, normalise

# ── openers: the captain is starting a conversation ─────────────────────────────────────────
OPENERS = frozenset({
    "hi", "hii", "hiii", "hello", "helo", "hlo", "hey", "heyy", "hai", "hallo",
    "namaste", "namaskar", "namaskaram", "salaam", "salam", "assalamualaikum",
    "ram", "jai", "shri", "radhe",                       # "ram ram", "jai shri ram"
    "gm", "ge",
    # "good morning/afternoon/evening" are openers. "night" is NOT — "good night" is how a
    # conversation ENDS, so it lives in CLOSERS along with its abbreviation "gn". Leaving it
    # here made "good night" score 2 opener tokens to 1 closer and reply "what's the problem?"
    # to a captain saying goodbye.
    "good", "morning", "afternoon", "evening",
    "start", "hlw", "yo",
    # Devanagari
    "नमस्ते", "नमस्कार", "हैलो", "हलो", "सलाम", "राम",
})

# ── closers and acknowledgements: the captain is wrapping up ─────────────────────────────────
CLOSERS = frozenset({
    "ok", "okay", "oky", "okk", "k", "kk", "kay", "acha", "achha", "accha",
    "thanks", "thank", "thnx", "thx", "tnx", "ty", "thanku", "thankyou", "shukriya",
    "dhanyawad", "dhanyavad", "dhanywad",
    "done", "sure", "noted", "got", "it", "received", "recieved",
    "super", "great", "nice", "good", "perfect", "awesome", "cool", "fine",
    "bye", "byee", "tata", "alvida", "night", "gn",   # "good night" — see the note in OPENERS
    "theek", "thik", "tik", "hai", "h",                  # "theek hai", "thik h"
    "samajh", "gaya", "samjha",                          # "samajh gaya"
    "chalo", "bas", "bilkul", "sahi",
    # Devanagari
    "धन्यवाद", "शुक्रिया", "ठीक", "है", "अच्छा", "समझ", "गया", "बढ़िया",
})

# ── affirmations: yes-words. Whitelisted, but see trap 2 — guarded separately. ───────────────
AFFIRMATIONS = frozenset({"ha", "han", "haan", "haa", "ji", "yes", "yep", "yeah", "y",
                          "हाँ", "हां", "जी"})

# ── vocatives and fillers: allowed as COMPANIONS only ───────────────────────────────────────
# These never make a message match on their own — `_is_greeting` requires at least one token
# from OPENERS or CLOSERS or AFFIRMATIONS. Their job is to stop "thanks bhai" from declining.
COMPANIONS = frozenset({
    "sir", "madam", "mam", "maam", "boss", "bhai", "bhaiya", "bhaiji", "ji", "saab", "sahab",
    "veer", "anna", "bro", "dear", "team", "you", "u", "very", "much", "so", "a", "lot",
    "please", "pls", "plz", "aap", "aapka", "mera", "hi",
    "सर", "भाई", "जी", "आप",
})

#: Emoji-only messages. Normalisation strips emoji to spaces, so an emoji-only message becomes
#: the empty string — which is how this is detected rather than by matching the emoji itself.
_EMOJI_ONLY_MEANS_ACK = True

_ALL = OPENERS | CLOSERS | AFFIRMATIONS | COMPANIONS
_SIGNIFICANT = OPENERS | CLOSERS | AFFIRMATIONS

# Longest message this tier will look at, in tokens. A ten-word message that happens to contain
# only whitelisted words is far more likely to be a sentence the whitelist does not understand
# than an unusually verbose "thanks".
MAX_TOKENS = 6

OPENER_REPLY = ("Namaste! Main Valmo ka support assistant hoon. Bataiye, kya dikkat hai — "
                "loss ya debit, payment, load, COD, kuch bhi. Aap seedha likh dijiye.")
CLOSER_REPLY = "Theek hai! Kuch aur ho to bata dijiye — main yahin hoon."


def classify(message: str, *, prev_asked_question: bool = False) -> tuple[str, str] | None:
    """(kind, reply) for a greeting, or None. `kind` is "opener" | "closer".

    Pure and side-effect free, so the golden-file harness can run it over hundreds of phrasings
    without any engine state.
    """
    norm = normalise(message)

    # Emoji-only. Normalisation strips non-alphanumerics, so a 👍 or 🙏 message is now empty.
    # An empty ORIGINAL message is not a greeting — it is nothing — so the original must have
    # had content for this to count.
    if not norm:
        return ("closer", CLOSER_REPLY) if (_EMOJI_ONLY_MEANS_ACK and (message or "").strip()) \
            else None

    toks = norm.split()
    if len(toks) > MAX_TOKENS:
        return None
    if not all(t in _ALL for t in toks):
        return None
    if not any(t in _SIGNIFICANT for t in toks):
        # Companions only — "sir bhai please". Attention-seeking, not a pleasantry. Treated as
        # an opener so the captain gets a prompt rather than a dead "you're welcome".
        return ("opener", OPENER_REPLY)

    # TRAP 2/3: a bare affirmation right after the engine asked something is CONSENT, and
    # conversation._SYSTEM says a yes to "should I escalate?" authorises escalation. Swallowing
    # it here would drop an instruction.
    if prev_asked_question and toks and all(t in (AFFIRMATIONS | COMPANIONS | CLOSERS)
                                            for t in toks):
        return None

    opener_hits = sum(1 for t in toks if t in OPENERS)
    closer_hits = sum(1 for t in toks if t in CLOSERS or t in AFFIRMATIONS)
    # "good morning" is an opener; "good" alone in "good, thanks" is a closer. Ties go to
    # CLOSER, because an ambiguous short message late in a conversation is far more often an
    # acknowledgement than a fresh hello — and a wrong closer ("anything else?") is a gentler
    # failure than a wrong opener ("what's the problem?") at someone who just said thanks.
    return ("opener", OPENER_REPLY) if opener_hits > closer_hits else ("closer", CLOSER_REPLY)


def _last_model_text(session) -> str:
    for entry in reversed(getattr(session, "contents", []) or []):
        if entry.get("role") == "model":
            return "".join(p.get("text", "") for p in entry.get("parts", []) if "text" in p)
    return ""


def tier(ctx: Ctx) -> Verdict | None:
    """Tier A. Fires only when NO identifier was extracted and nothing was attached."""
    ents = ctx.entities or {}
    if ents.get("any"):
        # An AWB, amount or UTR in the message means it is not a pleasantry, whatever else it
        # contains. "thanks VL0084…" is a captain following up on a shipment.
        return None

    asked = _last_model_text(ctx.session).rstrip().endswith("?") if ctx.session else False
    got = classify(ctx.message, prev_asked_question=asked)
    if got is None:
        return None
    kind, reply = got
    return Verdict(
        tier="greeting",
        reply=reply,
        because=f"{kind}: every token in the pleasantry whitelist "
                f"({len(normalise(ctx.message).split())} token(s))",
        action="respond",
        data={"kind": kind, "tokens": normalise(ctx.message).split()},
    )
