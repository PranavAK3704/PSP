"""Stage 3. The gate MARKS; it never deletes. And it has one exception it must never violate."""
from __future__ import annotations

import shutil

import pytest

from app.intake import loadstage, noise, store

pytestmark = pytest.mark.usefixtures("intake_db")


@pytest.fixture(scope="session")
def rules():
    return noise.load_rules()


@pytest.fixture()
def gated(tmp_path, fixtures_dir):
    raw = tmp_path / "raw"
    raw.mkdir()
    for f in sorted(fixtures_dir.glob("*.ndjson")):
        shutil.copy(f, raw / f.name)
    loadstage.load(raw, run_id="r1", skip_validate=True)
    stats = noise.run("r1")
    con = store.connect()
    yield con, stats
    con.close()


# ── the exception ───────────────────────────────────────────────────────────────────────────
def test_empty_text_with_an_attachment_is_never_gated(rules):
    """~5% of lm-ams records are exactly this — the issue lives entirely in a screenshot.
    Any emptiness rule without this carve-out deletes real issues while the filtered count
    looks healthy."""
    d = noise.classify("", None, True, rules)
    assert d["gated"] is False
    assert d["gate_rule"] == "kept:empty_text_with_attachment"


def test_the_exception_beats_every_other_rule(rules):
    """Even a subtype that would normally be dropped."""
    d = noise.classify("", "channel_join", True, rules)
    assert d["gated"] is False


def test_the_fixture_corpus_keeps_its_screenshot_only_message(gated):
    con, stats = gated
    assert stats["kept_empty_text_with_attachment"] == 1
    row = con.execute(
        "SELECT f.gated FROM messages m JOIN message_flags f USING(channel_id, message_id) "
        "WHERE f.run_id='r1' AND TRIM(m.text)='' AND m.has_media=1").fetchone()
    assert row["gated"] == 0


# ── gating marks, never deletes ─────────────────────────────────────────────────────────────
def test_gated_messages_are_still_in_the_store(gated):
    con, stats = gated
    assert stats["gated"] > 0
    total = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    flagged = con.execute("SELECT COUNT(*) FROM message_flags WHERE run_id='r1'").fetchone()[0]
    assert total == flagged, "every message gets a flag row; none are removed"


def test_every_gated_message_names_the_rule_that_caught_it(gated):
    con, _ = gated
    unlabelled = con.execute(
        "SELECT COUNT(*) FROM message_flags WHERE run_id='r1' AND gated=1 "
        "AND (gate_rule IS NULL OR gate_rule='')").fetchone()[0]
    assert unlabelled == 0


# ── the rules themselves ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text,rule", [
    ("ok", "bare_ack"), ("Okk!!", "bare_ack"), ("noted", "bare_ack"),
    ("thanks", "thanks"), ("Thank you!", "thanks"),
    ("noted, thanks", "combined_ack"), ("ok sir", "combined_ack"),
    ("+1", "plus_one"), ("same here", "plus_one"),
    ("FYI", "fyi_only"), ("hi", "greeting"),
    ("<@U09HH8QKZ43>", "mention_only"), ("<!channel>", "mention_only"),
    ("\U0001F44D", "emoji_only"),
])
def test_gate_rules_fire_and_are_labelled_correctly(text, rule, rules):
    d = noise.classify(text, None, False, rules)
    assert d["gated"] is True
    assert d["gate_rule"] == rule


@pytest.mark.parametrize("text", [
    "thanks, but the DC is still down",     # starts politely, IS an issue
    "ok so the payout is still not reflecting",
    "DC Code: NQS not closing",
    "any update ??",                        # an orphan follow-up is NOT noise
])
def test_real_messages_are_not_gated(text, rules):
    assert noise.classify(text, None, False, rules)["gated"] is False


def test_subtypes_are_gated_and_are_a_minority(gated):
    """3-20% of a day, NOT the 40% originally assumed. The gate is for acks, not volume."""
    con, stats = gated
    joins = sum(v for k, v in stats["gated_by_rule"].items() if k.startswith("subtype:"))
    assert joins == 3
    assert joins / stats["messages"] < 0.20


# ── informational: flagged, never gated, reported as a range ────────────────────────────────
def test_weather_is_informational_not_gated(rules):
    d = noise.classify("Heavy rainfall since 4 AM, most FE unable to start deliveries.",
                       None, False, rules)
    assert d["gated"] is False, "a weather callout is deliberate operational comms, not noise"
    assert d["informational"] is True
    assert d["informational_rule"].startswith("strict:")
    assert d["borderline"] is False


def test_the_festival_message_is_borderline_not_counted_either_way(rules):
    """The real message that forced the range: it leads with manpower and mentions rain
    second. Counting it either way moves the share by a percentage point silently."""
    d = noise.classify(
        "Due to festival manpower absenteeism today only 40% FE reported, and rain since "
        "morning is adding to it. Expect delivery percentage impact.", None, False, rules)
    assert d["informational"] is True
    assert d["borderline"] is True
    assert d["informational_rule"].startswith("loose:")
    assert "festival" in d["informational_rule"]


def test_the_share_is_a_range_and_borderlines_are_listed_by_id(gated):
    _, stats = gated
    lo, hi = stats["informational_share_range"]
    assert lo <= hi
    assert stats["informational_strict"] <= stats["informational_loose"]
    # every borderline is named, not summarised into a number
    assert len(stats["borderline"]) == stats["informational_loose"] - stats["informational_strict"]
    assert all("/" in b for b in stats["borderline"])


def test_informational_is_a_separate_axis_from_gated(gated):
    """Nothing may be both. Weather is not noise; noise is not informational."""
    con, _ = gated
    both = con.execute("SELECT COUNT(*) FROM message_flags "
                       "WHERE run_id='r1' AND gated=1 AND informational=1").fetchone()[0]
    assert both == 0


# ── the config, not the code ────────────────────────────────────────────────────────────────
def test_yaml_booleans_cannot_break_the_gate(tmp_path):
    """YAML 1.1 parses bare `yes`/`no` as booleans, so an unquoted ack arrives as Python True
    and the gate dies on .lower() three stages into a run. The loader coerces."""
    p = tmp_path / "noise.yaml"
    p.write_text("subtypes: []\nrules:\n  - name: t\n    exact: [yes, no, ok]\n"
                 "informational:\n  weather_terms: []\n  competing_terms: []\n")
    r = noise.load_rules(p)
    assert r["rules"][0]["exact"] == ["True", "False", "ok"]
    assert noise.classify("ok", None, False, r)["gated"] is True


def test_rule_order_is_config_order(rules):
    """mention_only must precede emoji_only: both strip to empty, and a bare @-ping reported
    as emoji_only is a wrong answer to 'what did the filter throw away'."""
    names = [r["name"] for r in rules["rules"]]
    assert names.index("mention_only") < names.index("emoji_only")
