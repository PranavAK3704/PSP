"""Positive evidence — the tier that decides whether a message is an issue at all.

The bug this stage exists to fix: "can we go to play arena?" raised a ticket, because every
gate before it was NEGATIVE and anything matching no rejection rule was treated as an issue.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.intake import evidence, extract as istage, group, loadstage, noise, store

pytestmark = pytest.mark.usefixtures("intake_db")


@pytest.fixture(scope="session")
def cfg():
    return evidence.load_config()


@pytest.fixture(scope="session")
def entity_kinds():
    pats = istage.load_patterns()
    reg = istage.load_code_list(istage.DC_CODES)
    deny = istage.load_code_list(istage.DC_DENYLIST)
    return lambda t: {k for k, _, _, _ in istage.extract_text(t, pats, reg, deny)}


# ── the bug ─────────────────────────────────────────────────────────────────────────────────
def test_the_play_arena_message_is_not_an_issue(cfg):
    d = evidence.score("can we go to play arena?", set(), cfg)
    assert d["decision"] == "not_an_issue"
    assert d["score"] == 0.0
    assert "no_identifier_and_no_ops_noun" in d["reasons"]


@pytest.mark.parametrize("text", [
    "can we go to play arena?",
    "anyone up for lunch at 1?",
    "is the cafeteria open today?",
    "happy birthday bhai",
    "who is coming to the offsite",
])
def test_off_topic_chat_is_rejected(text, cfg):
    assert evidence.score(text, set(), cfg)["decision"] == "not_an_issue"


def test_an_ops_noun_in_an_off_topic_sentence_still_qualifies_and_that_is_correct(cfg):
    """"please share the wifi password" scores as an issue, because `password` is a genuine ops
    noun — captain-panel password resets are a real ticket type.

    That is the right trade, and it is the same asymmetry this pipeline applies everywhere:
    a spurious ticket costs someone one click to close, a dropped real issue is invisible and
    unrecoverable. Removing `password` from the lexicon to win this one case would silently
    lose every genuine password-reset request.

    The disambiguator is `wifi`, and chasing it means maintaining a denylist of every
    non-Valmo noun in the world — the vocabulary arms race this design exists to avoid."""
    d = evidence.score("please share the wifi password", set(), cfg)
    assert d["decision"] == "issue"
    assert "password" in d["ops"]
    # and the sentence with no ops noun at all still dies, which is the property that matters
    assert evidence.score("please share the wifi", set(), cfg)["decision"] == "not_an_issue"


# ── THE rule: grammar never creates evidence ────────────────────────────────────────────────
def test_grammar_alone_is_never_evidence(cfg):
    """'can we go to play arena?' and 'can we get the payout released?' have IDENTICAL grammar
    — request, question mark, first-person plural. Only one names something we operate. If
    grammar could create evidence, no rule could separate them."""
    off = evidence.score("can we go to play arena?", set(), cfg)
    on = evidence.score("can we get the payout released?", set(), cfg)
    assert off["decision"] == "not_an_issue" and off["score"] == 0.0
    assert on["decision"] == "issue"
    assert on["ops"], "the ONLY difference is that this one names a thing we operate"


def test_urgency_alone_does_not_qualify(cfg):
    """Shouting does not make something an issue."""
    d = evidence.score("URGENT please help ASAP!!", set(), cfg)
    assert d["decision"] == "not_an_issue"


# ── the two sources of evidence ─────────────────────────────────────────────────────────────
def test_an_identifier_alone_qualifies(cfg):
    """A bare identifier is decisive — someone typed a waybill for a reason."""
    assert evidence.score("VL0084870753799", {"waybill"}, cfg)["decision"] == "issue"


@pytest.mark.parametrize("text", [
    "payout nahi aaya abhi tak",
    "invoice generate nahi ho raha",
    "app crash ho raha hai",
    "load not received since yesterday",
    "vehicle held at check post",
    "panel me login nahi ho raha",
])
def test_ops_noun_plus_a_problem_qualifies(text, cfg):
    assert evidence.score(text, set(), cfg)["decision"] == "issue"


# ── orphans are not rejections ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", ["any update ??", "gentle reminder", "?", "??", "kab tak"])
def test_bare_followups_become_orphans_not_rejections(text, cfg):
    """The brief calls untreaded follow-ups 'the real mess'. The context lives in a message we
    failed to link, so a human needs to see it — dropping it is the wrong answer."""
    assert evidence.score(text, set(), cfg)["decision"] == "orphan"


def test_a_bare_question_mark_is_only_an_orphan_when_it_IS_the_message(cfg):
    """'?' was a follow-up SUBSTRING once, which made every question an orphan — including
    'can we go to play arena?'. It is now a punctuation-only test."""
    assert evidence.score("?", set(), cfg)["decision"] == "orphan"
    assert evidence.score("can we go to play arena?", set(), cfg)["decision"] == "not_an_issue"


# ── the safety property ─────────────────────────────────────────────────────────────────────
def test_no_real_ticket_in_the_corpus_is_rejected(cfg, entity_kinds):
    """THE regression test. This is the one tier where a real issue can die silently, so the
    bar is absolute: every message the corpus labels a ticket must survive."""
    corpus = json.loads(
        (Path(__file__).resolve().parents[2] / "data" / "intake" / "corpus" /
         "messages.json").read_text())["messages"]
    killed = [m for m in corpus if m["expect"] == "ticket"
              and evidence.score(m["text"], entity_kinds(m["text"]), cfg)["decision"]
              == "not_an_issue"]
    assert killed == [], f"{len(killed)} real tickets rejected"


def test_every_decision_records_why(cfg):
    for text in ["can we go to play arena?", "payout nahi aaya", "any update ??"]:
        assert evidence.score(text, set(), cfg)["reasons"], "a rejection with no reason is unauditable"


# ── wiring ──────────────────────────────────────────────────────────────────────────────────
@pytest.fixture()
def pipeline(tmp_path, fixtures_dir):
    raw = tmp_path / "raw"
    raw.mkdir()
    for f in sorted(fixtures_dir.glob("*.ndjson")):
        shutil.copy(f, raw / f.name)
    loadstage.load(raw, run_id="r1", skip_validate=True)
    noise.run("r1")
    istage.run("r1")
    con = store.connect()
    yield con
    con.close()


def test_evidence_stores_every_decision_including_rejections(pipeline):
    stats = evidence.run("r1", con=pipeline)
    stored = pipeline.execute("SELECT COUNT(*) FROM evidence WHERE run_id='r1'").fetchone()[0]
    assert stored == stats["messages"]
    unexplained = pipeline.execute(
        "SELECT COUNT(*) FROM evidence WHERE run_id='r1' AND (reasons IS NULL OR reasons='')"
    ).fetchone()[0]
    assert unexplained == 0


def test_a_rejected_message_cannot_anchor_an_issue(pipeline):
    evidence.run("r1", con=pipeline)
    group.run("r1", con=pipeline)
    leaked = pipeline.execute(
        "SELECT COUNT(*) FROM assignments a JOIN evidence e "
        "  ON e.run_id=a.run_id AND e.channel_id=a.channel_id AND e.message_id=a.message_id "
        "WHERE a.run_id='r1' AND e.decision='not_an_issue' AND a.issue_id IS NOT NULL"
    ).fetchone()[0]
    assert leaked == 0


def test_an_orphan_is_unassigned_not_discarded(pipeline):
    """It must reach stage 6, not the bin."""
    evidence.run("r1", con=pipeline)
    group.run("r1", con=pipeline)
    rows = pipeline.execute(
        "SELECT a.issue_id, a.rule FROM assignments a JOIN evidence e "
        "  ON e.run_id=a.run_id AND e.channel_id=a.channel_id AND e.message_id=a.message_id "
        "WHERE a.run_id='r1' AND e.decision='orphan'").fetchall()
    for r in rows:
        assert r["issue_id"] is None and r["rule"] == "unassigned"


def test_without_the_evidence_stage_nothing_is_filtered(pipeline):
    """An ungated run must be visibly ungated rather than silently empty."""
    pipeline.execute("DELETE FROM evidence WHERE run_id='r1'")
    pipeline.commit()
    stats = group.run("r1", con=pipeline)
    assert stats["excluded_no_evidence"] == 0


# ── an ask beats the weather ─────────────────────────────────────────────────────────────────

def test_a_partner_asking_for_reversal_is_not_a_weather_callout():
    """MEASURED FAILURE, twice in one afternoon, on a live channel:

        "Dear losses team, I have so many losses at my hub please reverse - rain has made it
         extremely difficult to route these parcels"

    was held back as an operational weather callout and never became a ticket. It is not a
    callout — it is a partner asking for their losses to be reversed, with the rain offered as
    the reason. The old rule already knew it was unsure (an ops topic came first, so it marked
    the message `borderline`) and suppressed it anyway. Borderline has to mean something.
    """
    from app.intake import noise
    rules = noise.load_rules()
    d = noise.classify(
        "Dear losses team, I have so many losses at my hub please reverse - rain has made it "
        "extremely difficult to route these parcels", None, False, rules)
    assert d["informational"] is False, d.get("informational_rule")
    assert "with_ask" in d["informational_rule"]


def test_a_genuine_weather_callout_is_still_held_back():
    """The fix must not simply disable the classifier — these are deliberate comms, and a
    register full of rain announcements is the thing it exists to prevent."""
    from app.intake import noise
    rules = noise.load_rules()
    for text in ("Heavy rain in Bangalore today, expect delays",
                 "rain has flooded the hub, all routes stopped",
                 "IMD alert: red alert for the city tomorrow"):
        assert noise.classify(text, None, False, rules)["informational"] is True, text


def test_weather_after_an_ops_topic_with_no_ask_stays_informational():
    """The ask is what distinguishes them. "manpower short today because of rain" reports a
    situation; it does not request anything."""
    from app.intake import noise
    rules = noise.load_rules()
    d = noise.classify("manpower short today because of rain", None, False, rules)
    assert d["informational"] is True and d["borderline"] is True


# ── Devanagari ───────────────────────────────────────────────────────────────────────────────

def test_a_devanagari_message_is_recognised_as_an_issue():
    """MEASURED FAILURE on the live channel: "मेरा पेमेंट नहीं आया" produced no ticket at all.
    WhatsApp traffic will be heavily Devanagari, so silence there is not a small gap."""
    from app.intake import evidence
    cfg = evidence.load_config()
    d = evidence.score("मेरा पेमेंट नहीं आया", {}, cfg)
    assert d["decision"] == "issue", d


def test_the_word_boundary_works_on_combining_marks():
    r"""THE LATENT BUG UNDERNEATH. `\b` is defined through `\w`, so a term ending in a matra or
    anusvara could never match: `\bपेमेंट\b` works (ends in a consonant) but `\bनहीं\b` never
    does. Adding Hindi vocabulary would have silently worked for half of it and no error would
    have appeared anywhere."""
    import re
    from app.intake import evidence
    assert re.search(evidence._WORDISH, "क")
    rx = re.compile(rf"(?<!{evidence._WORDISH})नहीं(?!{evidence._WORDISH})")
    assert rx.search("मेरा पेमेंट नहीं आया")
    # and it still refuses a fragment of a longer word
    assert not re.compile(rf"(?<!{evidence._WORDISH})पे(?!{evidence._WORDISH})").search("पेमेंट")


def test_a_devanagari_greeting_is_still_not_an_issue():
    """The fix must not turn every Hindi sentence into a ticket."""
    from app.intake import evidence
    cfg = evidence.load_config()
    assert evidence.score("नमस्ते", {}, cfg)["decision"] == "not_an_issue"
