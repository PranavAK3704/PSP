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
31% against a target of 19%", a captain asks one of about five things. That is not a
classification problem over 537 corpus chunks — it is a **menu with five items**.

So the matcher is scoped by disposition. Scoping is what turns an open NLU problem into a small
closed one, and small closed problems are where algorithms beat models outright.

PRIOR ART, because this is a solved shape elsewhere
  · **Dialogflow follow-up intents + contexts.** A parent intent activates a context; only
    intents scoped to that context can match next. That is exactly `_scope` below.
  · **IVR menu trees.** A phone menu is a deterministic per-node option list, and it works for
    every literacy level because the option is READ ALOUD and chosen by one keypress. The
    equivalent here is a tappable chip — which is also why chips are not decoration: a tap is an
    exact match that needs no spelling, no NLU, and no confidence threshold.
  · **Gmail Smart Reply.** Predicts the next utterance by RANKING A FIXED CANDIDATE SET rather
    than generating. Our candidate set is authored per disposition.
  · **Rasa's rule policy over its ML policy.** Deterministic rules take precedence and the model
    is the fallback — the same precedence this router already implements.

══ THE TWO RULES THAT MAKE MATCHING SAFE, both learned the hard way ═════════════════════════

An adversarial review of the first version of this file reproduced 25 wrong answers. Almost
every one was a single common word carrying a whole node. The two rules below are the structural
answer; the per-node comments record the specific phrasings that forced them.

**RULE 1 — A MATCH NEEDS A TOPIC, NOT JUST A WORD.**
Each node's vocabulary is split. `topic` (or a `phrases` group) names WHAT THE QUESTION IS
ABOUT, and at least one must hit. `frame` is the question's grammar — interrogatives,
auxiliaries, politeness — which may add to the score but can never carry a node alone.

Without this, frame words fired on their own and the answers were confidently wrong:

    "cod jama karna hai kaise"  -> bare "kaise" carried l_how_fix     -> load levers
    "debit kyu laga"            -> bare "kyu"   carried l_why_low     -> load allocation
    "mera id block ho gaya"     -> bare "gaya"  carried u_who_has_it  -> "your case was sent"
    "sir jaldi kuch kijiye"     -> bare "sir"   carried u_talk_human  -> a handoff promise
    "load kab badhega"          -> bare "kab"   carried u_how_long_team -> the SLA table

A vocative ("sir") is never a topic. An auxiliary ("gaya", "hua", "milega") is never a topic.
An interrogative ("kaise", "kyun", "kab", "kitna") is never a topic.

**RULE 2 — A FOREIGN QUEUE REFUSES THE TURN.**
If the message names a queue this scope does not serve, it is not a follow-up — it is a new
concern, and answering it from this scope's table is the worst failure available here. Reuses
`router._DOMAIN_WORDS` rather than restating it, so the two cannot drift.

    "mera paisa nahi aaya"   under load -> "paisa" is a PAYMENTS word -> refuse
    "cod pendency clear karo" under load -> "cod" is a COD word       -> refuse
    "order kaise cancel karu" under loss -> "order" is an ORDERS word -> refuse

The test is "foreign AND not in this scope's own vocabulary", because some words legitimately
belong to two queues: `pendency` is a COD word AND a growth-dashboard lever, so under a load
scope "pendency kya hai" must still be answerable. The scope's own vocabulary is what
disambiguates, and it is computed from the nodes rather than maintained by hand.

WHAT IS NOT ALLOWED HERE
Every answer is authored from material that already exists in the corpus, and carries `source`
naming it. Where there is no source, there is no node — the turn falls through to the LLM.

That rule was violated in the first version and the review caught it. Two examples, both of
which would have cost a captain real money:

  · `d_why_marked` said a shortage is marked automatically by the system. The corpus says the
    DESTINATION FACILITY marks it, within SIX HOURS of vehicle arrival, and that missing the six
    hours puts default liability on that facility "without any recourse to CCTV or other
    evidence". A human does it, against a deadline, with consequences — and a captain told "the
    system does it automatically" has no reason to act.
  · `d_what_evidence` said to mail a photo of the shipment. The corpus says valid CCTV footage,
    within 72 hours of notification, submitted through the Kapture tool with a mandatory
    attachment inside an SLA countdown — with default liability falling on the facility when
    evidence is missing. Telling someone to mail a photo instead is not a vague answer; it is
    the answer that loses them the case.

Both came from ONE graph shared across seven dispositions whose mechanisms differ. The graphs
below are per-mechanism, and a disposition with no authored source has NO graph.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from .router import _DOMAIN_WORDS, Ctx, Verdict, normalise

#: A chip label must be short enough to read at a glance on a phone, in simple Hinglish.
MAX_CHIP_CHARS = 34
#: How many chips to offer. Four is the IVR convention — beyond that a menu stops being scanned.
MAX_CHIPS = 4
#: The last slot is RESERVED for the route to a human, always.
#:
#: An exhaustive sweep of chips_for() over every disposition and every `after` found
#: `u_talk_human` in ZERO menus: `_scope` appends UNIVERSAL after all nine GLOSSARY nodes, so it
#: sat at position 11+ and MAX_CHIPS=4 never reached it. On WhatsApp the numbered list IS the
#: interface for someone who cannot reliably spell — so the only route to a human was
#: unreachable for exactly the users who need it most.
#:
#: This is also the IVR convention the module note already cites: every phone menu ends with
#: "press 0 for an operator", and it is the last option precisely so it is always there.
ESCAPE_HATCH = "u_talk_human"
#: A match must STRICTLY beat the runner-up.
#:
#: Margin 1 (strictly better), not 2. Overlap between nodes is deliberate, so a 2-hit margin
#: would decline unambiguous questions. Strictly-better answers those; a genuine TIE falls
#: through to chips, which is the correct outcome for a question that really is ambiguous.
MIN_MARGIN = 1

# Words that carry no signal because they appear in almost every question a captain asks.
#
# THE TRAP, and it is the same one router.py records about SOP trigger keywords (296 of 320 were
# single common words like "me", "has", "date"): "kya" is in "kya hai", "kya karun", "kya hua" —
# every question. The guard is an import-time assertion, not a review habit.
STOPWORDS = frozenset({
    # Hindi/Hinglish function words
    "kya", "hai", "hain", "he", "ho", "hoga", "ka", "ki", "ke", "ko", "kar", "karo", "karna",
    "karun", "karu", "mein", "me", "se", "ye", "yeh", "wo", "woh", "ab", "to", "toh", "bhi",
    "aur", "par", "pe", "na", "nahi", "nhi", "koi", "iska", "isme", "uska", "rahi",
    # Possessives. "mera" prefixes everything a captain owns — mera payment, mera load, mera
    # loss, mera paisa — so on its own it identifies nothing.
    "mera", "mere", "meri", "apna", "apni", "hamara", "aapka", "aapki", "tumhara",
    # Auxiliaries and light verbs. RULE 1: never a topic. "gaya" was a match keyword on
    # u_who_has_it, so "mera id block ho gaya" — a different queue entirely — was answered with
    # "your case has been sent to the owning team and a reference number was issued".
    "gaya", "gayi", "gaye", "hua", "hui", "hue", "aaya", "aayi", "laga", "lagi", "lag",
    "diya", "liya", "kiya", "raha", "tha", "thi", "kuch",
    # Vocatives. RULE 1: a deferential form of address is not a request for a human. "sir" was a
    # match keyword on u_talk_human, so "sir jaldi kuch kijiye" was answered with a handoff.
    "sir", "madam", "mam", "boss", "bhai", "bhaiya", "ji", "saab", "sahab", "bro", "dear",
    # English function words
    "is", "are", "was", "the", "a", "an", "of", "in", "on", "for", "and", "or",
    "i", "it", "this", "that", "do", "does", "did", "be", "will", "can", "my",
})

