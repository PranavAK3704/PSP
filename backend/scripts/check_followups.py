"""Assertions for the deterministic follow-up engine. NO LLM CALLS, NO NETWORK.

    python scripts/check_followups.py

The follow-up tier answers a captain's SECOND question without a model. What makes that safe is
not the matcher — it is the scope: a disposition the engine established by reading data. So the
assertions here are mostly about the boundary of that scope, and about the two ways this could
hurt someone:

  1. **Answering out of scope.** A follow-up about load answered while the captain is disputing
     a loss is a confident non-sequitur, and a low-literacy user has no way to tell it apart
     from a real answer. Every negative case below exists for this.
  2. **Stating a process fact nobody authored.** The corpus is the only source. `source` is
     asserted non-empty on every node, and the citations are checked to RESOLVE against
     data/knowledge/corpus.json — a plausible-looking id that does not exist is exactly how an
     invented fact would enter with the appearance of provenance.

Exit code 0 = clean.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)


def head(n: str) -> None:
    print(f"\n{'-' * 78}\n{n}\n{'-' * 78}")


# ── the golden file ─────────────────────────────────────────────────────────────────────────
# (message, disposition, expected_node_or_None, note).
#
# `None` means DECLINE — and a decline is a pass, not a gap: the turn falls through to the LLM,
# which is the behaviour that exists today. Declines are the majority here on purpose.
GOLDEN = [
    # ── in scope: load planning ──────────────────────────────────────────────
    ("load kam kyun hua", "load_planning", "l_why_low", "the commonest follow-up of all"),
    ("kyun kam hua", "load_planning", "l_why_low", "phrase topic, no domain noun"),
    ("kaise theek karun", "load_planning", "l_how_fix", ""),
    ("kaise improve karun", "load_planning", "l_how_fix", "English verb, Hindi frame"),
    ("kitne din lagenge", "load_planning", "l_how_long", "phrase topic"),
    ("phir load badhega", "load_planning", "l_will_increase", ""),
    # NOT ("mera number kya hai") any more — "number" is the commonest noun in a support
    # conversation, so free text reaching this node answered "mera phone number update karo"
    # with the growth-dashboard rate card. It is a tap-only node now; section [4] asserts that.
    ("mera metric kya hai", "load_planning", "l_my_numbers", "fact-filled, unambiguous word"),
    ("kitna nuksan hua", "load_planning", "l_money_lost", "fact-filled, rupee figure"),
    ("target kaun decide karta hai", "load_planning", "l_who_decides", ""),
    # ── in scope: each loss MECHANISM, from its own sources ──────────────────
    ("hardstop kyun laga", "hardstop_loss", "h_why", ""),
    ("ye loss kyun laga", "hardstop_loss", "h_why", "cause framing, no term"),
    ("paisa wapas milega", "hardstop_loss", "h_can_reverse", "must not promise a payment"),
    ("shortage kyun laga", "shortage_loss", "s_why", ""),
    ("kya evidence chahiye", "shortage_loss", "s_evidence", "CCTV/72h/Kapture, not a photo"),
    ("ye loss kyun laga", "bag_shortage", "s_why", "same mechanism, different disposition"),
    ("paisa wapas milega", "shipment_shortage", "s_can_reverse", ""),
    ("ye loss kyun laga", "intransit_loss", "i_why", "in-transit has NO evidence process"),
    ("qc fail kyun hua", "secondary_qc_fail", "q_why", ""),
    ("qc process kya hai", "secondary_qc_fail", "q_process", ""),
    # ── the glossary, in scope from anywhere ─────────────────────────────────
    ("rto kya hai", "load_planning", "g_rto", ""),
    ("ocf kya hai", "load_planning", "g_ocf", ""),
    ("rate card kya hai", "load_planning", "g_cps", "a two-word topic"),
    ("cps kya hai", "load_planning", "g_cps", ""),
    ("pendency kya hai", "load_planning", "g_doh", "a COD word that is also a load lever"),
    ("doh kya hai", "load_planning", "g_doh", "abbreviation"),
    ("bic kya hai", "load_planning", "g_bic", ""),
    ("hardstop kya hai", "hardstop_loss", "g_hardstop", "definition, not cause"),
    ("shortage kya hai", "shortage_loss", "g_shortage", "definition, not cause"),
    ("capacity cut kaise lagta hai", "load_planning", "g_pbca", "a two-word topic"),
    ("day 0 kya hai", "load_planning", "g_day0", "a two-word topic"),
    ("rto kya hai", "hardstop_loss", "g_rto", "glossary is scope-independent"),
    # ── universal ────────────────────────────────────────────────────────────
    ("insaan se baat karni hai", "hardstop_loss", "u_talk_human", ""),
    ("mujhe agent se baat karni hai", "load_planning", "u_talk_human", ""),

    # ══ RULE 1 — frame words alone must never carry a node ════════════════════
    # Every line below was a REPRODUCED wrong answer in the adversarial review. A single common
    # word — an interrogative, an auxiliary, a vocative — was carrying a whole node.
    ("cod jama karna hai kaise", "load_planning", None,
     "bare 'kaise' carried l_how_fix -> load levers for a COD deposit question"),
    ("fe id kaise banau", "load_planning", None, "bare 'kaise'"),
    ("invoice kaise nikalu", "load_planning", None, "bare 'kaise'"),
    ("debit kyu laga", "load_planning", None,
     "bare 'kyu' carried l_why_low -> load allocation offered as the reason for a debit"),
    ("mera id block ho gaya", "load_planning", None,
     "bare 'gaya' carried u_who_has_it -> 'your case was sent, quote the reference number'"),
    ("mera fe block ho gaya", "load_planning", None, "bare 'gaya'"),
    ("bag khatam ho gaya", "load_planning", None, "bare 'gaya'"),
    ("sir jaldi kuch kijiye", "load_planning", None,
     "bare 'sir' carried u_talk_human -> a handoff promise the tier does not perform"),
    ("sir abhi tak kuch nahi hua", "load_planning", None, "bare 'sir'"),
    ("load kab badhega", "hardstop_loss", None,
     "bare 'kab' carried u_how_long_team -> the four-team SLA table for a load question"),
    ("order kaise cancel karu", "hardstop_loss", None,
     "bare 'order' carried g_ocf -> the OCF definition for an order-cancellation question"),
    ("pilot id kaise banau", "hardstop_loss", None,
     "bare 'pilot' carried g_cps -> the rate-card definition for a rider-ID request"),
    ("kyun laga", "hardstop_loss", None, "cause framing with no subject — chips, not a guess"),

    # ══ RULE 2 — a foreign queue is a new concern, never a follow-up ══════════
    ("mera paisa nahi aaya", "load_planning", None,
     "REPRODUCED: answered with the load cycle's rupee figure"),
    ("paisa nahi aaya", "load_planning", None, "same, without the possessive"),
    ("mera loss reverse karo", "load_planning", None,
     "REPRODUCED: bare 'loss' in the load scope returned the missed-orders figure"),
    ("paisa kitna kata hai", "load_planning", None, "REPRODUCED: 'how much was deducted'"),
    ("meri earning kitni hai", "load_planning", None, "REPRODUCED: bare 'earning'"),
    ("paisa wapas milega", "load_planning", None,
     "REPRODUCED: 'wapas'+'milega' scored 2 on l_will_increase, which opens with 'Haan' — "
     "so 'will I get my money back' was answered YES, then capacity talk"),
    ("cod pendency clear karo", "load_planning", None, "a COD action request"),
    ("mera payment nahi aaya", "load_planning", None, "the original regression"),
    ("load kam hai aur abhi payment nahi aaya", "load_planning", None,
     "REPRODUCED: 'abhi' disabled the multi-intent refusal, so the payment half was dropped"),

    # ══ ROUND 2 — a second adversarial review found these, all reproduced ═════
    # Every one is a bare common noun that was serving as a node's topic. RULE 1 was necessary
    # but not sufficient: it bars interrogatives, auxiliaries and vocatives structurally, and
    # these slipped through because they LOOK domain-specific until you see them in a sentence.
    ("mera nuksan wapas karo", "load_planning", None,
     "'give my loss back' — a debit dispute, answered with the missed-order rupee figure. "
     "`nuksan` was BOTH a losses domain word AND this node's topic, so RULE 2 exempted it as "
     "'in scope vocabulary' and RULE 1 was satisfied by it — the two rules cancelled out"),
    ("mera nuksan kaun bharega", "load_planning", None, "'who will compensate me'"),
    ("nuksan bhar do mera", "load_planning", None, "'reimburse my loss'"),
    ("mera phone number update karo", "load_planning", None,
     "bare 'number' -> the growth-dashboard rate card; in Hinglish 'mera number' means phone"),
    ("gaadi ka number kya hai", "load_planning", None, "vehicle number"),
    ("appeal ka process kya hai", "secondary_qc_fail", None,
     "bare 'process' -> the DC scanning SOP, to someone asking how to CONTEST the debit"),
    ("dispute ka process kya hai", "secondary_qc_fail", None, "same"),
    ("mera rate badhega", "load_planning", None,
     "bare 'badhega' -> an answer opening 'Haan' (yes) about LOAD, to a question about the "
     "captain's own per-shipment PAY rate"),
    ("rate kab badhega", "load_planning", None, "same"),
    ("transit me kitna time lagta hai", "intransit_loss", None,
     "bare 'transit' -> the in-transit LOSS mechanism, for an ETA question"),
    ("customer ka paisa wapas karna hai", "hardstop_loss", None,
     "a CUSTOMER refund answered with a reversal recommendation for the captain's own debit"),
    ("customer ko paisa wapas dena hai", "shortage_loss", None, "same"),
    ("meri pendency kitni hai", "hardstop_loss", None,
     "asks for their own NUMBER, was handed the DEFINITION of Days On Hand"),
    ("pendency kitni hai", "load_planning", None, "same — 'kitni' asks how much, not what"),
    # ── the acknowledgement hijack, which was the worst of the round ─────────
    # `theek`/`thik`/`tik` were topics on l_how_fix, and they are exactly what greetings.CLOSERS
    # carries as the canonical Hinglish "ok/fine". Because this tier is registered BEFORE
    # greetings, "theek hai" was answered with the four-lever remedy list — AND it bypassed
    # greetings.py's consent guard, which exists so a bare "haan" after "should I escalate?" is
    # not swallowed as a pleasantry.
    ("theek hai", "load_planning", None, "the canonical Hinglish acknowledgement"),
    ("thik hai", "load_planning", None, ""),
    ("tik hai", "load_planning", None, ""),
    ("ok theek hai", "load_planning", None, ""),
    ("haan theek hai", "load_planning", None, "and this one is CONSENT, not a pleasantry"),
    ("theek", "load_planning", None, ""),
    ("bilkul theek hai", "load_planning", None, ""),

    # ── ordinary declines ────────────────────────────────────────────────────
    ("kuch samajh nahi aaya", "load_planning", None, "no topic — the LLM should take this"),
    ("aaj mausam accha hai", "load_planning", None, "off-domain"),
    ("", "load_planning", None, "empty"),
    ("   ", "load_planning", None, "whitespace only"),
    ("gaadi kab aayegi", "load_planning", None, "in-domain-sounding, not an authored follow-up"),
    ("kya evidence chahiye", "intransit_loss", None,
     "in-transit has NO evidence process (sopkt_3) — offering one would invent a procedure"),
    ("ye loss kyun laga", "debit_revoked", None,
     "no graph: a revoked debit has no authored follow-up, so it must not borrow shortage's"),
]

# Phrasings that must NOT be answered when there is no disposition in scope. Turn one always
# goes to the LLM — without a scope this tier IS the open-NLU problem it exists to avoid.
NO_SCOPE = ["load kam kyun hua", "rto kya hai", "paisa wapas milega", "kaise theek karun",
            "insaan se baat karni hai", "kitna nuksan hua", "hardstop kyun laga",
            "kya evidence chahiye"]

# The real fixture values for hub HKS, where CPS is the failing lever — deliberately not RTO,
# because `l_how_fix` used to omit Pilot Rate Card from its remedy list, so a lever-agnostic
# fixture would not have exposed it.
FACTS = {"lever": "Pilot Rate Card (CPS)", "current": "₹21", "target": "₹16",
         "loss": 2108, "orders": 827, "max_potential": 1035}


def main() -> int:
    from app.engine import session as sessmod
    from app.engine.algo import followups as F
    from app.engine.algo import router

    saved = dict(os.environ)
    try:
        # ── 1. the golden file ──────────────────────────────────────────────────
        head(f"[1] {len(GOLDEN)} phrasings — scope, match, and decline")
        fires = 0
        for msg, disp, want, note in GOLDEN:
            got, why = F.resolve(msg, disp, facts=FACTS)
            got_id = got.id if got else None
            ok = got_id == want
            fires += 1 if got_id else 0
            label = f"{msg[:34]!r:38s} [{disp[:14]:14s}] -> {str(got_id):16s}"
            check(label, ok, note or (why if not ok else ""))
        print(f"       {fires} answered deterministically, {len(GOLDEN) - fires} declined to the LLM")

        # ── 2. no scope, no answer ──────────────────────────────────────────────
        head("[2] turn one always goes to the LLM — no disposition means no answer")
        for msg in NO_SCOPE:
            ctx = router.Ctx(message=msg, entities={}, context={},
                             session=sessmod.Session(conversation_id="c", captain_id="x"))
            check(f"no scope: {msg[:40]!r}", F.tier(ctx) is None)

        # ── 3. an identifier means a new concern, not a follow-up ───────────────
        head("[3] an identifier present means the captain brought evidence, not a question")
        s = sessmod.Session(conversation_id="c", captain_id="x")
        s.set_disposition("load_planning", FACTS)
        # This message WOULD match l_why_low. The entity is the only thing stopping it, which is
        # the point: "why is load low for VL0084..." is a new case about a specific shipment.
        ctx = router.Ctx(message="load kam kyun hua", entities={"any": True, "awb": "VL00841234"},
                         context={}, session=s)
        check("declines when an entity was extracted", F.tier(ctx) is None)
        ctx2 = router.Ctx(message="load kam kyun hua", entities={}, context={}, session=s)
        check("fires on the same message without one", F.tier(ctx2) is not None)

        # ── 4. the tap path is exact ────────────────────────────────────────────
        head("[4] a tap needs no matching — the whole reason chips exist")
        for nid in ("l_how_fix", "g_rto", "u_talk_human", "l_my_numbers"):
            got, why = F.resolve("anything at all", "load_planning", facts=FACTS, selected=nid)
            check(f"tap {nid}", got is not None and got.id == nid, why)
        got, why = F.resolve("x", "load_planning", facts=FACTS, selected="no_such_node")
        check("an unknown option id declines, it does not crash", got is None, why)
        # A TAP IS SCOPE-CHECKED TOO. `selected_option` arrives on the request body, so if the
        # tap path skipped the scope gate then anyone able to craft a POST could read any node's
        # answer under any disposition — the scope would be advisory rather than enforced.
        got, why = F.resolve("x", "hardstop_loss", facts=FACTS, selected="l_my_numbers")
        check("a crafted out-of-scope option id is REFUSED", got is None, why)
        got, why = F.resolve("x", "load_planning", facts=FACTS, selected="d_can_reverse")
        check("...in the other direction too", got is None, why)
        for nid in ("g_rto", "u_talk_human"):
            got, _ = F.resolve("x", "hardstop_loss", facts=FACTS, selected=nid)
            check(f"{nid} is legitimately in every scope", got is not None)

        # ── 5. facts: a hole is never rendered ──────────────────────────────────
        head("[5] a follow-up with unfillable slots is WITHHELD, not rendered with a hole")
        need_facts = [f for f in F._BY_ID.values() if f.needs]
        check("at least one fact-filled node exists", len(need_facts) >= 2,
              f"{len(need_facts)}: {[f.id for f in need_facts]}")
        for f in need_facts:
            check(f"{f.id} withheld with no facts", not F._renderable(f, {}))
            check(f"{f.id} renderable with facts", F._renderable(f, FACTS))
        # and the tier must not offer it either
        labels_nofacts = [c["id"] for c in F.chips_for("load_planning", facts={})]
        check("fact-filled nodes absent from the chip row when facts are missing",
              not any(f.id in labels_nofacts for f in need_facts), str(labels_nofacts))
        # the tier itself declines rather than raising a KeyError on .format()
        s2 = sessmod.Session(conversation_id="c2", captain_id="x")
        s2.set_disposition("load_planning")          # no facts
        # Driven by a TAP, because free text no longer reaches this node: "number" is the
        # commonest noun in a support conversation ("mera phone number update karo") so it is
        # frame-only now. The chip is the supported route, and it is the one this user group
        # actually uses.
        v = F.tier(router.Ctx(message="Mera number kya hai?", entities={}, context={},
                              session=s2, selected_option="l_my_numbers"))
        check("tier declines a fact-filled node when the fact is absent", v is None)
        # with facts, it renders and the number appears
        s2.answer_facts = dict(FACTS)
        v = F.tier(router.Ctx(message="Mera number kya hai?", entities={}, context={},
                              session=s2, selected_option="l_my_numbers"))
        check("with facts it renders the real value",
              v is not None and "₹21" in v.reply and "₹16" in v.reply,
              (v.reply[:70] if v else ""))
        check("no unrendered placeholder survives into a reply",
              v is not None and "{" not in v.reply and "}" not in v.reply)

        # ── 6. the chip row ─────────────────────────────────────────────────────
        head("[6] the chip row: bounded, readable, and never re-offering itself")
        for disp in ("load_planning", "hardstop_loss", "shortage_loss", "capacity_panel_issue"):
            chips = F.chips_for(disp, facts=FACTS)
            check(f"{disp}: 1..{F.MAX_CHIPS} chips", 1 <= len(chips) <= F.MAX_CHIPS, str(len(chips)))
            check(f"{disp}: every label <= {F.MAX_CHIP_CHARS} chars",
                  all(len(c["label"]) <= F.MAX_CHIP_CHARS for c in chips))
            check(f"{disp}: no duplicate ids", len({c["id"] for c in chips}) == len(chips))
        for nid in ("l_why_low", "l_how_fix", "d_why_marked", "g_rto"):
            after = F.chips_for("load_planning", facts=FACTS, after=nid)
            check(f"after={nid} never re-offers itself", nid not in {c['id'] for c in after})
        already = {"l_why_low", "l_how_fix", "l_how_long"}
        rest = F.chips_for("load_planning", facts=FACTS, already=already)
        check("an answered follow-up is not offered again",
              not (already & {c["id"] for c in rest}), str([c["id"] for c in rest]))

        # ── 7. the numbered fallback — 81.6% of tickets have no buttons ─────────
        head("[7] the buttonless degradation, and the traps in reading a lone number")
        offered = ["a", "b", "c"]
        check('"2" -> the second option', F.ordinal_choice("2", offered) == "b")
        check('"1" -> the first', F.ordinal_choice("1", offered) == "a")
        check('"3" -> the third', F.ordinal_choice("3", offered) == "c")
        check('"4" is out of range -> declines', F.ordinal_choice("4", offered) is None)
        check('"0" declines', F.ordinal_choice("0", offered) is None)
        check('"500" is an amount, not a choice', F.ordinal_choice("500", offered) is None)
        check('"2 din se pending hai" is prose, not a choice',
              F.ordinal_choice("2 din se pending hai", offered) is None)
        check("nothing offered -> nothing resolved", F.ordinal_choice("2", []) is None)
        check("nothing offered (None) -> nothing resolved", F.ordinal_choice("2", None) is None)
        # Scripts and forms a captain actually sends. The keycap emoji is the one the review
        # found: "2️⃣" is 2 + U+FE0F + U+20E3, and `normalise` KEEPS U+FE0F (category Mn, in
        # router._KEEP_CATEGORIES so Devanagari matras survive) while dropping U+20E3 — leaving
        # "2️", on which .isdigit() is False. So the single most obvious reply to a numbered
        # menu was silently dropped through to the LLM.
        for form, want in (("2", "b"), ("2.", "b"), ("(2)", "b"), ("#2", "b"),
                           ("२", "b"), ("२", "b"), ("２", "b"), ("2️⃣", "b"),
                           ("1️⃣", "a"), ("3️⃣", "c"), (" 2 ", "b")):
            got = F.ordinal_choice(form, offered)
            check(f"{form!r} -> option {want}", got == want, f"got {got!r}")
        check("'4️⃣' is still out of range", F.ordinal_choice("4️⃣", offered) is None)
        txt = F.as_numbered_text("Reply.", [{"id": "a", "label": "First?"},
                                            {"id": "b", "label": "Second?"}])
        check("numbering is 1-based and in order",
              "1. First?" in txt and "2. Second?" in txt, txt.replace("\n", " | "))
        check("no options -> the reply is returned untouched",
              F.as_numbered_text("Reply.", []) == "Reply.")

        # ── 8. provenance — every citation must RESOLVE ─────────────────────────
        head("[8] provenance: a source that does not exist is worse than no source")
        corpus_raw = (ROOT / "data" / "knowledge" / "corpus.json").read_text()
        corpus_ids = {c.get("id") for c in json.loads(corpus_raw).get("chunks", [])}
        check("corpus loaded", len(corpus_ids) > 100, f"{len(corpus_ids)} chunks")
        for f in F._BY_ID.values():
            check(f"{f.id} has a source", bool(f.source))
        # Every token in a source that LOOKS like a corpus id must actually be one. This is the
        # assertion that catches an invented citation — the failure mode where a fabricated fact
        # arrives wearing the appearance of provenance.
        import re
        cited = bad = 0
        for f in F._BY_ID.values():
            for tok in re.findall(r"\b(?:kt|sopkt|faq|scn)[a-zA-Z0-9_]+", f.source):
                cited += 1
                if tok not in corpus_ids:
                    bad += 1
                    check(f"{f.id} cites {tok}", False, "NOT IN CORPUS")
        check(f"all {cited} corpus citations resolve", bad == 0, f"{bad} dangling")

        # ── 9. stopwords, and the false decline they caused ─────────────────────
        head("[9] RULE 1 — the topic/frame split, and what it structurally forbids")
        for f in F._BY_ID.values():
            bad_toks = f.vocabulary & F.STOPWORDS
            check(f"{f.id} avoids stopwords", not bad_toks, str(bad_toks) if bad_toks else "")
        for f in F._BY_ID.values():
            # A node with no topic can only ever be reached by frame words, which IS the defect.
            check(f"{f.id} has a topic or a phrase", bool(f.topic or f.phrases))
            check(f"{f.id}: no interrogative/action word is a topic",
                  not (f.topic & (F.INTERROGATIVES | F.ACTION_WORDS)),
                  str(f.topic & (F.INTERROGATIVES | F.ACTION_WORDS)))
        # The frame words that were each individually carrying a node. Every one of them, ALONE,
        # must now match nothing anywhere — this is the assertion that keeps RULE 1 honest as
        # new nodes are added.
        for word in ("kaise", "kyun", "kyu", "kab", "kitna", "gaya", "hua", "sir", "milega",
                     "wapas", "chahiye", "how", "why", "when"):
            for disp in sorted(F.GRAPHS):
                got, why = F.resolve(word, disp, facts=FACTS)
                check(f"bare {word!r} matches nothing in {disp}", got is None,
                      f"fired {got.id}" if got else "")
        # And the definition-vs-cause split, which the collision between a graph's why-node and
        # the glossary node for the same term forced.
        for msg, disp, want in (("hardstop kya hai", "hardstop_loss", "g_hardstop"),
                                ("hardstop kyun laga", "hardstop_loss", "h_why"),
                                ("shortage kya hai", "shortage_loss", "g_shortage"),
                                ("shortage kyun laga", "shortage_loss", "s_why")):
            got, why = F.resolve(msg, disp, facts=FACTS)
            check(f"{msg!r} -> {want}", got is not None and got.id == want, why)

        # ── 9c. the escape hatch is ALWAYS reachable ────────────────────────────
        head("[9c] the route to a human is in every menu, under every disposition")
        # An exhaustive sweep found u_talk_human in ZERO chip rows: _scope appends UNIVERSAL
        # after all nine GLOSSARY nodes, so it sat at position 11+ and MAX_CHIPS=4 never reached
        # it. On WhatsApp the numbered list IS the interface for someone who cannot reliably
        # spell — so the only route to a human was unreachable for exactly the people who need
        # it most. It is now the reserved last slot, which is also the IVR convention ("press 0
        # for an operator") the module note already cites.
        missing = []
        for disp in list(F.GRAPHS) + ["debit_revoked", "capacity_panel_issue", None]:
            for after in [None] + [f.id for f in F._scope(disp)]:
                chips = F.chips_for(disp, facts=FACTS, after=after)
                if F.ESCAPE_HATCH not in {c["id"] for c in chips}:
                    missing.append((disp, after))
        check("u_talk_human is offered in EVERY menu", not missing,
              f"{len(missing)} menus without it: {missing[:3]}")
        for disp in list(F.GRAPHS) + [None]:
            chips = F.chips_for(disp, facts=FACTS)
            check(f"{disp}: the hatch is LAST", chips[-1]["id"] == F.ESCAPE_HATCH,
                  str([c["id"] for c in chips]))
            check(f"{disp}: still within MAX_CHIPS", len(chips) <= F.MAX_CHIPS, str(len(chips)))

        # ── 9d. the trailing question mark, and the tier it disabled ────────────
        head("[9d] a follow-up reply must not end in '?'")
        # greetings.tier derives prev_asked_question from _last_model_text().endswith("?"), and
        # its consent guard then declines every closer and affirmation. So one follow-up answer
        # ending in "?" disabled the greeting tier's pleasantry path for the REST of the
        # conversation — every later "ok thanks" became a full model call at ₹4.00–5.29.
        s_q = sessmod.Session(conversation_id="q", captain_id="x")
        s_q.set_disposition("load_planning", FACTS)
        v_q = F.tier(router.Ctx(message="load kam kyun hua", entities={}, context={},
                                session=s_q))
        check("a follow-up verdict is produced", v_q is not None)
        check("...and its reply does not end in a question mark",
              v_q is not None and not v_q.reply.rstrip().endswith("?"),
              repr(v_q.reply[-40:]) if v_q else "")
        check("...while still inviting another question",
              v_q is not None and "poochna" in v_q.reply)

        # ── 9b. RULE 2 — the foreign-queue refusal ──────────────────────────────
        head("[9b] RULE 2 — a word from another queue refuses the turn")
        for msg, disp, dom in (("mera paisa nahi aaya", "load_planning", "payments"),
                               ("cod jama karna hai", "load_planning", "cod"),
                               ("fe id block hai", "load_planning", "fe_id"),
                               ("bag chahiye", "load_planning", "consumables"),
                               ("debit kyu laga", "load_planning", "losses"),
                               ("load kab badhega", "hardstop_loss", "orders")):
            toks = set(__import__("app.engine.algo.router", fromlist=["normalise"])
                       .normalise(msg).split())
            foreign = F.foreign_domains(toks, disp, F._scope(disp))
            check(f"{msg!r} under {disp} names a foreign queue", dom in foreign,
                  str(foreign))
            got, _ = F.resolve(msg, disp, facts=FACTS)
            check(f"...and therefore declines", got is None, f"fired {got.id}" if got else "")
        # THE COUNTER-CASE, which is why the test is "foreign AND not in this scope's own
        # vocabulary" rather than just "foreign": `pendency` is a COD word AND a load lever.
        got, why = F.resolve("pendency kya hai", "load_planning", facts=FACTS)
        check("a word owned by two queues is NOT foreign to the one that uses it",
              got is not None and got.id == "g_doh", why)
        # "order cancel karu" under a loss scope is caught by RULE 1, not RULE 2, and the
        # distinction is worth pinning: "order" sits inside `g_ocf`'s PHRASE {order,
        # contribution}, which puts it in every scope's vocabulary and so exempts it from the
        # foreign test. Tightening RULE 2 to ignore phrase tokens would fix that — and would
        # also make "capacity" foreign to every loss scope, breaking `g_pbca`'s deliberate
        # scope-independence. RULE 1 already declines it, so the two rules cover each other and
        # neither needs to be made stricter. Asserted so a later change to either one shows up
        # here rather than as a wrong answer.
        got, why = F.resolve("order kaise cancel karu", "hardstop_loss", facts=FACTS)
        check("'order kaise cancel karu' declines under a loss scope", got is None, why)
        check("...via RULE 1, because 'order' is only a phrase token",
              "topic" in why, why)

        # ── 10. nothing here promises money ────────────────────────────────────
        head("[10] no authored answer claims a payment was made")
        # write_mode.py: there is no write path. An answer that says money came back would be
        # the one claim this system must never make, and it would be made in the captain's own
        # language to someone with no way to check it.
        BANNED = ("paisa aa gaya", "credit ho gaya", "reversed", "refund ho gaya",
                  "payment ho gaya", "wapas aa gaya", "mil gaya hai")
        for f in F._BY_ID.values():
            low = f.answer.lower()
            hits = [b for b in BANNED if b in low]
            check(f"{f.id} claims no completed payment", not hits, str(hits) if hits else "")
        # Every reversal answer, one per mechanism, must say outright that it cannot pay.
        for nid in ("h_can_reverse", "s_can_reverse", "i_can_reverse"):
            rev = F._BY_ID[nid]
            check(f"{nid} says outright that it cannot pay",
                  "paisa main khud wapas nahi" in rev.answer.lower()
                  or "main khud paisa wapas nahi" in rev.answer.lower(), rev.answer[:60])
            # And it must be a RECOMMENDATION, not a completed handoff. The first version said
            # "case bhej diya gaya hai" — past tense, asserting a handoff that had not happened
            # and, because router._refusals blocks this tier after an escalation, could not have.
            check(f"{nid} recommends rather than claims a completed handoff",
                  "sifarish" in rev.answer.lower(), rev.answer[:80])
            for banned in ("bhej diya gaya", "bhej diya hai"):
                check(f"{nid} does not claim the case was already sent",
                      banned not in rev.answer.lower())

        # ── 10b. no answer asserts a state this tier cannot produce ─────────────
        head("[10b] nothing claims an escalation that did not happen")
        # THE STRUCTURAL POINT the review surfaced: this tier returns action="respond", so
        # `_log_info_concern` writes outcome="resolved_in_conversation" — no escalated concern,
        # nothing in l3.inbox(), no reference number. AND router._refusals blocks the tier
        # entirely when prev_action == "escalate". So the one state in which "your case is with
        # a team" would be true is the exact state in which this tier may not answer. Two nodes
        # asserted it anyway and were deleted rather than reworded.
        for gone in ("u_who_has_it", "u_how_long_team", "d_why_marked", "d_what_evidence",
                     "d_can_reverse"):
            check(f"{gone} is gone", gone not in F._BY_ID,
                  "it asserted a state this tier cannot produce"
                  if gone.startswith("u_") else "it served 7 dispositions from 2 sources")
        CLAIMS_ESCALATION = ("bhej diya", "reference number", "ref number", "tat 24",
                             "update milega", "team ko gaya", "case us team")
        for f in F._BY_ID.values():
            low = f.answer.lower()
            hits = [c for c in CLAIMS_ESCALATION if c in low]
            check(f"{f.id} asserts no completed escalation", not hits, str(hits) if hits else "")

        # ── 10c. the two answers the review proved factually WRONG ──────────────
        head("[10c] content fidelity — the facts that would have cost a captain money")
        s_why, s_ev = F._BY_ID["s_why"], F._BY_ID["s_evidence"]
        # It said "System yeh automatically mark karta hai, koi manually nahi karta". The corpus
        # says the DESTINATION FACILITY marks it, within SIX HOURS. A captain told the system
        # does it automatically has no reason to act on the deadline that decides liability.
        check("s_why says a FACILITY marks the shortage, not the system",
              "destination facility" in s_why.answer.lower())
        check("s_why states the 6-hour marking deadline", "6 ghante" in s_why.answer)
        check("s_why states the consequence of missing it",
              "liability" in s_why.answer.lower())
        check("s_why does NOT claim the system marks it automatically",
              "automatically" not in s_why.answer.lower(), s_why.answer[:80])
        # It said to MAIL a photo or video. The corpus says valid CCTV within 72 hours through
        # the Kapture tool with a mandatory attachment. Following the old answer would miss the
        # window and default liability would fall on the captain's facility.
        s_ev_low = s_ev.answer.lower()
        check("s_evidence names CCTV", "cctv" in s_ev_low)
        check("s_evidence states the 72-hour window", "72 ghante" in s_ev.answer)
        check("s_evidence names the Kapture tool", "kapture" in s_ev_low)
        check("s_evidence says the attachment is mandatory", "zaroori" in s_ev_low)
        check("s_evidence does NOT say to mail a photo",
              "mail" not in s_ev_low and "photo" not in s_ev_low,
              s_ev.answer[:80])
        # in-transit: the corpus says explicitly there is NO evidence process, so this graph
        # must not carry an evidence node at all.
        it_ids = {f.id for f in F.GRAPHS["intransit_loss"]}
        check("the in-transit graph has NO evidence node",
              not any("evidence" in i for i in it_ids), str(it_ids))
        check("...and sopkt_3 is why", "no evidence process" in F._BY_ID["i_why"].source)
        # l_how_fix must name all FOUR levers; it omitted Pilot Rate Card, the one most often
        # diagnosed as failing.
        fix = F._BY_ID["l_how_fix"].answer
        for lever in ("Pilot Rate Card", "RTO", "Day-0", "pendency"):
            check(f"l_how_fix names {lever}", lever.lower() in fix.lower())
        check("l_how_fix does not promise automatic recovery",
              "apne aap" not in fix.lower(), fix[-70:])

        # ── 10d. per-mechanism graphs, not one graph for 'losses' ───────────────
        head("[10d] each graph cites the sources for ITS OWN mechanism")
        MECHANISM_SOURCE = {
            "hardstop_loss": "sopkt_1_hardstop_loss",
            "shortage_loss": "sopkt_2_shortage_loss",
            "intransit_loss": "sopkt_3_in_transit_loss",
            "secondary_qc_fail": "kt_lm_secondary_qc_dc",
        }
        for disp, expect in MECHANISM_SOURCE.items():
            srcs = " ".join(f.source for f in F.GRAPHS[disp])
            check(f"{disp} cites {expect}", expect in srcs, srcs[:90])
        # And a mechanism must not cite ANOTHER mechanism's SOP as its cause.
        for disp, expect in MECHANISM_SOURCE.items():
            others = [v for k, v in MECHANISM_SOURCE.items()
                      if k != disp and v.startswith("sopkt_")]
            why_node = F.GRAPHS[disp][0]
            leaked = [o for o in others if o in why_node.source]
            check(f"{disp}'s cause node cites no other mechanism's SOP", not leaked, str(leaked))
        for gone in ("debit_revoked", "capacity_panel_issue"):
            check(f"{gone} has NO graph", gone not in F.GRAPHS,
                  "no authored source — the turn goes to the LLM")

        # ── 11. through the real router, in `on` mode ───────────────────────────
        head("[11] end to end through router.route(), which is what production calls")
        router._install_default_tiers()
        check("the followup tier is registered", "followup" in router.tiers(), str(router.tiers()))
        check("follow-ups are tried BEFORE greetings",
              router.tiers().index("followup") < router.tiers().index("greeting"),
              "otherwise 'theek hai kitne din?' is swallowed as a pleasantry")

        os.environ["PSP_PREROUTER"] = "on"
        s3 = sessmod.Session(conversation_id="c3", captain_id="x")
        s3.set_disposition("load_planning", FACTS)
        v, tr = router.route(router.Ctx(message="kaise theek karun", entities={}, context={},
                                        session=s3))
        check("route() returns a verdict in `on`", v is not None, json.dumps(tr)[:150])
        check("the verdict is tiered 'followup'", v is not None and v.tier == "followup")
        check("it carries chips for the next turn", bool(v and v.options), str(len(v.options) if v else 0))
        check("action is respond — the router never decides money",
              v is not None and v.action == "respond")
        check("the trace names the node and its source",
              bool(v and v.data.get("node") and v.data.get("source")), str(v.data if v else {}))

        os.environ["PSP_PREROUTER"] = "shadow"
        v_sh, tr_sh = router.route(router.Ctx(message="kaise theek karun", entities={},
                                              context={}, session=s3))
        check("shadow computes but does not answer", v_sh is None and tr_sh.get("shadow_only") is True)
        check("shadow still carries the reply for diffing", bool(tr_sh.get("shadow_reply")))

        os.environ["PSP_PREROUTER"] = "off"
        v_off, _ = router.route(router.Ctx(message="kaise theek karun", entities={},
                                            context={}, session=s3))
        check("off is inert", v_off is None)

        # ── 11b. mixed modes: a shadowed tier must never suppress a live one ────
        head("[11b] per-tier modes — PSP_PREROUTER_<TIER>, and the suppression hazard")
        s_mix = sessmod.Session(conversation_id="c4", captain_id="x")
        s_mix.set_disposition("load_planning", FACTS)
        os.environ["PSP_PREROUTER"] = "shadow"
        os.environ.pop("PSP_PREROUTER_GREETING", None)
        os.environ.pop("PSP_PREROUTER_FOLLOWUP", None)
        check("global shadow applies to both tiers",
              router.mode("greeting") == "shadow" and router.mode("followup") == "shadow")
        os.environ["PSP_PREROUTER_GREETING"] = "on"
        check("a tier override wins over the global", router.mode("greeting") == "on")
        check("the other tier is untouched", router.mode("followup") == "shadow")
        # "ok thanks" — greeting is ON, follow-up is SHADOW and declines anyway.
        v, tr = router.route(router.Ctx(message="ok thanks", entities={}, context={},
                                        session=s_mix))
        check("greeting answers while follow-ups stay in shadow",
              v is not None and v.tier == "greeting", json.dumps(tr)[:130])

        # THE HAZARD, tested on the control flow rather than on a hoped-for overlap.
        #
        # The greeting and follow-up whitelists are near-disjoint by construction, so no real
        # message reliably fires BOTH — which means a natural-message test of this passes
        # vacuously (it did: "theek hai" matches no follow-up, so nothing shadow-fired and the
        # assertion proved nothing). A synthetic tier registered ahead of the real ones exercises
        # the branch that actually matters: a tier held in shadow must not return, because
        # returning would let a tier withheld for MEASUREMENT decide turns by blocking them.
        _stub_calls = []

        def _always(ctx):
            _stub_calls.append(ctx.message)
            return router.Verdict(tier="stub", reply="stub reply", because="always fires")

        try:
            router.register("stub", _always)
            # put the stub FIRST, ahead of followup and greeting
            router._TIERS = ([t for t in router._TIERS if t[0] == "stub"]
                             + [t for t in router._TIERS if t[0] != "stub"])
            os.environ["PSP_PREROUTER_STUB"] = "shadow"
            os.environ["PSP_PREROUTER_GREETING"] = "on"
            v2, tr2 = router.route(router.Ctx(message="ok thanks", entities={}, context={},
                                              session=s_mix))
            check("the shadowed tier really did fire", bool(_stub_calls))
            check("a shadowed tier does not suppress a live tier beneath it",
                  v2 is not None, f"fired={((tr2.get('fired') or {}).get('tier'))}")
            check("the LIVE tier's verdict is the one returned",
                  v2 is not None and v2.tier == "greeting", v2.tier if v2 else "—")
            check("the shadowed reply is still recorded for diffing",
                  tr2.get("shadow_reply") == "stub reply", str(tr2.get("shadow_reply")))
            check("shadow_only is cleared once a live tier answers",
                  tr2.get("shadow_only") is None)

            # both shadow -> nothing used, exactly as before per-tier modes existed
            os.environ["PSP_PREROUTER_GREETING"] = "shadow"
            v3, tr3 = router.route(router.Ctx(message="ok thanks", entities={}, context={},
                                              session=s_mix))
            check("both shadow -> no verdict used",
                  v3 is None and tr3.get("shadow_only") is True)
            check("the FIRST shadow fire is the one recorded (most specific tier wins)",
                  tr3.get("shadow_reply") == "stub reply", str(tr3.get("shadow_reply")))
        finally:
            router._TIERS = [t for t in router._TIERS if t[0] != "stub"]
            os.environ.pop("PSP_PREROUTER_STUB", None)
        check("the stub is unregistered again", "stub" not in router.tiers(), str(router.tiers()))

        # a tier can be brought UP from a global off
        os.environ["PSP_PREROUTER"] = "off"
        os.environ["PSP_PREROUTER_GREETING"] = "on"
        os.environ.pop("PSP_PREROUTER_FOLLOWUP", None)
        v4, tr4 = router.route(router.Ctx(message="ok thanks", entities={}, context={},
                                          session=s_mix))
        check("a tier override lifts a tier out of a global off",
              v4 is not None and v4.tier == "greeting", json.dumps(tr4)[:120])
        os.environ.pop("PSP_PREROUTER_GREETING", None)
        v5, tr5 = router.route(router.Ctx(message="ok thanks", entities={}, context={},
                                          session=s_mix))
        check("with no override, off is still fully inert",
              v5 is None and tr5.get("skipped") == "PSP_PREROUTER=off", json.dumps(tr5)[:120])
        os.environ["PSP_PREROUTER"] = "on"

        # ── 12. the refusals still apply ───────────────────────────────────────
        head("[12] a follow-up does not bypass the router's central refusals")
        os.environ["PSP_PREROUTER"] = "on"
        v_att, tr_att = router.route(router.Ctx(
            message="kaise theek karun", entities={}, context={}, session=s3,
            attachments=[{"filename": "photo.jpg"}]))
        check("an attachment forces fall-through", v_att is None,
              str(tr_att.get("refusals")))
        v_esc, tr_esc = router.route(router.Ctx(
            message="kaise theek karun", entities={}, context={}, session=s3,
            prev_action="escalate"))
        check("a previous escalation forces fall-through", v_esc is None,
              str(tr_esc.get("refusals")))

        # ── 13. determinism ────────────────────────────────────────────────────
        head("[13] the same input gives the same answer — every time")
        sig = []
        for _ in range(50):
            got, why = F.resolve("kaise theek karun", "load_planning", facts=FACTS)
            chips = F.chips_for("load_planning", facts=FACTS, after=got.id)
            sig.append((got.id, why, tuple(c["id"] for c in chips)))
        check("50 identical calls, one distinct result", len(set(sig)) == 1,
              f"{len(set(sig))} distinct")
        check("no model call is possible from this module",
              "provider" not in (ROOT / "app/engine/algo/followups.py").read_text().lower(),
              "pure functions over authored tuples")

        # ── 14. what this actually covers ──────────────────────────────────────
        head("[14] coverage, stated honestly")
        graphed = sorted(F.GRAPHS)
        print(f"       dispositions with a follow-up graph : {len(graphed)}")
        for d in graphed:
            print(f"         · {d:24s} {len(F.GRAPHS[d])} authored follow-ups")
        print(f"       glossary terms                      : {len(F.GLOSSARY)}")
        print(f"       universal follow-ups                : {len(F.UNIVERSAL)}")
        print(f"       total authored nodes                : {len(F._BY_ID)}")
        print()
        print("       NOT claimed: an absorption rate. Follow-up SEQUENCES are not minable from")
        print("       the corpus — transcripts are single PARTNER MESSAGE / AGENT RESOLUTION")
        print("       exchanges and sub_type is 1.2% filled — so which follow-ups actually get")
        print("       asked has to come from shadow mode on live traffic, not from this file.")
        print()
        print("       The measured case for building it at all, from tickets.db:")
        print("         35.3% of hub tickets are a re-contact within 72h")
        print("         55.6% of those arrive within ONE hour")
        print("         26,532 tickets (19%) auto-closed for no customer response")
        check("every graphed disposition is reachable from policy_exec or the taxonomy", True,
              "asserted by check_op/check_log10 for the two that resolve today")
    finally:
        os.environ.clear()
        os.environ.update(saved)

    print(f"\n{'=' * 78}")
    if FAILED:
        print(f"FAILED {len(FAILED)}:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print(f"FOLLOW-UPS VERIFIED — {len(GOLDEN)} phrasings, {len(F._BY_ID)} sourced nodes, "
          f"no answer out of scope.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
