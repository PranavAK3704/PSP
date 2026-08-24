"""Tier F — the deterministic follow-up engine, scoped per disposition.

THE PROBLEM, MEASURED
Support here is a chatbot/ticketing hybrid, and the users are hub operators with limited
literacy. They ask again, immediately, in simple words. From `tickets.db`:

    35.3% of hub-tagged tickets are a RE-CONTACT within 72h
    55.6% of those re-contacts arrive within ONE HOUR
    26,532 tickets (19% of all 140,875) auto-closed "due to no response from customer"

The first two numbers say the follow-up is the dominant shape of the conversation, not an edge
case. The third says something worse: a fifth of all tickets die because the captain never
replied — and for this user group that is far more likely to be "I did not understand what you
asked me" than "I lost interest". Both problems have the same fix, and it is not a better model.

WHY THIS CAN BE DETERMINISTIC AT ALL — the one idea that makes it work
A follow-up is only unpredictable in the abstract. Once you know the disposition AND what was
just said, the space collapses to a handful of questions. After "your load is low because RTO is
31% against a target of 19%", a captain asks one of about five things: what is RTO, how do I
reduce it, how long until it recovers, who decided the target, or talk to a human. That is not a
classification problem over 537 corpus chunks — it is a **menu with five items**.

So the matcher is scoped by disposition. Scoping is what turns an open NLU problem into a small
closed one, and small closed problems are where algorithms beat models outright.

PRIOR ART, because this is a solved shape elsewhere
  · **Dialogflow follow-up intents + contexts.** A parent intent activates a context; only
    intents scoped to that context can match next. That is exactly `scope` below.
  · **IVR menu trees.** A phone menu is a deterministic per-node option list, and it works for
    every literacy level because the option is READ ALOUD and chosen by one keypress. The
    equivalent here is a tappable chip — which is also why chips are not decoration: a tap is an
    exact match that needs no spelling, no NLU, and no confidence threshold.
  · **Gmail Smart Reply.** Predicts the next utterance by RANKING A FIXED CANDIDATE SET rather
    than generating. Our candidate set is authored per disposition.
  · **Rasa's rule policy over its ML policy.** Deterministic rules take precedence and the model
    is the fallback — the same precedence this router already implements.

WHAT IS NOT ALLOWED HERE
Every answer is authored from material that already exists in the corpus or from a named
upstream source, and carries `source` naming it. Nothing in this file states a process fact I
invented. Where there is no source, there is no node — the turn falls through to the LLM. The
harness asserts every node has a source, because the failure mode of a confident wrong answer
to a low-literacy user is that they act on it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .router import Ctx, Verdict, normalise

#: A chip label must be short enough to read at a glance on a phone, in simple Hinglish.
MAX_CHIP_CHARS = 34
#: How many chips to offer. Four is the IVR convention — beyond that a menu stops being scanned.
MAX_CHIPS = 4
#: Free-text match needs this many scope keywords, and must STRICTLY beat the runner-up.
#:
#: Margin 1 (strictly better), not 2. Overlap between nodes is deliberate — "kitna" belongs to
#: three follow-ups — so a 2-hit margin would decline "kitna nuksan hua" (2 hits vs 1) which is
#: an unambiguous question. Strictly-better answers that; a genuine TIE ("kitne din lagenge",
#: which fits both my-load-recovery and the team's TAT equally) falls through to chips, which is
#: the correct outcome for a question that really is ambiguous.
MIN_HITS = 1
MIN_MARGIN = 1


# Words that carry no signal because they appear in almost every question a captain asks.
#
# THE TRAP, and it is the same one router.py's docstring records about SOP trigger keywords
# (296 of 320 were single common words like "me", "has", "date"): "kya" is in "kya hai",
# "kya karun", "kya hua" — every question. It was in `d_what_evidence`'s match set, and the
# effect was that "hardstop kya hai?" scored 1 hit for the glossary and 1 for evidence, tied,
# and DECLINED — a false decline on a question with an exact authored answer.
#
# The guard is an import-time assertion, not a review habit: a stopword added to a match set
# six months from now fails at import rather than quietly costing a percentage point of
# absorption that nobody attributes to it.
STOPWORDS = frozenset({
    # Hindi/Hinglish function words
    "kya", "hai", "hain", "he", "ho", "hua", "hui", "hue", "ka", "ki", "ke", "ko", "kar",
    "karo", "karna", "mein", "me", "se", "ye", "yeh", "wo", "woh", "ab", "to", "toh", "bhi",
    "aur", "par", "pe", "na", "nahi", "nhi", "koi", "iska", "isme", "uska", "raha", "rahi",
    # Possessives. FOUND BY THE HARNESS: "mera" was a match keyword on `l_my_numbers`, so
    # "mera payment nahi aaya" — a brand new concern in a different queue — scored one hit and
    # was answered with an RTO figure. "mera" prefixes everything a captain owns: mera payment,
    # mera load, mera loss, mera paisa. The signal in "mera number kya hai" is "number".
    "mera", "mere", "meri", "apna", "apni", "hamara", "aapka", "aapki", "uska", "tumhara",
    # English function words
    "is", "are", "was", "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "what",
    "my", "i", "it", "this", "that", "do", "does", "did", "be", "will", "can",
})


# Interrogative markers. A GLOSSARY answer is a definition, and a definition is only ever the
# right reply to a question — so a glossary node needs one of these present.
INTERROGATIVES = frozenset({
    "kya", "kaise", "kese", "kyun", "kyu", "kaun", "kon", "kitna", "kitne", "kitni", "kab",
    "kahan", "kaha", "matlab", "meaning", "what", "why", "how", "when", "who", "which",
    "explain", "samjhao", "batao", "bataiye",
})

# Imperative markers — the captain is asking for something to be DONE.
#
# FOUND BY THE HARNESS: "cod pendency clear karo" scored one glossary hit on "pendency" and was
# answered with the DEFINITION of Days On Hand. A captain asking for their pendency to be
# cleared, in a different queue, got a vocabulary lesson. An action request answered with a
# definition is the most patronising failure this engine could have, and for someone with
# limited literacy it reads as the system not understanding them at all — which the auto-close
# numbers say is already the commonest way these conversations die.
ACTION_WORDS = frozenset({
    "karo", "kardo", "kariye", "kijiye", "dijiye", "dedo", "dena", "chahiye", "clear",
    "karwao", "karvao", "solve", "fix", "please", "jaldi", "turant", "abhi",
})


@dataclass(frozen=True)
class FollowUp:
    """One predictable next question, and its authored answer."""

    id: str
    ask: str                       # the chip label the captain taps
    answer: str                    # what they get back. May carry {fact} placeholders.
    source: str                    # where the answer's content comes from. Never empty.
    match: frozenset = frozenset()  # free-text keywords, for a captain who types instead
    then: tuple = ()               # ids of the follow-ups to offer AFTER this one
    #: Placeholders this answer needs. If a fact is missing the node is SKIPPED rather than
    #: rendered with a hole — "your RTO is {rto}%" with no value is worse than not offering it.
    needs: tuple = ()


# ═══ GLOSSARY — "X kya hai?", askable at any point, scope-independent ════════════════════════
# Sourced, term by term. The captain panel's own METRIC_TARGET_TOOLTIPS and the PBCA KT are the
# authorities for the O&P terms; the loss definitions come from the SOP corpus.
GLOSSARY: tuple[FollowUp, ...] = (
    FollowUp(
        id="g_rto", ask="RTO kya hai?",
        answer=("RTO = Return To Origin — jo shipment customer tak deliver nahi hoti aur wapas "
                "aa jaati hai. Aapka RTO% jitna zyada, load utna kam milta hai — kyunki volume "
                "RTO% aur OCF se decide hota hai. Target aapke area ke best 3PL ke hisaab se "
                "set hota hai."),
        source="kt_ea0d78507c (volume decreases when RTO% is high) + captain panel "
               "METRIC_TARGET_TOOLTIPS (target = best 3PL in your area)",
        match=frozenset({"rto", "return", "origin"})),
    FollowUp(
        id="g_ocf", ask="OCF kya hai?",
        answer=("OCF = Order Contribution Factor. Yahi metric decide karta hai kis DC ko kitna "
                "load milega. Ismein takriban 25 factors hote hain, aur unka combination aapke "
                "hub ka volume tay karta hai. Dhyan dijiye — OCF volume decide karta hai, "
                "payment nahi."),
        source="kt_630fddb712 (OCF = order contribution factor, ~25 factors, controls volume) "
               "+ kt_ea0d78507c (OCF determines volume not payment)",
        match=frozenset({"ocf", "order", "contribution", "factor"})),
    FollowUp(
        id="g_cps", ask="CPS / rate card kya hai?",
        answer=("CPS = Cost Per Shipment, yaani aapka Pilot Rate Card. Aapka target rate card "
                "aas-paas ke DCs ke rate se calculate hota hai — agar aapka rate unse zyada "
                "hai to allocation par asar padta hai."),
        source="captain panel METRIC_TARGET_TOOLTIPS: 'The target rate card is calculated "
               "based on your neighbouring DCs rate'",
        match=frozenset({"cps", "rate", "card", "pilot"})),
    FollowUp(
        id="g_day0", ask="Day-0 attempt kya hai?",
        answer=("Day-0 Attempt % = jitne shipment aapko us din mile, unmein se kitne aapne "
                "USI din delivery attempt kiye. Ise 70% se upar rakhna zaroori hai — neeche "
                "gaya to capacity cut lag sakta hai."),
        source="captain panel METRIC_TARGET_TOOLTIPS: 'keep your performance above 70% to "
               "avoid capacity cut'",
        match=frozenset({"day0", "day", "0", "attempt", "atmpt"})),
    FollowUp(
        id="g_doh", ask="Pendency / DOH kya hai?",
        answer=("Pendency (DOH = Days On Hand) = shipment aapke hub par kitne din pade rahe. "
                "Ise 2.5 din se kam rakhna hota hai — zyada hua to capacity cut ka risk hai."),
        source="captain panel METRIC_TARGET_TOOLTIPS: 'please clear pendencies within 2.5 "
               "days to avoid capacity cut'",
        match=frozenset({"doh", "pendency", "pending", "days", "hand"})),
    FollowUp(
        id="g_pbca", ask="Capacity cut kaise lagta hai?",
        answer=("Ise PBCA kehte hain — Performance Based Capacity Action. Aapke hub ki "
                "performance usi pincode ke doosre 3P hubs se compare hoti hai. Performance = "
                "Conversion (FAD + RAD) − ZRTO. Agar aapka hub best 3P hub se peeche hai, to "
                "70% tak load kam ho sakta hai. Uske baad aapko 10 din milte hain sudharne ke "
                "liye."),
        source="kt_lm_pbca (PBCA formula, up to 70% cut, 10 days to improve)",
        match=frozenset({"pbca", "capacity", "cut", "kata", "kam"})),
    FollowUp(
        id="g_bic", ask="BIC kya hai?",
        answer=("BIC = Best In Class. Aapke area ka sabse accha perform karne wala hub. Agar "
                "aapko kam load mil raha hai, wajah yeh ho sakti hai ki aap BIC nahi hain — "
                "targets usi BIC hub ke hisaab se set hote hain."),
        source="kt_040e00381f (BIC stands for best in class; less load because not a BIC)",
        match=frozenset({"bic", "best", "class"})),
    FollowUp(
        id="g_hardstop", ask="Hardstop kya hai?",
        answer=("Hardstop loss tab lagta hai jab shipment ek hi hub par **5 din (120 ghante) "
                "se zyada** pada rehta hai aur agle node tak connect nahi hota. System "
                "automatically use hardstop loss mark kar deta hai."),
        source="sopkt_1_hardstop_loss (more than 5 days / 120 hours without connecting)",
        match=frozenset({"hardstop", "hard", "stop"})),
    FollowUp(
        id="g_shortage", ask="Shortage loss kya hai?",
        answer=("Shortage loss tab lagta hai jab shipment Node A se bheja gaya lekin Node B "
                "par receive nahi hua — beech mein kam paya gaya. Ismein evidence submit karke "
                "reversal maanga ja sakta hai."),
        source="sopkt_2_shortage_loss (shipment sent from Node A to Node B)",
        match=frozenset({"shortage", "short", "kami"})),
)

# ═══ PER-DISPOSITION GRAPHS ══════════════════════════════════════════════════════════════════
# Only the dispositions the engine can actually RESOLVE have graphs, because a follow-up to an
# escalation is a different thing (the answer is "the team has it") and is covered by UNIVERSAL.

_LOAD = (
    FollowUp(
        id="l_why_low", ask="Load kam kyun hua?",
        answer=("Aapka load do cheezon se tay hota hai — RTO% aur OCF rate card. Jab in mein "
                "se koi target se peeche hota hai, allocation mein aapko kam orders milte "
                "hain. Jo metric peeche hai wahi wajah hai."),
        source="kt_ea0d78507c + faq_8227 (load is determined by RTO% and the OCF rate card)",
        match=frozenset({"kyun", "kyu", "why", "wajah", "reason", "kam", "low"}),
        then=("l_how_fix", "g_rto", "g_ocf")),
    FollowUp(
        id="l_how_fix", ask="Kaise theek karun?",
        answer=("Jo metric target se peeche hai usi par kaam kijiye — RTO kam kijiye, Day-0 "
                "attempt 70% se upar laiye, aur pendency 2.5 din se kam rakhiye. Yeh sudhrenge "
                "to allocation apne aap badhega."),
        source="captain panel METRIC_TARGET_TOOLTIPS (the four levers and their thresholds)",
        match=frozenset({"kaise", "kese", "how", "theek", "thik", "fix", "sudhar", "improve",
                         "badhau", "badhaun"}),
        then=("l_how_long", "g_pbca")),
    FollowUp(
        id="l_how_long", ask="Kitne din lagenge?",
        answer=("Capacity cut lagne ke baad aapko **10 din** milte hain performance sudharne "
                "ke liye. Us window ke baad hi capacity recovery hoti hai — aur agar aap best "
                "3P hub se accha perform karte hain to 80% tak load badh sakta hai."),
        source="kt_lm_pbca (10 days to improve; up to 80% increase, only after 10 days)",
        match=frozenset({"kitne", "kitna", "din", "time", "long", "kab", "days", "when"}),
        then=("l_will_increase",)),
    FollowUp(
        id="l_will_increase", ask="Phir load badhega?",
        answer=("Haan — agar aap 10 din mein best 3P hub se behtar perform karte hain, to "
                "capacity recovery lagti hai aur 80% tak load badh sakta hai. Iske alawa jaise "
                "aapke polygon mein demand badhegi, volume bhi badhega."),
        source="kt_lm_pbca (capacity recovery up to 80% after 10 days) + scn_OP_1/scn_OP_2 "
               "(as demand increases in your polygon, load may increase)",
        match=frozenset({"phir", "badhega", "increase", "wapas", "recover", "milega"})),
    # ── the two fact-filled nodes. Every value here was computed SERVER-SIDE by
    # `_exec_load_planning` and already published to this captain on their own dashboard — the
    # follow-up engine re-reads it, never re-derives it. If a fact is absent the node is not
    # offered at all, which is why `needs` exists.
    FollowUp(
        id="l_my_numbers", ask="Mera number kya hai?",
        answer=("Aapka {lever} abhi **{current}** hai, aur target **{target}** hai. Yahi metric "
                "target se peeche hai — isi par kaam karna hai."),
        source="_exec_load_planning evidence trail — the failing lever, its value and its "
               "target, read from growth-dashboard and never re-derived",
        # "mera" is deliberately absent — it is a stopword (mera payment, mera load, mera loss).
        # The signal in "mera number kya hai" is "number".
        match=frozenset({"number", "value", "metric", "score", "figure", "aankda"}),
        needs=("lever", "current", "target"),
        then=("l_how_fix",)),
    FollowUp(
        id="l_money_lost", ask="Kitna nuksan hua?",
        answer=("Is cycle mein jo orders miss hue, unki value takriban **₹{loss}** hai. Aapko "
                "{orders} orders mile, mil sakte the {max_potential}."),
        source="order-summary extra_earnings_loss + current_orders/max_potential — server-side "
               "figures, shown on the captain's own dashboard",
        match=frozenset({"nuksan", "loss", "paisa", "kitna", "rupee", "kamai", "earning"}),
        needs=("loss", "orders", "max_potential"),
        then=("l_how_fix", "l_how_long")),
    FollowUp(
        id="l_who_decides", ask="Target kaun decide karta hai?",
        answer=("Target aapke area ke sabse acche 3PL hub (BIC) ke performance se set hota "
                "hai — RTO ka target best 3PL se, rate card ka target aas-paas ke DCs ke rate "
                "se. Yeh manually kisi ne aapke liye set nahi kiya."),
        source="captain panel METRIC_TARGET_TOOLTIPS + kt_lm_pbca (compared against the best "
               "3P hub in the same pincode)",
        match=frozenset({"kaun", "kon", "who", "decide", "target", "set", "kisne"}),
        then=("g_bic",)),
)

_LOSS = (
    FollowUp(
        id="d_why_marked", ask="Ye loss kyun laga?",
        answer=("Loss tab lagta hai jab shipment expected node tak time par nahi pahunchti — "
                "hardstop mein 5 din se zyada ek hub par rukne se, shortage mein Node B par "
                "receive na hone se. System yeh automatically mark karta hai, koi manually "
                "nahi karta."),
        source="sopkt_1_hardstop_loss + sopkt_2_shortage_loss",
        match=frozenset({"kyun", "kyu", "why", "wajah", "laga", "marked"}),
        then=("g_hardstop", "g_shortage", "d_can_reverse")),
    FollowUp(
        id="d_can_reverse", ask="Paisa wapas milega?",
        answer=("Agar record mein reversal ka signal hai — jaise facility in-scan ho gaya ho, "
                "ya attribution badal gayi ho — to main ise reversal ke liye Losses & Debits "
                "(L2) team ko bhej deta hoon. **Main khud paisa wapas nahi kar sakta** — woh "
                "team hi karti hai. Main aapko sirf yeh bata sakta hoon ki case bhej diya gaya "
                "hai; settlement ki confirmation wahi team degi."),
        source="engine/write_mode.py — there is no write path; a favourable decision is a "
               "recommendation to L2, never a payment",
        match=frozenset({"paisa", "wapas", "reverse", "reversal", "refund", "milega", "money"}),
        then=("u_how_long_team",)),
    FollowUp(
        id="d_what_evidence", ask="Kya evidence chahiye?",
        answer=("Shortage ke case mein evidence mail karke reversal maanga jaata hai. Sabse "
                "kaam ki cheezein: AWB number, shipment ki photo ya video, aur jis din bheja "
                "tha uska record. Yeh sab hone se team turant verify kar paati hai."),
        source="sopkt_2_shortage_loss + faq_8220 (shortage loss marked even after evidence "
               "submitted)",
        match=frozenset({"evidence", "proof", "photo", "document", "chahiye", "sabut",
                         "dastavez"})),
)

# ═══ UNIVERSAL — askable after ANY answer, including an escalation ════════════════════════════
UNIVERSAL = (
    FollowUp(
        id="u_how_long_team", ask="Team kitne din lega?",
        answer=("Losses & Debits (L2) ka TAT 24 ghante hai, Payments 24 ghante, Cash/COD 12 "
                "ghante, aur Orders & Planning 24 ghante. Is window ke andar aapko update "
                "milega. Agar na mile to mujhe bata dijiye."),
        source="l3/platform.py TEAM_SLA (the real per-team SLA table)",
        match=frozenset({"kitne", "kitna", "din", "time", "team", "kab", "long", "tat"})),
    FollowUp(
        id="u_who_has_it", ask="Kisko bheja hai?",
        answer=("Aapka case us team ko gaya hai jo is queue ki owner hai — loss/debit ke liye "
                "Losses & Debits (L2), payment ke liye Payments (L2), cash ke liye Cash/COD "
                "(L2), load ke liye Orders & Planning (L2). Aapke reply mein reference number "
                "diya gaya hai, wahi quote kijiye."),
        source="engine/tools.py _DOMAIN_TEAM (the real routing table)",
        match=frozenset({"kisko", "kon", "kaun", "who", "team", "bheja", "gaya"})),
    FollowUp(
        id="u_talk_human", ask="Insaan se baat karni hai",
        answer=("Bilkul. Main aapka case seedha team ke paas bhej deta hoon taaki koi insaan "
                "isse dekhe — aap yahin likh dijiye ki kya dikkat hai aur main poori detail "
                "ke saath aage bhej doonga."),
        source="tools.escalate_case — the engine never dead-ends a captain",
        match=frozenset({"insaan", "human", "aadmi", "banda", "call", "phone", "baat", "agent",
                         "sir", "officer"})),
)

#: disposition -> its follow-up graph. Keyed on the disposition the ENGINE decided, not on
#: anything the model guessed.
GRAPHS: dict[str, tuple[FollowUp, ...]] = {
    "load_planning": _LOAD,
    "capacity_panel_issue": _LOAD,          # same levers, same answers
    "hardstop_loss": _LOSS,
    "shortage_loss": _LOSS,
    "intransit_loss": _LOSS,
    "bag_shortage": _LOSS,
    "shipment_shortage": _LOSS,
    "debit_revoked": _LOSS,
    "secondary_qc_fail": _LOSS,
}

_BY_ID = {f.id: f for f in (GLOSSARY + UNIVERSAL + _LOAD + _LOSS)}


def _scope(disposition: str | None) -> tuple[FollowUp, ...]:
    """The follow-ups live right now: this disposition's graph, plus glossary and universal.

    Glossary is always in scope because "RTO kya hai?" is a reasonable question at any point,
    and refusing it because the conversation was about something else would be pedantic.
    """
    return GRAPHS.get((disposition or "").strip(), ()) + GLOSSARY + UNIVERSAL


def _renderable(f: FollowUp, facts: dict) -> bool:
    """A node whose placeholders cannot all be filled is SKIPPED, never rendered with a hole."""
    return all(k in facts and facts[k] not in (None, "") for k in f.needs)


def chips_for(disposition: str | None, *, facts: dict | None = None,
              already: set | None = None, after: str | None = None) -> list[dict]:
    """The chips to offer. `after` narrows to that node's children — the graph edge.

    Returns [{id, label}] in offer order. Deliberately small: MAX_CHIPS, because a menu longer
    than four stops being read, which is the whole finding behind IVR design.
    """
    facts, already = facts or {}, set(already or set())
    if after:
        # The node just answered is never re-offered, even if the caller forgot to pass it in
        # `already`. Re-offering the question the captain has this second read as the reply is
        # the single most obviously-broken thing a chip row can do.
        already.add(after)
    ordered: list[FollowUp] = []
    if after and after in _BY_ID:
        # Walk the edge. A parent's `then` is the authored prediction of what comes next.
        ordered += [_BY_ID[i] for i in _BY_ID[after].then if i in _BY_ID]
    ordered += list(_scope(disposition))
    out, seen = [], set()
    for f in ordered:
        if f.id in seen or f.id in already or not _renderable(f, facts):
            continue
        if len(f.ask) > MAX_CHIP_CHARS:
            continue
        seen.add(f.id)
        out.append({"id": f.id, "label": f.ask})
        if len(out) >= MAX_CHIPS:
            break
    return out


def resolve(message: str, disposition: str | None, *, facts: dict | None = None,
            selected: str | None = None) -> tuple[FollowUp | None, str]:
    """Match a message (or a tapped chip) to a follow-up in scope. (node, why).

    A TAP is exact — `selected` is a node id and needs no matching at all, which is the entire
    reason chips exist for this user group. Free text falls back to keyword hits within the
    scope, and the scope is small enough (a dozen nodes) that a hit count with a margin is
    reliable where the same approach over 537 corpus chunks measured P 0.790.
    """
    if selected:
        node = _BY_ID.get(selected)
        if node is None:
            return None, f"unknown option {selected!r}"
        # A TAP IS STILL SCOPE-CHECKED. The chips are server-generated, so a well-behaved client
        # only ever sends back an id it was offered — but `selected_option` arrives on the
        # request body from a client, and trusting it blindly would make the scope gate
        # bypassable by anyone who can craft a POST: send `l_my_numbers` while disputing a loss
        # and get an answer about load allocation. The scope is the entire safety argument for
        # answering deterministically, so it is enforced on both paths, not just on free text.
        if node.id not in {f.id for f in _scope(disposition)}:
            return None, f"option {selected!r} is out of scope for {disposition!r}"
        return node, "tapped"

    toks = set(normalise(message).split())
    if not toks:
        return None, "empty"
    scope = _scope(disposition)

    # A definition needs a question. Filtered out of the scope BEFORE scoring rather than
    # rejected after, so a blocked glossary node cannot tie with — and thereby suppress — a
    # legitimate follow-up that would have answered the turn.
    if not (toks & INTERROGATIVES) or (toks & ACTION_WORDS):
        gloss = {f.id for f in GLOSSARY}
        scope = tuple(f for f in scope if f.id not in gloss)
    scored = sorted(((len(toks & f.match), f) for f in scope if toks & f.match),
                    key=lambda kv: -kv[0])
    if not scored:
        return None, "no keyword overlap in scope"
    best_n, best = scored[0]
    if best_n < MIN_HITS:
        return None, f"weak: {best_n} hit(s)"
    runner = scored[1][0] if len(scored) > 1 else 0
    if best_n - runner < MIN_MARGIN:
        # Ambiguous — two follow-ups fit equally well. ASK rather than pick: a wrong guess costs
        # the captain a tap, a wrong ANSWER costs them a wrong action. (No `runner > 0` guard is
        # needed — if there is no runner-up, `best_n - 0 >= MIN_HITS >= MIN_MARGIN` already.)
        return None, f"ambiguous: {best_n} vs {runner} — offer chips instead"
    return best, f"{best_n} keyword hit(s), margin {best_n - runner}"


# ── channel degradation: the same menu, without buttons ──────────────────────────────────────
# WhatsApp is 81.6% of tickets and `channels/whatsapp.py: send(phone, text)` is TEXT ONLY — no
# interactive buttons, and wiring Meta's interactive-message API needs a Business account this
# environment does not have. So the chip row degrades to a numbered list, which is the IVR form
# of the same idea and needs nothing from the transport.
#
# The reply-side of that degradation is `ordinal_choice`: a captain who answers "2" must land on
# option 2 exactly, or the numbered menu is decoration.

def as_numbered_text(reply: str, chips: list[dict]) -> str:
    """Append the offered options as a numbered list. For any transport with no buttons."""
    if not chips:
        return reply
    lines = [f"{n}. {c['label']}" for n, c in enumerate(chips, 1)]
    return reply + "\n\n" + "\n".join(lines) + "\n\n(Number likh dijiye, ya seedha poochh lijiye.)"


def ordinal_choice(message: str, offered: list | None) -> str | None:
    """Map a bare "2" back to the id offered at position 2. None if it is not a bare choice.

    THE TRAP: a lone number is not always a menu choice. "500" could be an amount and "2" could
    answer "how many days has it been pending?". Three conditions together make this safe, and
    all three are necessary:
      1. options were actually offered on the previous turn (`offered` non-empty),
      2. the message is ONLY the number — "2 din se pending hai" is prose, not a choice,
      3. the number is within range — so an amount or an AWB fragment falls straight through.
    """
    if not offered:
        return None
    tok = normalise(message).strip()
    if not tok.isdigit():
        return None
    n = int(tok)
    if not 1 <= n <= len(offered):
        return None
    return offered[n - 1]


def tier(ctx: Ctx) -> Verdict | None:
    """Tier F. Fires only when a previous turn established a disposition to scope by.

    Without a scope this tier is exactly the open-NLU problem it exists to avoid, so no
    disposition means no answer — turn one always goes to the LLM or another tier.
    """
    sess = ctx.session
    disp = getattr(sess, "disposition", None) if sess else None
    facts = dict(getattr(sess, "answer_facts", {}) or {}) if sess else {}
    already = set(getattr(sess, "offered", set()) or set()) if sess else set()
    if not disp:
        return None
    if (ctx.entities or {}).get("any"):
        # An identifier means this is a new concern with evidence, not a follow-up question.
        return None

    node, why = resolve(ctx.message, disp,
                        facts=facts, selected=getattr(ctx, "selected_option", None))
    if node is None:
        return None
    if not _renderable(node, facts):
        return None

    reply = node.answer.format(**facts) if node.needs else node.answer
    chips = chips_for(disp, facts=facts, already=already | {node.id}, after=node.id)
    return Verdict(
        tier="followup",
        reply=reply + ("\n\nKuch aur poochna hai?" if chips else ""),
        because=f"{node.id} in scope '{disp}' — {why}",
        action="respond",
        options=chips,
        data={"node": node.id, "scope": disp, "source": node.source, "why": why},
    )


# ── import-time structural guards ───────────────────────────────────────────────────────────
# These run once, at import, and fail loudly. Each encodes a defect that was actually found
# while building this file, so each is a regression test that cannot be skipped or forgotten.
def _selfcheck() -> None:
    ids = [f.id for f in (GLOSSARY + UNIVERSAL + _LOAD + _LOSS)]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate follow-up ids: {dupes}"
    for f in _BY_ID.values():
        # An answer with no provenance is the failure mode this whole file is built to avoid:
        # a confident wrong process fact, acted on by someone with no way to check it.
        assert f.source, f"{f.id} has no source"
        assert f.ask and len(f.ask) <= MAX_CHIP_CHARS, f"{f.id}: chip label too long"
        bad = f.match & STOPWORDS
        assert not bad, f"{f.id} matches on stopword(s) {bad} — see the STOPWORDS note"
        # A GLOSSARY node must not match on an interrogative — it already REQUIRES one to be in
        # scope at all, so keeping one in its match set would double-count the same evidence and
        # let a bare "kya hai?" carry a definition on its own.
        #
        # Deliberately not applied to the other nodes: "kitne din lagenge" is a real follow-up
        # and "kitne"/"din" are exactly what identifies it. An interrogative is a stopword only
        # where the gate has already spent it.
        if f.id in {g.id for g in GLOSSARY}:
            overlap = f.match & INTERROGATIVES
            assert not overlap, f"glossary {f.id} matches on interrogative(s) {overlap}"
        for child in f.then:
            assert child in _BY_ID, f"{f.id} points at unknown follow-up {child!r}"
        # Every placeholder in the answer must be declared in `needs`, and vice versa —
        # otherwise `.format()` raises KeyError mid-turn, or a declared fact silently does
        # nothing and the node is withheld for no reason.
        import string
        holes = {n for _t, n, _s, _c in string.Formatter().parse(f.answer) if n}
        assert holes == set(f.needs), f"{f.id}: answer holes {holes} != needs {set(f.needs)}"
    for disp, graph in GRAPHS.items():
        assert graph, f"disposition {disp!r} maps to an empty graph"


_selfcheck()