# Interrogative markers. A GLOSSARY answer is a definition, and a definition is only ever the
# right reply to a question — so a glossary node needs one of these present. RULE 1 also makes
# every one of them frame-only: an interrogative names the question's shape, never its subject.
# A word can be BOTH a stopword and an interrogative, and several are — "kya" most of all. The
# two sets do different jobs and neither implies the other:
#   STOPWORDS      bars a word from any node's match vocabulary (it identifies nothing).
#   INTERROGATIVES lets a word satisfy the glossary gate (it marks the message as a question).
# Dropping "kya" from this set while leaving it in STOPWORDS silently broke five glossary
# lookups — "rto kya hai", "ocf kya hai", "bic kya hai" — because the gate never opened and the
# term's own node was filtered out of scope before it could match.
INTERROGATIVES = frozenset({
    "kya", "kaise", "kese", "kyun", "kyu", "kaun", "kon", "kaunsa", "konsa",
    "kitna", "kitne", "kitni", "kab", "kahan", "kaha", "matlab", "meaning",
    "what", "why", "how", "when", "who", "which", "explain", "samjhao", "batao", "bataiye",
})

#: Interrogatives that ask HOW MUCH rather than WHAT. A definition never answers one of these.
QUANTITY_INTERROGATIVES = frozenset({"kitna", "kitne", "kitni"})

# Imperative markers — the captain is asking for something to be DONE.
#
# FOUND BY THE HARNESS: "cod pendency clear karo" scored one glossary hit on "pendency" and was
# answered with the DEFINITION of Days On Hand. An action request answered with a definition is
# the most patronising failure available here, and for someone with limited literacy it reads as
# the system not understanding them at all — which the auto-close numbers say is already the
# commonest way these conversations die.
# Third parties. If the message names one, the money in question is not the captain's own debit.
#
# FOUND BY THE REVIEW: "customer ka paisa wapas karna hai" — a question about refunding a
# CUSTOMER — matched the phrase (paisa, wapas) and was answered with a recommendation to seek a
# reversal of the CAPTAIN'S loss. Two different people's money, one answer.
THIRD_PARTY = frozenset({
    "customer", "grahak", "gaahak", "buyer", "seller", "supplier", "consignee",
    "receiver", "sender", "shopkeeper", "dukandar",
})

ACTION_WORDS = frozenset({
    "kardo", "kariye", "kijiye", "dijiye", "dedo", "dena", "chahiye", "clear",
    "karwao", "karvao", "solve", "fix", "please", "jaldi", "turant", "abhi",
})


@dataclass(frozen=True)
class FollowUp:
    """One predictable next question, and its authored answer."""

    id: str
    ask: str                        # the chip label the captain taps
    answer: str                     # what they get back. May carry {fact} placeholders.
    source: str                     # where the answer's content comes from. Never empty.
    #: RULE 1. WHAT the question is about. At least one must hit, or the node cannot fire.
    topic: frozenset = frozenset()
    #: Multi-word topics — each entry is a set whose tokens must ALL be present. Lets "rate card"
    #: and "capacity cut" be topics without making the bare words "rate" or "cut" into one.
    phrases: tuple = ()
    #: The question's grammar. Adds to the score; never satisfies the topic requirement.
    frame: frozenset = frozenset()
    then: tuple = ()                # ids of the follow-ups to offer AFTER this one
    #: Placeholders this answer needs. If a fact is missing the node is SKIPPED rather than
    #: rendered with a hole — "your RTO is {rto}%" with no value is worse than not offering it.
    #: Must match the answer's `{}` fields exactly, by import-time assertion.
    needs: tuple = ()
    #: PRECONDITION facts. Not interpolated anywhere — they gate whether the answer is TRUE.
    #:
    #: Separate from `needs` because they are a different job, and merging them broke the
    #: placeholder assertion. `l_how_long` describes what happens after a capacity cut, so it
    #: requires `cut`; nothing in its text quotes the value. Without the split, a precondition
    #: either had to appear in the prose or could not be expressed at all — and the second
    #: option is how "you get 10 days to improve" ended up being offered to a captain whose hub
    #: never had a cut.
    requires: tuple = ()

    @property
    def gates(self) -> tuple:
        """Every fact that must be present: placeholders and preconditions alike."""
        return tuple(self.needs) + tuple(self.requires)

    @property
    def vocabulary(self) -> frozenset:
        """Every token this node knows — for scoring, and for the RULE 2 foreign-word test."""
        out = set(self.topic) | set(self.frame)
        for p in self.phrases:
            out |= set(p)
        return frozenset(out)

    def topic_hit(self, toks: set) -> bool:
        """RULE 1. True only if the message names what this follow-up is ABOUT."""
        return bool(self.topic & toks) or any(set(p) <= toks for p in self.phrases)


# ═══ GLOSSARY — "X kya hai?", askable at any point, scope-independent ════════════════════════
# Sourced term by term. The captain panel's own METRIC_TARGET_TOOLTIPS and the PBCA KT are the
# authorities for the O&P terms; the loss definitions come from the SOP corpus.
#
# Every topic here is the TERM ITSELF (or its spelled-out phrase). The review found `g_ocf`
# matching bare "order" and `g_cps` matching bare "pilot"/"rate", so "order kaise cancel karu"
# got the OCF definition and "pilot id kaise banau" got the rate-card definition. A glossary
# node's subject is its own name and nothing else.
GLOSSARY: tuple[FollowUp, ...] = (
    FollowUp(
        id="g_rto", ask="RTO kya hai?",
        answer=("RTO = Return To Origin — jo shipment customer tak deliver nahi hoti aur wapas "
                "aa jaati hai. Aapka RTO% jitna zyada, load utna kam milta hai — kyunki volume "
                "RTO% aur OCF se decide hota hai. Target aapke area ke best 3PL ke hisaab se "
                "set hota hai."),
        source="kt_ea0d78507c (volume decreases when RTO% is high) + captain panel "
               "METRIC_TARGET_TOOLTIPS (target = best 3PL in your area)",
        topic=frozenset({"rto"}), frame=frozenset({"origin"})),
    FollowUp(
        id="g_ocf", ask="OCF kya hai?",
        answer=("OCF = Order Contribution Factor. Yahi metric decide karta hai kis DC ko kitna "
                "load milega. Ismein takriban 25 factors hote hain, aur unka combination aapke "
                "hub ka volume tay karta hai. Dhyan dijiye — OCF volume decide karta hai, "
                "payment nahi."),
        source="kt_630fddb712 (OCF = order contribution factor, ~25 factors, controls volume) "
               "+ kt_ea0d78507c (OCF determines volume not payment)",
        topic=frozenset({"ocf"}),
        phrases=(frozenset({"order", "contribution"}),),
        frame=frozenset({"factor"})),
    FollowUp(
        id="g_cps", ask="CPS / rate card kya hai?",
        answer=("CPS = Cost Per Shipment, yaani aapka Pilot Rate Card. Aapka target rate card "
                "aas-paas ke DCs ke rate se calculate hota hai — agar aapka rate unse zyada "
                "hai to allocation par asar padta hai."),
        source="captain panel METRIC_TARGET_TOOLTIPS: 'The target rate card is calculated "
               "based on your neighbouring DCs rate'",
        topic=frozenset({"cps", "ratecard"}),
        phrases=(frozenset({"rate", "card"}),),
        frame=frozenset({"pilot"})),
    FollowUp(
        id="g_day0", ask="Day-0 attempt kya hai?",
        answer=("Day-0 Attempt % = jitne shipment aapko us din mile, unmein se kitne aapne "
                "USI din delivery attempt kiye. Ise 70% se upar rakhna zaroori hai — neeche "
                "gaya to capacity cut lag sakta hai."),
        source="captain panel METRIC_TARGET_TOOLTIPS: 'keep your performance above 70% to "
               "avoid capacity cut'",
        topic=frozenset({"day0", "d0"}),
        phrases=(frozenset({"day", "0"}), frozenset({"day", "zero"})),
        frame=frozenset({"attempt", "atmpt"})),
    FollowUp(
        id="g_doh", ask="Pendency / DOH kya hai?",
        answer=("Pendency (DOH = Days On Hand) = shipment aapke hub par kitne din pade rahe. "
                "Ise 2.5 din se kam rakhna hota hai — zyada hua to capacity cut ka risk hai."),
        source="captain panel METRIC_TARGET_TOOLTIPS: 'please clear pendencies within 2.5 "
               "days to avoid capacity cut'",
        # "pendency" is ALSO a COD word (router._DOMAIN_WORDS["cod"]). Keeping it in this node's
        # vocabulary is what stops RULE 2 from refusing "pendency kya hai" under a load scope —
        # see the module note on words that legitimately belong to two queues.
        topic=frozenset({"doh", "pendency"}), frame=frozenset({"pending", "hand"})),
    FollowUp(
        id="g_pbca", ask="Capacity cut kaise lagta hai?",
        answer=("Ise PBCA kehte hain — Performance Based Capacity Action. Aapke hub ki "
                "performance usi pincode ke doosre 3P hubs se compare hoti hai. Performance = "
                "Conversion (FAD + RAD) − ZRTO. Agar aapka hub best 3P hub se peeche hai, to "
                "70% tak load kam ho sakta hai. Uske baad aapko 10 din milte hain sudharne ke "
                "liye."),
        source="kt_lm_pbca (PBCA formula, up to 70% cut, 10 days to improve)",
        topic=frozenset({"pbca"}),
        phrases=(frozenset({"capacity", "cut"}),)),
    FollowUp(
        id="g_bic", ask="BIC kya hai?",
        answer=("BIC = Best In Class. Aapke area ka sabse accha perform karne wala hub. Agar "
                "aapko kam load mil raha hai, wajah yeh ho sakti hai ki aap BIC nahi hain — "
                "targets usi BIC hub ke hisaab se set hote hain."),
        source="kt_040e00381f (BIC stands for best in class; less load because not a BIC)",
        topic=frozenset({"bic"}),
        phrases=(frozenset({"best", "class"}),)),
    FollowUp(
        id="g_hardstop", ask="Hardstop kya hai?",
        answer=("Hardstop loss tab lagta hai jab shipment ek hi hub par **5 din (120 ghante) "
                "se zyada** pada rehta hai aur agle node tak connect nahi hota. System "
                "automatically use hardstop loss mark kar deta hai."),
        source="sopkt_1_hardstop_loss (more than 5 days / 120 hours without being connected; "
               "'The system marks it as hardstop loss')",
        topic=frozenset({"hardstop"}),
        phrases=(frozenset({"hard", "stop"}),)),
    FollowUp(
        id="g_shortage", ask="Shortage loss kya hai?",
        answer=("Shortage loss tab lagta hai jab shipment Node A se bheji gayi lekin Node B "
                "par receive nahi hui. Destination facility ise shortage mark karti hai, aur "
                "dono nodes se CCTV evidence maanga jaata hai — evidence ke aadhaar par loss "
                "kisi ek node par lagta hai."),
        source="sopkt_2_shortage_loss (B marks it as shortage; both nodes asked for evidence "
               "(CCTV footage); loss attributed to one node based on evidence)",
        topic=frozenset({"shortage"}), frame=frozenset({"kami"})),
)

