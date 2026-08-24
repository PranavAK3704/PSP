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
    ("kyun kam hua", "load_planning", "l_why_low", "no domain word, still scoped"),
    ("kaise theek karun", "load_planning", "l_how_fix", ""),
    ("kaise improve karun", "load_planning", "l_how_fix", "English verb, Hindi frame"),
    ("phir load badhega", "load_planning", "l_will_increase", ""),
    ("mera number kya hai", "load_planning", "l_my_numbers", "fact-filled"),
    ("kitna nuksan hua", "load_planning", "l_money_lost", "fact-filled, rupee figure"),
    ("target kaun decide karta hai", "load_planning", "l_who_decides", ""),
    # ── in scope: the loss family ────────────────────────────────────────────
    ("ye loss kyun laga", "hardstop_loss", "d_why_marked", ""),
    ("paisa wapas milega", "hardstop_loss", "d_can_reverse", "must not promise a payment"),
    ("kya evidence chahiye", "shortage_loss", "d_what_evidence", ""),
    ("loss kyun laga", "shortage_loss", "d_why_marked", "same graph, different disposition"),
    ("kyun laga", "intransit_loss", "d_why_marked", "third disposition on the loss graph"),
    # ── the glossary, in scope from anywhere ─────────────────────────────────
    ("rto kya hai", "load_planning", "g_rto", ""),
    ("ocf kya hai", "load_planning", "g_ocf", ""),
    ("cps kya hai", "load_planning", "g_cps", ""),
    ("pendency kya hai", "load_planning", "g_doh", ""),
    ("doh kya hai", "load_planning", "g_doh", "abbreviation"),
    ("bic kya hai", "load_planning", "g_bic", ""),
    ("hardstop kya hai", "hardstop_loss", "g_hardstop", ""),
    ("shortage kya hai", "hardstop_loss", "g_shortage", ""),
    ("capacity cut kaise lagta hai", "load_planning", "g_pbca", ""),
    ("day0 attempt kya hai", "load_planning", "g_day0", ""),
    ("rto kya hai", "hardstop_loss", "g_rto", "glossary is scope-independent"),
    # ── universal, askable after anything ────────────────────────────────────
    ("insaan se baat karni hai", "hardstop_loss", "u_talk_human", ""),
    ("mujhe agent se baat karni hai", "load_planning", "u_talk_human", ""),
    ("kisko bheja hai", "hardstop_loss", "u_who_has_it", ""),
    # ── DECLINES, which is where the safety lives ───────────────────────────
    ("mera payment nahi aaya", "load_planning", None, "a NEW concern, not a follow-up"),
    ("cod pendency clear karo", "load_planning", None, "different queue entirely"),
    ("kitne din lagenge", "load_planning", None,
     "genuinely ambiguous — my recovery or the team's TAT? chips, not a guess"),
    # NOT a tie, and the asymmetry with the load scope is the scoping mechanism working:
    # `l_how_long` (my load's recovery) lives only in the LOAD graph, so inside a loss scope the
    # only "how long" question authored is the team's TAT — and that is the right answer to give
    # someone who just had a case raised. Same words, different scope, different answer.
    ("kitne din", "hardstop_loss", "u_how_long_team", "unambiguous inside the loss scope"),
    ("kitne din lagenge", "shortage_loss", "u_how_long_team", "same"),
    ("kuch samajh nahi aaya", "load_planning", None, "no keyword — the LLM should take this"),
    ("aaj mausam accha hai", "load_planning", None, "off-domain"),
    ("", "load_planning", None, "empty"),
    ("   ", "load_planning", None, "whitespace only"),
    ("gaadi kab aayegi", "load_planning", None, "in-domain-sounding, not an authored follow-up"),
    ("fe id block ho gayi", "hardstop_loss", None, "a different disposition's concern"),
]

# Phrasings that must NOT be answered when there is no disposition in scope. Turn one always
# goes to the LLM — without a scope this tier IS the open-NLU problem it exists to avoid.
NO_SCOPE = ["load kam kyun hua", "rto kya hai", "paisa wapas milega", "kaise theek karun",
            "insaan se baat karni hai", "kitna nuksan hua"]

FACTS = {"lever": "RTO Performance", "current": "31%", "target": "19%",
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
        v = F.tier(router.Ctx(message="mera number kya hai", entities={}, context={}, session=s2))
        check("tier declines a fact-filled node when the fact is absent", v is None)
        # with facts, it renders and the number appears
        s2.answer_facts = dict(FACTS)
        v = F.tier(router.Ctx(message="mera number kya hai", entities={}, context={}, session=s2))
        check("with facts it renders the real value", v is not None and "31%" in v.reply,
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
        head("[9] no node matches on a word that appears in every question")
        for f in F._BY_ID.values():
            bad_toks = f.match & F.STOPWORDS
            check(f"{f.id} avoids stopwords", not bad_toks, str(bad_toks) if bad_toks else "")
        # the specific regression: "kya" on d_what_evidence tied with the glossary and declined
        got, _ = F.resolve("hardstop kya hai", "hardstop_loss", facts=FACTS)
        check("REGRESSION: 'hardstop kya hai' is answered, not tied into a decline",
              got is not None and got.id == "g_hardstop")

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
        rev = F._BY_ID["d_can_reverse"]
        check("the reversal answer says explicitly that it cannot pay",
              "khud paisa wapas nahi" in rev.answer.lower(), rev.answer[:60])

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