# ═══ PER-MECHANISM GRAPHS ════════════════════════════════════════════════════════════════════
# One graph per LOSS MECHANISM, not one graph for "losses".
#
# The first version shared a single graph across seven dispositions. The review proved that
# makes it state a cause the corpus does not support for five of them — a secondary-QC failure
# is neither a hardstop nor a shortage, and in-transit loss explicitly has NO evidence process
# ("Simpler than shortage — no evidence process", sopkt_3), so offering an evidence answer there
# invents a procedure. Each graph below cites the sources for ITS OWN mechanism.

_LOAD = (
    FollowUp(
        id="l_why_low", ask="Load kam kyun hua?",
        answer=("Aapka load do cheezon se tay hota hai — RTO% aur OCF rate card. Jab in mein "
                "se koi target se peeche hota hai, allocation mein aapko kam orders milte "
                "hain. Jo metric peeche hai wahi wajah hai."),
        source="kt_ea0d78507c + faq_8227 (load is determined by RTO% and the OCF rate card)",
        # "load"/"volume"/"allocation" are the subject. Bare "kyun" is frame only — it was
        # carrying this node, so "debit kyu laga" was answered with load allocation.
        topic=frozenset({"load", "volume", "allocation"}),
        phrases=(frozenset({"kam", "kyun"}), frozenset({"kam", "kyu"}),
                 frozenset({"kam", "why"})),
        frame=frozenset({"kyun", "kyu", "why", "wajah", "kam", "low", "reason"}),
        # `needs=("lever",)` — the closing sentence "jo metric peeche hai wahi wajah hai" asserts
        # that a metric IS behind target, and `_exec_load_planning` stamps `lever` only when one
        # actually is. On hub LZI every lever passes and the engine's own turn-1 reply says so
        # ("every performance metric is meeting its target"), so offering this as chip 1 had the
        # follow-up contradicting the answer it was following up on.
        requires=("lever",),
        then=("l_how_fix", "g_rto", "g_ocf")),
    FollowUp(
        id="l_how_fix", ask="Kaise theek karun?",
        answer=("Jo metric target se peeche hai usi par kaam kijiye. Chaar metric dekhe jaate "
                "hain: Pilot Rate Card (CPS) aas-paas ke DCs se zyada na ho, RTO% target se "
                "neeche rahe, Day-0 attempt 70% se upar rahe, aur pendency 2.5 din se kam "
                "rahe. Aakhri do capacity cut se bachne ke liye zaroori hain."),
        # Now names all FOUR levers. It named three and omitted Pilot Rate Card (CPS) — which
        # `growth/contract.py:LEVERS` lists FIRST and which `_exec_load_planning` most often
        # stamps as the failing one, so the remedy list omitted the very lever just diagnosed.
        # The old closing line "yeh sudhrenge to allocation apne aap badhega" is gone too: no
        # source promises automatic recovery. METRIC_TARGET_TOOLTIPS only says these avoid a
        # capacity cut, and kt_lm_pbca makes recovery conditional and 10 days away.
        source="captain panel METRIC_TARGET_TOOLTIPS (all four levers and their thresholds) "
               "+ growth/contract.py LEVERS (the four, in panel order)",
        # "theek"/"thik"/"tik" are GONE. They are the canonical Hinglish acknowledgement
        # ("theek hai", "thik h") and greetings.CLOSERS carries them for exactly that reason —
        # so a captain saying "ok, fine" under a load scope was handed the four-lever remedy
        # list. Worse, because this tier is registered BEFORE greetings, it also bypassed
        # greetings.py's documented consent guard, which exists so a bare "haan" after the
        # engine asks "should I escalate?" is not swallowed as a pleasantry.
        #
        # "theek karun" (fix it) is a real phrasing, so it survives as a phrase — where the verb
        # is present and the acknowledgement reading is impossible.
        topic=frozenset({"sudhar", "sudharu", "improve", "badhau", "badhaun", "behtar"}),
        # The discriminator is the INTERROGATIVE, not the verb: "kaise theek karun" carries
        # "kaise" and "theek hai" does not. ("karun"/"karu" would be the natural discriminator
        # but they are stopwords — "kya karun", "kaise karun" — and the import-time guard
        # correctly refuses them.)
        phrases=(frozenset({"kaise", "theek"}), frozenset({"kaise", "thik"}),
                 frozenset({"kese", "theek"}), frozenset({"kese", "thik"})),
        frame=frozenset({"kaise", "kese", "how"}),
        then=("l_how_long", "g_pbca")),
    FollowUp(
        id="l_how_long", ask="Kitne din lagenge?",
        answer=("Capacity cut lagne ke baad aapko **10 din** milte hain performance sudharne "
                "ke liye. Us window ke baad hi capacity recovery hoti hai — aur agar aap best "
                "3P hub se accha perform karte hain to 80% tak load badh sakta hai."),
        source="kt_lm_pbca (10 days to improve; up to 80% increase, only after 10 days)",
        # No bare topic: "how long" is pure frame, so it is a PHRASE requirement. Bare "kab"
        # was carrying u_how_long_team and answering load questions with the SLA table.
        phrases=(frozenset({"kitne", "din"}), frozenset({"kitna", "din"}),
                 frozenset({"kitna", "time"}), frozenset({"kab", "tak"}),
                 frozenset({"how", "long"})),
        frame=frozenset({"lagenge", "lagega", "din", "time", "kab"}),
        # `needs=("cut",)` — this answer is entirely about what happens AFTER a capacity cut, and
        # `_exec_load_planning` stamps `cut` only when capacity_loss is the dominant stage. On an
        # allocation-miss hub (LZ5: 545 orders missed in allocation, no cut) or one meeting every
        # target (LZI: is_good=True, all four levers passing), PBCA's clock has never started —
        # so "you get 10 days to improve" describes a process that is not happening to this
        # captain, and the chip is not offered at all.
        requires=("cut",),
        then=("l_will_increase",)),
    FollowUp(
        id="l_will_increase", ask="Phir load badhega?",
        answer=("Haan — agar aap 10 din mein best 3P hub se behtar perform karte hain, to "
                "capacity recovery lagti hai aur 80% tak load badh sakta hai. Iske alawa jaise "
                "aapke polygon mein demand badhegi, volume bhi badhega."),
        source="kt_lm_pbca (capacity recovery up to 80% after 10 days) + scn_OP_1/scn_OP_2 "
               "(as demand increases in your polygon, load may increase)",
        # "wapas" and "milega" are GONE. Together they scored 2 on "paisa wapas milega" — a
        # money question — and this answer opens with "Haan" (yes), so a captain asking whether
        # they get their money back was told "yes", followed by capacity talk.
        # "badhega" is GONE as a bare topic. This answer opens with "Haan" (yes), so "mera rate
        # badhega?" — a question about the captain's own per-shipment PAY rate — got an
        # affirmative followed by load-volume talk. It now needs the subject named.
        phrases=(frozenset({"load", "badhega"}), frozenset({"volume", "badhega"}),
                 frozenset({"load", "badhegi"}), frozenset({"load", "increase"}),
                 frozenset({"capacity", "recovery"})),
        frame=frozenset({"phir", "load", "volume", "badhega", "badhegi", "increase",
                         "recover", "recovery"}),
        # Same gate: "capacity recovery" and the 80% figure are post-cut mechanics.
        requires=("cut",)),
    FollowUp(
        id="l_my_numbers", ask="Mera number kya hai?",
        answer=("Aapka {lever} abhi **{current}** hai, aur target **{target}** hai. Yahi metric "
                "target se peeche hai — isi par kaam karna hai."),
        source="_exec_load_planning evidence trail — the failing lever, its value and its "
               "target, read from growth-dashboard and never re-derived",
        # "number" is GONE as a bare topic. In Hinglish "mera number" overwhelmingly means the
        # PHONE number, so "mera phone number update karo" and "gaadi ka number kya hai" were
        # answered with the growth-dashboard rate card. The chip label still reads "Mera number
        # kya hai?" and the TAP path is unaffected — only free text needs the tighter gate.
        # Free text keeps only the UNAMBIGUOUS words. "number" is frame-only, so "mera number
        # kya hai" now declines to the LLM rather than being answered with the rate card — the
        # cost of that is one phrasing, and the chip labelled "Mera number kya hai?" still
        # answers it in one tap, which is the primary interface for this user group anyway.
        # Being strict on free text and generous with taps is the design.
        topic=frozenset({"metric", "aankda"}),
        phrases=(frozenset({"number", "target"}), frozenset({"score", "target"})),
        frame=frozenset({"value", "figure", "number", "score", "target"}),
        needs=("lever", "current", "target"),
        then=("l_how_fix",)),
    FollowUp(
        id="l_money_lost", ask="Kitna nuksan hua?",
        answer=("Is cycle mein jo orders miss hue, unki value takriban **₹{loss}** hai. Aapko "
                "{orders} orders mile, mil sakte the {max_potential}."),
        source="order-summary extra_earnings_loss + current_orders/max_potential — server-side "
               "figures, shown on the captain's own dashboard",
        # "paisa", "loss", "earning" and "kamai" are GONE. Each was a single unique hit inside
        # the load scope, so "mera paisa nahi aaya", "mera loss reverse karo", "paisa kitna kata
        # hai" and "meri earning kitni hai" were ALL answered with the load cycle's rupee
        # figure — a confident, quantified answer from the wrong queue. Only "nuksan"/"ghata"
        # (the shortfall itself) remain, and RULE 2 now refuses the payment words outright.
        # `nuksan` is GONE as a bare topic, and this is the subtlest hole the review found:
        # `nuksan` is in router._DOMAIN_WORDS["losses"] AND was this node's topic, so RULE 2
        # subtracted it as "in this scope's own vocabulary" and RULE 1 was then satisfied by it.
        # THE TWO RULES CANCELLED EACH OTHER OUT for that one word — so "mera nuksan wapas karo"
        # ("give my loss back") and "mera nuksan kaun bharega" ("who will compensate me") were
        # answered with the missed-order rupee figure from the LOAD queue, while the English
        # "mera loss reverse karo" was correctly refused. Same question, opposite outcome by
        # language.
        #
        # As a PHRASE it needs the quantity interrogative, which is what distinguishes "how much
        # did I lose" (this node) from "give it back" (a debit dispute, and a different queue).
        phrases=(frozenset({"kitna", "nuksan"}), frozenset({"kitni", "nuksan"}),
                 frozenset({"kitna", "ghata"})),
        frame=frozenset({"kitna", "rupee", "nuksan", "ghata"}),
        needs=("loss", "orders", "max_potential"),
        then=("l_how_fix", "l_how_long")),
    FollowUp(
        id="l_who_decides", ask="Target kaun decide karta hai?",
        answer=("Target aapke area ke sabse acche 3PL hub (BIC) ke performance se set hota "
                "hai — RTO ka target best 3PL se, rate card ka target aas-paas ke DCs ke rate "
                "se. Yeh manually kisi ne aapke liye set nahi kiya."),
        source="captain panel METRIC_TARGET_TOOLTIPS + kt_lm_pbca (compared against the best "
               "3P hub in the same pincode)",
        topic=frozenset({"target"}),
        frame=frozenset({"kaun", "kon", "who", "decide", "set", "kisne"}),
        then=("g_bic",)),
)

# ── hardstop: the ONE loss mechanism the corpus says is genuinely automatic ─────────────────
_HARDSTOP = (
    FollowUp(
        id="h_why", ask="Hardstop kyun laga?",
        answer=("Hardstop tab lagta hai jab shipment agle node tak time par connect nahi hota. "
                "Dhyan dijiye — **loss D5 par eligible hota hai, lekin connect karne ka SLA 48 "
                "ghante hai** (FM Forward/RTO, FMSC aur LMSC Forward/RTO, aur LM RTO ke liye). "
                "Sirf LM Forward mein 5 din milte hain deliver ya RTO karne ke liye. Breach D3 "
                "par hota hai, loss D5 par, aur LOST D6 par mark hota hai."),
        # The previous version said "connect within 5 days" and called that "the only way" to
        # prevent it. D5 is the LOSS-MARKING day, not the SLA — and prescribing it as the target
        # is the actionable half being wrong: `kt_lm_sla_hardstop_matrix` gives 48 HOURS for FM
        # Forward/RTO, FMSC & LMSC Forward/RTO, and LM RTO (the RVP connection a DC captain
        # actually performs), with 5 days only for LM Forward delivery. A captain working to a
        # 5-day deadline on a 48-hour leg has already breached on D3, and is loss-eligible by
        # the time they think they still have two days left.
        source="kt_lm_sla_hardstop_matrix (48 hrs to connect for FM Forward/RTO, FMSC & LMSC "
               "Forward/RTO and LM RTO; 5 days for LM Forward; breach D3, loss-eligible D5, "
               "marked LOST D6) + sopkt_1_hardstop_loss (the D5 loss-marking rule)",
        # NO bare topic. `g_hardstop` (the definition) and this node (the cause) would otherwise
        # both match the bare term and tie at 1-1, so "hardstop kya hai" declined into chips.
        # "X kya hai" and "X kyun laga" are different questions; the term alone does not
        # distinguish them, so the CAUSE framing is required here and the definition keeps the
        # bare term. A captain who types only "kyun laga" gets chips — which for this user group
        # is the primary interface anyway, and being strict on free text while being generous
        # with taps is the whole design.
        phrases=(frozenset({"hardstop", "kyun"}), frozenset({"hardstop", "kyu"}),
                 frozenset({"hardstop", "why"}),
                 frozenset({"loss", "kyun"}), frozenset({"loss", "kyu"}),
                 frozenset({"debit", "kyun"}), frozenset({"debit", "kyu"})),
        frame=frozenset({"kyun", "kyu", "why", "wajah", "loss", "debit", "hardstop"}),
        then=("h_can_reverse", "g_hardstop")),
    FollowUp(
        id="h_can_reverse", ask="Paisa wapas milega?",
        answer=("Agar record mein reversal ka signal hai — jaise facility in-scan ho gaya ho, "
                "ya attribution badal gayi ho — to main aapka case Losses & Debits (L2) team "
                "ko reversal ke liye bhejne ki **sifarish** karta hoon. Main khud paisa wapas "
                "nahi kar sakta, aur settlement ki confirmation bhi wahi team degi."),
        # Rewritten. It previously said "main ise ... bhej deta hoon" and "case bhej diya gaya
        # hai" — present and past tense, asserting a handoff that had happened. It had not:
        # tier() returns action="respond" and _log_info_concern writes
        # outcome="resolved_in_conversation", so no escalated concern exists and nothing reaches
        # l3.inbox(). Worse, router._refusals blocks this whole tier when prev_action ==
        # "escalate", so the sentence was reachable ONLY on turns where nothing was escalated —
        # it was false in every case where it could fire.
        source="engine/write_mode.py (there is no write path; a favourable decision is a "
               "recommendation to L2, never a payment) + tools.py _DOMAIN_TEAM",
        topic=frozenset({"reversal", "reverse", "refund"}),
        phrases=(frozenset({"paisa", "wapas"}), frozenset({"paise", "wapas"}),
                 frozenset({"money", "back"})),
        frame=frozenset({"paisa", "wapas", "milega"})),
)

# ── shortage: marked by a HUMAN, against a deadline, with CCTV evidence ─────────────────────
_SHORTAGE = (
    FollowUp(
        id="s_why", ask="Shortage kyun laga?",
        answer=("Shortage tab lagta hai jab shipment Node A se bheji gayi lekin Node B par "
                "receive nahi hui. **Destination facility** ise system mein shortage mark "
                "karti hai — aur yeh vehicle aane ke **6 ghante ke andar** karna hota hai. "
                "6 ghante ke baad mark hua to default liability usi destination facility par "
                "aa jaati hai, aur CCTV ya kisi aur evidence ka mauka nahi milta."),
        # This is the answer the first version got WRONG. It said "System yeh automatically mark
        # karta hai, koi manually nahi karta" — the opposite of what the corpus says. A human at
        # the destination marks it, inside six hours, and missing that window forfeits the
        # evidence process entirely. The six hours are the single most actionable fact here, and
        # a captain told "the system does it automatically" has no reason to act on them.
        source="sopkt_2_shortage_loss ('B marks it as shortage') + kt_lm_shortage_marking_6hr "
               "(within SIX HOURS of the vehicle arrival timestamp; otherwise default liability "
               "on the Destination Facility without recourse to CCTV or other evidence)",
        # No bare "shortage" topic — see the note on h_why. `g_shortage` owns the definition;
        # this node owns the cause, and only the cause framing reaches it.
        phrases=(frozenset({"shortage", "kyun"}), frozenset({"shortage", "kyu"}),
                 frozenset({"shortage", "why"}),
                 frozenset({"loss", "kyun"}), frozenset({"loss", "kyu"}),
                 frozenset({"debit", "kyun"}), frozenset({"debit", "kyu"})),
        frame=frozenset({"kyun", "kyu", "why", "wajah", "loss", "debit", "shortage"}),
        then=("s_evidence", "s_can_reverse")),
    FollowUp(
        id="s_evidence", ask="Kya evidence chahiye?",
        answer=("Shortage mein evidence **CCTV footage** hota hai. Agar shortage mark hone ke "
                "5 din tak resolve nahi hota, to origin aur destination dono ko notice jaata "
                "hai aur **72 ghante ke andar** valid CCTV dena hota hai. Footage ek hi dock "
                "camera se, continuous, aur **2 ghante se zyada nahi**. Ticket Kapture "
                "**self-serve portal** par raise kijiye (selfserveapp.kapturecrm.com) — "
                "registered email se login, phir OTP → Raise a Ticket → Hub Code, issue "
                "description, aur template download karke upload kijiye. Lost shipment ka "
                "callout **loss marking ke 7 din ke andar** karna hota hai. Evidence na dene "
                "par default liability aap par aa sakti hai."),
        # Also wrong before: it said "evidence mail karke", and listed "AWB number, shipment ki
        # photo ya video, aur jis din bheja tha uska record" — none of which appears in any
        # source. Following that advice would miss the 72-hour CCTV window and the mandatory
        # Kapture attachment, and default liability falls on the facility when evidence is
        # missing. This is the finding that would have cost a captain real money.
        # The Kapture screen was WRONG. `kt_lm_mm_kapture_shortage_evidence` describes the
        # **Mid-Mile Sort-Centre** tool — "Tickets → Assigned to Me → Dispose Ticket" — which a
        # DC captain has no credentials for. The captain-facing route is the self-serve portal
        # (`kt_lm_kapture_selfserve`), and `kt_lm_lost_shipment_ticket_7days` supplies the 7-day
        # callout window the MM description does not mention. Sending a captain to a console they
        # cannot log into, inside an evidence deadline, loses the case.
        source="kt_lm_shortage_liability_cctv (5 days → notice; valid CCTV within 72 hours; "
               "single dock camera, continuous, max 2 hours; default liability when evidence "
               "is missing) + kt_lm_kapture_selfserve (the CAPTAIN-facing portal: "
               "selfserveapp.kapturecrm.com, registered email → OTP) + "
               "kt_lm_lost_shipment_ticket_7days (raise within 7 days of loss marking)",
        topic=frozenset({"evidence", "proof", "cctv", "sabut", "dastavez"}),
        frame=frozenset({"footage", "camera"}),
        then=("s_can_reverse",)),
    FollowUp(
        id="s_can_reverse", ask="Paisa wapas milega?",
        answer=("Evidence ke aadhaar par loss kisi ek node par lagta hai — agar aapka CCTV "
                "valid hai aur doosre node ka nahi, to liability unki banti hai. Main aapka "
                "case Losses & Debits (L2) team ko reversal ke liye bhejne ki **sifarish** kar "
                "sakta hoon; paisa main khud wapas nahi kar sakta aur confirmation wahi team "
                "degi."),
        source="sopkt_2_shortage_loss (loss attributed to one node based on evidence) + "
               "kt_lm_shortage_liability_cctv (default-liability rules) + "
               "engine/write_mode.py (no write path exists)",
        topic=frozenset({"reversal", "reverse", "refund"}),
        phrases=(frozenset({"paisa", "wapas"}), frozenset({"paise", "wapas"}),
                 frozenset({"money", "back"})),
        frame=frozenset({"paisa", "wapas", "milega"})),
)

# ── in-transit: the corpus is explicit that there is NO evidence process ────────────────────
_INTRANSIT = (
    FollowUp(
        id="i_why", ask="Ye loss kyun laga?",
        answer=("In-transit loss tab lagta hai jab trip/vehicle origin se nikal gayi lekin "
                "**5 din** ke andar destination par receive nahi hui. Origin vendor ko notice "
                "jaata hai aur **72 ghante ke andar** teen cheezein deni hoti hain: Pre-Alert "
                "email copy, SC outbound desk security se signed/stamped Delivery Challan, aur "
                "valid CCTV. Destination ko departure ke 5 din ke andar pre-alert email par "
                "revert karna hota hai. Proof adhoora nikla to origin ko 24 ghante extra "
                "milte hain — uske baad shipment lost maan kar debit ho jaata hai."),
        # THE CORPUS CONTRADICTS ITSELF HERE, and the earlier version of this node picked the
        # wrong side. `sopkt_3_in_transit_loss` is a one-line summary saying "Simpler than
        # shortage — no evidence process". `kt_lm_intransit_pendency` is the operational
        # procedure and documents a full one: a 5-day trigger, a 72-hour window, three named
        # artefacts, a +24-hour grace, and "else shipments are deemed lost and debited".
        #
        # The operational chunk governs, because the summary's claim is the kind that costs money
        # if believed: a captain told there is no evidence process does not send the Pre-Alert
        # copy, the Delivery Challan or the CCTV — and the shipments are then deemed lost and
        # debited to them. The conflict is recorded here rather than silently resolved.
        source="kt_lm_intransit_pendency (5-day flag; origin must furnish Pre-Alert email copy "
               "+ signed/stamped Delivery Challan + valid CCTV within 72 hours; +24h if "
               "incomplete, else deemed lost and debited) — PREFERRED OVER "
               "sopkt_3_in_transit_loss, whose one-line 'no evidence process' contradicts it",
        # "transit" is GONE as a bare topic — it is the everyday word for a shipment that is
        # still MOVING, so "transit me kitna time lagta hai" (an ETA question) was answered with
        # the in-transit LOSS mechanism.
        topic=frozenset({"intransit"}),
        phrases=(frozenset({"transit", "loss"}), frozenset({"transit", "kho"}),
                 frozenset({"loss", "kyun"}), frozenset({"loss", "kyu"}),
                 frozenset({"debit", "kyun"}), frozenset({"debit", "kyu"})),
        frame=frozenset({"kyun", "kyu", "why", "wajah", "loss", "debit", "transit"}),
        then=("i_evidence", "i_can_reverse")),
    FollowUp(
        id="i_evidence", ask="Kya evidence chahiye?",
        answer=("In-transit mein origin vendor ko **72 ghante ke andar** teen cheezein deni "
                "hoti hain: (1) Pre-Alert email ki copy, (2) Delivery Challan jo SC outbound "
                "desk security se signed aur stamped ho, (3) valid CCTV. Agar origin valid "
                "departure proof de aur destination revert na kare, to liability destination par "
                "jaati hai. Police custody, sales-tax detention ya accident jaise external "
                "factors pre-alert thread par supporting docs ke saath report karein — 10 din "
                "tak extra mil sakte hain."),
        # This node exists because the operational chunk documents a process the summary chunk
        # denies. Omitting it — as the previous version did, on the summary's authority — left a
        # captain with a 72-hour deadline and no idea it existed.
        source="kt_lm_intransit_pendency (the three artefacts, the 72-hour window, the "
               "destination-revert rule, and the up-to-10-extra-days external-factor clause)",
        topic=frozenset({"evidence", "proof", "cctv", "sabut", "dastavez", "challan"}),
        frame=frozenset({"footage", "prealert", "camera"}),
        then=("i_can_reverse",)),
    FollowUp(
        id="i_can_reverse", ask="Paisa wapas milega?",
        answer=("Agar record mein attribution badalne ka signal hai, to main aapka case Losses "
                "& Debits (L2) team ko reversal ke liye bhejne ki **sifarish** karta hoon. "
                "Paisa main khud wapas nahi kar sakta — confirmation wahi team degi."),
        source="engine/write_mode.py (no write path exists) + tools.py _DOMAIN_TEAM",
        topic=frozenset({"reversal", "reverse", "refund"}),
        phrases=(frozenset({"paisa", "wapas"}), frozenset({"paise", "wapas"}),
                 frozenset({"money", "back"})),
        frame=frozenset({"paisa", "wapas", "milega"})),
)

# ── secondary QC: its own mechanism, its own sources ────────────────────────────────────────
_QC = (
    FollowUp(
        id="q_why", ask="QC fail kyun hua?",
        answer=("Secondary QC har return shipment par DC pe hoti hai — AWB scan, QR/packet ID "
                "scan, 3 photo (Side, Back, Front), aur FE ke category/design jawab ka milaan. "
                "Mismatch hua to system 'QC Failed' dikhata hai, aur woh shipment LMSC tak "
                "connect nahi hota. **DC ki secondary QC fail par filhaal hubs par koi debit "
                "nahi lagta.** Debit alag case mein aata hai — jab QC fail **FM location** par "
                "RTO journey ke dauraan mark hota hai, ya Wrong RVP pick hota hai; tab Meesho "
                "Central QC team ke decision par shipment value LM Pilot/Captain par debit ho "
                "sakti hai."),
        # THE TWO SOURCES DESCRIBE DIFFERENT SITUATIONS and the previous version merged them,
        # telling a captain their DC secondary-QC failure had been debited to them.
        # `kt_lm_secondary_qc_dc` says the opposite in its own last line: "for now no debit is
        # applied to hubs for those shipments". The debit clause in `kt_lm_wrong_rvp_debits`
        # applies to Wrong RVP picks and to Secondary QC Fail "marked at FM locations during the
        # RTO journey" — not to the DC check this disposition is about. Telling someone they owe
        # money they do not owe is the same class of error as telling them they have been paid.
        source="kt_lm_secondary_qc_dc (the scan/3-image process; mismatch → 'QC Failed'; "
               "prevented from connecting to the LMSC; 'for now NO DEBIT is applied to hubs for "
               "those shipments') + kt_lm_wrong_rvp_debits (the debit case: Wrong RVP picked, "
               "and Secondary QC Fail marked at FM locations during the RTO journey)",
        topic=frozenset({"qc"}),
        phrases=(frozenset({"fail", "kyun"}), frozenset({"fail", "kyu"})),
        frame=frozenset({"fail", "kyun", "kyu", "why", "wajah", "reject"}),
        then=("q_process",)),
    FollowUp(
        id="q_process", ask="QC process kya hai?",
        answer=("DC par: AWB scan → QR/packet ID scan → QC window khulega → 3 photo lijiye "
                "(Side, Back, Front) → FE ke category/design jawab verify kijiye → match hua "
                "to Approve, warna Reject + Next → Finish. Success par print label lijiye. "
                "Dhyan rakhiye — secondary-QC-failed shipment LMSC tak connect nahi hote."),
        source="kt_lm_secondary_qc_dc (full process in its own step order; secondary-QC-failed "
               "shipments are prevented from connecting to the LMSC)",
        # "process"/"tarika" are GONE as bare topics: under a QC scope they claimed EVERY "what
        # is the process" question, so "appeal ka process kya hai" and "dispute ka process kya
        # hai" — asking how to CONTEST the debit — were answered with the operational DC
        # scanning SOP, which is the opposite of what was asked.
        phrases=(frozenset({"qc", "process"}), frozenset({"qc", "kaise"}),
                 frozenset({"qc", "tarika"}), frozenset({"qc", "tareeka"}),
                 frozenset({"qc", "steps"})),
        frame=frozenset({"kaise", "how", "qc", "process", "tarika", "tareeka", "steps"})),
)

# ═══ UNIVERSAL — askable after ANY disposition ════════════════════════════════════════════════
# ONE node, deliberately.
#
# `u_who_has_it` ("your case went to team X, quote the reference number in your reply") and
# `u_how_long_team` ("the team's TAT is 24 hours, you'll get an update in that window") were
# both DELETED rather than fixed, because they cannot be made true in this tier:
#
#   · Tier F returns action="respond", so `_log_info_concern` records
#     outcome="resolved_in_conversation" — no escalated concern exists, nothing reaches
#     l3.inbox(), and no reference number was ever issued.
#   · And `router._refusals` blocks this entire tier whenever prev_action == "escalate". So the
#     one state in which "your case is with a team" WOULD be true is the exact state in which
#     this tier is not allowed to answer. They were false in every reachable case.
#   · `u_who_has_it` also named only 4 of the 7 teams in its own cited source, so for a
#     secondary-QC case — owned by Quality / QC (L2) — every team it listed was the wrong one.
#
# The TAT question is a good question. It belongs to a tier that fires AFTER an escalation and
# can read the concern's real team and reference — not to this one. Left unbuilt rather than
# answered wrongly.
UNIVERSAL = (
    FollowUp(
        id="u_talk_human", ask="Insaan se baat karni hai",
        answer=("Bilkul. Aap yahin likh dijiye ki kya dikkat hai — main poori detail ke saath "
                "team tak pahuncha doonga taaki koi insaan isse dekhe."),
        # Reworded to the future tense. It previously said "Main aapka case seedha team ke paas
        # bhej deta hoon" — present tense, asserting a handoff it does not perform: the verdict
        # carries action="respond", nothing is escalated, and the concern's outcome is
        # "resolved_in_conversation". Asking for the detail and promising to forward it is true,
        # and the next turn's LLM path can actually call escalate_case.
        source="tools.escalate_case is reachable on the LLM path — this tier asks for the "
               "detail rather than claiming to have already forwarded anything",
        topic=frozenset({"insaan", "human", "aadmi", "banda", "agent"}),
        phrases=(frozenset({"baat", "karni"}), frozenset({"baat", "karwao"}),
                 frozenset({"baat", "karau"})),
        frame=frozenset({"call", "phone", "baat"})),
)

#: disposition -> its follow-up graph. Keyed on the disposition the ENGINE decided.
#:
#: `debit_revoked` and `capacity_panel_issue` are deliberately ABSENT. A revoked debit has no
#: authored follow-up in the corpus, and mapping it onto the shortage graph — as the first
#: version did — told a captain whose debit was already reversed that a destination facility had
#: marked a shortage against them.
#:
#: PRECISELY WHAT "no graph" MEANS, because the earlier wording here overclaimed: an ungraphed
#: disposition gets NO MECHANISM ANSWERS — no cause, no evidence, no reversal recommendation.
#: `_scope` still appends GLOSSARY and UNIVERSAL, so "RTO kya hai?" and the route to a human
#: remain available, which is deliberate: those are true regardless of disposition, and refusing
#: a captain a human because their disposition has no authored graph would be the worst possible
#: reading of "be careful". It does NOT mean the turn always goes to the LLM.
GRAPHS: dict[str, tuple[FollowUp, ...]] = {
    "load_planning": _LOAD,
    "hardstop_loss": _HARDSTOP,
    "shortage_loss": _SHORTAGE,
    "bag_shortage": _SHORTAGE,
    "shipment_shortage": _SHORTAGE,
    "intransit_loss": _INTRANSIT,
    "secondary_qc_fail": _QC,
}

#: The queue each graphed disposition belongs to, for the RULE 2 foreign-word test. Keys are
#: `router._DOMAIN_WORDS` names so the two tables cannot drift.
SCOPE_DOMAIN: dict[str, str] = {
    "load_planning": "orders",
    "hardstop_loss": "losses",
    "shortage_loss": "losses",
    "bag_shortage": "losses",
    "shipment_shortage": "losses",
    "intransit_loss": "losses",
    "secondary_qc_fail": "losses",
}

_ALL_NODES = GLOSSARY + UNIVERSAL + _LOAD + _HARDSTOP + _SHORTAGE + _INTRANSIT + _QC
_BY_ID = {f.id: f for f in _ALL_NODES}


def _scope(disposition: str | None) -> tuple[FollowUp, ...]:
    """The follow-ups live right now: this disposition's graph, plus glossary and universal.

    Glossary is always in scope because "RTO kya hai?" is a reasonable question at any point,
    and refusing it because the conversation was about something else would be pedantic.
    """
    return GRAPHS.get((disposition or "").strip(), ()) + GLOSSARY + UNIVERSAL


def _scope_vocabulary(scope: tuple[FollowUp, ...]) -> frozenset:
    """Every word the live scope knows. Decides what counts as FOREIGN — see RULE 2."""
    out: set = set()
    for f in scope:
        out |= f.vocabulary
    return frozenset(out)


def foreign_domains(toks: set, disposition: str | None,
                    scope: tuple[FollowUp, ...]) -> dict:
    """RULE 2. Queues named by this message that this scope does not serve. {domain: words}.

    A word only counts as foreign if it is NOT in the live scope's own vocabulary, because some
    words genuinely belong to two queues — `pendency` is a COD word AND a growth-dashboard
    lever, so "pendency kya hai" under a load scope must still be answerable.
    """
    own_domain = SCOPE_DOMAIN.get((disposition or "").strip())
    known = _scope_vocabulary(scope)
    out: dict = {}
    for domain, words in _DOMAIN_WORDS.items():
        if domain == own_domain:
            continue
        hits = (toks & words) - known
        if hits:
            out[domain] = sorted(hits)
    return out


def _renderable(f: FollowUp, facts: dict) -> bool:
    """True only if every placeholder AND every precondition fact is present.

    A missing placeholder would render a hole; a missing precondition would render a sentence
    that is false. Both are withheld, and the second is the more dangerous of the two because it
    reads perfectly well.
    """
    return all(k in facts and facts[k] not in (None, "") for k in f.gates)


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
        if f.id == ESCAPE_HATCH:
            continue                      # placed last, unconditionally — see ESCAPE_HATCH
        if len(f.ask) > MAX_CHIP_CHARS:
            continue
        seen.add(f.id)
        out.append({"id": f.id, "label": f.ask})
        if len(out) >= MAX_CHIPS - 1:
            break
    hatch = _BY_ID.get(ESCAPE_HATCH)
    if hatch is not None:
        out.append({"id": hatch.id, "label": hatch.ask})
    return out


def resolve(message: str, disposition: str | None, *, facts: dict | None = None,
            selected: str | None = None) -> tuple[FollowUp | None, str]:
    """Match a message (or a tapped chip) to a follow-up in scope. (node, why).

    A TAP is exact — `selected` is a node id and needs no matching, which is the entire reason
    chips exist for this user group. Free text goes through RULE 2 (a foreign queue refuses) and
    RULE 1 (a topic must hit), then a hit count with a margin inside the surviving scope.
    """
    scope = _scope(disposition)
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
        if node.id not in {f.id for f in scope}:
            return None, f"option {selected!r} is out of scope for {disposition!r}"
        return node, "tapped"

    toks = set(normalise(message).split())
    if not toks:
        return None, "empty"

    # ── RULE 2, first: a foreign queue is not a follow-up at all ─────────────────────
    foreign = foreign_domains(toks, disposition, scope)
    if foreign:
        named = ", ".join(f"{d}({'/'.join(w)})" for d, w in sorted(foreign.items()))
        return None, f"foreign queue named: {named} — a new concern, not a follow-up"

    # RULE 2b — a third party's money is a different question entirely.
    tp = toks & THIRD_PARTY
    if tp:
        return None, f"names a third party ({'/'.join(sorted(tp))}) — not the captain's own case"

    # A definition needs a question, and specifically a DEFINITION question.
    #
    # Filtered out of the scope BEFORE scoring rather than rejected after, so a blocked glossary
    # node cannot tie with — and thereby suppress — a legitimate follow-up that would have
    # answered the turn.
    #
    # QUANTITY interrogatives are excluded too: "kitni" asks for a VALUE, "kya" asks for a
    # meaning. "meri pendency kitni hai" was answered with the DEFINITION of Days On Hand —
    # a captain asking for their own number was handed a vocabulary entry instead, which is the
    # same patronising failure as the "cod pendency clear karo" case and reads, to someone with
    # limited literacy, as the system not understanding them.
    asks_definition = bool(toks & (INTERROGATIVES - QUANTITY_INTERROGATIVES))
    if not asks_definition or (toks & ACTION_WORDS):
        gloss = {f.id for f in GLOSSARY}
        scope = tuple(f for f in scope if f.id not in gloss)

    # ── RULE 1: only nodes whose SUBJECT was named may compete ──────────────────────
    eligible = [f for f in scope if f.topic_hit(toks)]
    if not eligible:
        return None, "no node's topic was named (frame words alone never match)"

    scored = sorted(((len(toks & f.vocabulary), f) for f in eligible), key=lambda kv: -kv[0])
    best_n, best = scored[0]
    runner = scored[1][0] if len(scored) > 1 else 0
    if best_n - runner < MIN_MARGIN:
        # Ambiguous — two follow-ups fit equally well. ASK rather than pick: a wrong guess costs
        # the captain a tap, a wrong ANSWER costs them a wrong action.
        return None, f"ambiguous: {best_n} vs {runner} — offer chips instead"
    return best, f"{best_n} hit(s) incl. topic, margin {best_n - runner}"


# ── channel degradation: the same menu, without buttons ──────────────────────────────────────
# WhatsApp is 81.6% of tickets and `channels/whatsapp.py: send(phone, text)` is TEXT ONLY — no
# interactive buttons, and wiring Meta's interactive-message API needs a Business account this
# environment does not have. So the chip row degrades to a numbered list, which is the IVR form
# of the same idea and needs nothing from the transport.

def as_numbered_text(reply: str, chips: list[dict]) -> str:
    """Append the offered options as a numbered list. For any transport with no buttons."""
    if not chips:
        return reply
    lines = [f"{n}. {c['label']}" for n, c in enumerate(chips, 1)]
    return reply + "\n\n" + "\n".join(lines) + "\n\n(Number likh dijiye, ya seedha poochh lijiye.)"


#: Combining marks used to build a keycap emoji.
#:
#: THE TRAP the review found: "2️⃣" is THREE codepoints — "2", U+FE0F (variation selector) and
#: U+20E3 (combining enclosing keycap). `normalise` KEEPS U+FE0F, because it is category Mn and
#: Mn is in `router._KEEP_CATEGORIES` — which exists so Devanagari matras survive — while U+20E3
#: is dropped. The result is "2️", and `.isdigit()` on that is False. So a captain who taps
#: the single most obvious reply to a numbered menu was silently dropped through to the LLM.
_KEYCAP_MARKS = frozenset({"️", "⃣", "︎"})


def _as_int(text: str) -> int | None:
    """A number in any form a captain might send it, or None if it is not purely one.

    `unicodedata.digit` rather than `str.isdigit`, so Devanagari (२) and full-width (２) digits
    work as well as ASCII — this user group types in more than one script.
    """
    s = normalise("".join(ch for ch in (text or "") if ch not in _KEYCAP_MARKS)).strip()
    s = "".join(ch for ch in s if ch not in _KEYCAP_MARKS).strip()
    if not s or " " in s:
        return None
    try:
        digits = [unicodedata.digit(ch) for ch in s]
    except (TypeError, ValueError):
        return None
    n = 0
    for d in digits:
        n = n * 10 + d
    return n


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
    n = _as_int(message)
    if n is None or not 1 <= n <= len(offered):
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
        # NO QUESTION MARK. `greetings.tier` derives `prev_asked_question` from
        # `_last_model_text(...).endswith("?")`, and its consent guard then declines every
        # closer and affirmation — so one follow-up answer ending in "?" disabled the greeting
        # tier's pleasantry path for the REST of the conversation, sending every subsequent
        # "ok thanks" to a full model call at the measured ₹4.00–5.29. The guard is right (a
        # bare "haan" after "should I escalate?" is consent, not a pleasantry); this reply just
        # should not have been claiming to ask anything.
        reply=reply + ("\n\nKuch aur poochna ho to bataiye." if chips else ""),
        because=f"{node.id} in scope '{disp}' — {why}",
        action="respond",
        options=chips,
        data={"node": node.id, "scope": disp, "source": node.source, "why": why},
    )


# ── import-time structural guards ───────────────────────────────────────────────────────────
# These run once, at import, and fail loudly. Each encodes a defect that was actually found —
# most of them by the adversarial review — so each is a regression test that cannot be skipped.
def _selfcheck() -> None:
    import string

    ids = [f.id for f in _ALL_NODES]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate follow-up ids: {dupes}"
    gloss_ids = {g.id for g in GLOSSARY}
    for f in _BY_ID.values():
        # An answer with no provenance is the failure mode this whole file is built to avoid:
        # a confident wrong process fact, acted on by someone with no way to check it.
        assert f.source, f"{f.id} has no source"
        assert f.ask and len(f.ask) <= MAX_CHIP_CHARS, f"{f.id}: chip label too long"
        # RULE 1: a node with no topic and no phrase can only ever be carried by frame words,
        # which is exactly the defect class the review found eight instances of.
        assert f.topic or f.phrases, f"{f.id} has no topic — frame words would carry it alone"
        bad = f.vocabulary & STOPWORDS
        assert not bad, f"{f.id} matches on stopword(s) {bad} — see the STOPWORDS note"
        # An interrogative or an action word may sit in `frame` (always additive) but never in
        # `topic`, which would make the question's grammar into its subject.
        assert not (f.topic & (INTERROGATIVES | ACTION_WORDS)), \
            f"{f.id}: interrogative/action word used as a topic"
        for p in f.phrases:
            assert len(p) >= 2, f"{f.id}: a phrase needs 2+ tokens, got {set(p)}"
            assert not set(p) <= INTERROGATIVES, \
                f"{f.id}: phrase {set(p)} is all interrogatives — a frame, not a topic"
        # A GLOSSARY node must not score on an interrogative: it already REQUIRES one to be in
        # scope, so counting it again would double-spend the same evidence.
        if f.id in gloss_ids:
            assert not (f.vocabulary & INTERROGATIVES), \
                f"glossary {f.id} scores on an interrogative it already required"
        for child in f.then:
            assert child in _BY_ID, f"{f.id} points at unknown follow-up {child!r}"
        # Every placeholder in the answer must be declared in `needs`, and vice versa —
        # otherwise `.format()` raises KeyError mid-turn, or a declared fact silently does
        # nothing and the node is withheld for no reason.
        holes = {n for _t, n, _s, _c in string.Formatter().parse(f.answer) if n}
        assert holes == set(f.needs), f"{f.id}: answer holes {holes} != needs {set(f.needs)}"
        # A precondition must NOT also be a placeholder: it is checked, never interpolated, and
        # listing it twice would make the two fields silently redundant.
        assert not (set(f.requires) & set(f.needs)), \
            f"{f.id}: {set(f.requires) & set(f.needs)} is both a placeholder and a precondition"
    for disp, graph in GRAPHS.items():
        assert graph, f"disposition {disp!r} maps to an empty graph"
        # RULE 2 needs a domain for every graphed disposition, or the foreign-word test treats
        # EVERY domain as foreign and the scope can never answer anything.
        assert disp in SCOPE_DOMAIN, f"{disp!r} has a graph but no SCOPE_DOMAIN entry"
    for disp, dom in SCOPE_DOMAIN.items():
        assert dom in _DOMAIN_WORDS, f"SCOPE_DOMAIN[{disp!r}] = {dom!r} is not a router domain"


_selfcheck()
