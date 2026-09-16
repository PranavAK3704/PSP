"""Stages 5 and the evaluation harness.

The grouping tests are mostly about what must NOT happen. A wrong merge contaminates two
issues and sends the wrong DC the wrong answer; a wrong split leaves two rows a human joins in
the review sheet. Those are not symmetric, so the assertions are asymmetric too.
"""
from __future__ import annotations

import json
import shutil

import pytest

from app.intake import evaluate, extract, group, loadstage, noise, store

pytestmark = pytest.mark.usefixtures("intake_db")


@pytest.fixture()
def pipeline(tmp_path, fixtures_dir):
    raw = tmp_path / "raw"
    raw.mkdir()
    for f in sorted(fixtures_dir.glob("*.ndjson")):
        shutil.copy(f, raw / f.name)
    loadstage.load(raw, run_id="r1", skip_validate=True)
    extract.run("r1")
    noise.run("r1")
    con = store.connect()
    yield con
    con.close()


@pytest.fixture(scope="session")
def same_hub(fixtures_dir):
    return json.loads((fixtures_dir / "expected_same_hub.json").read_text())


@pytest.fixture(scope="session")
def cross_post(fixtures_dir):
    return json.loads((fixtures_dir / "expected_cross_post.json").read_text())


def _issue_of(con, message_id):
    row = con.execute("SELECT issue_id FROM assignments WHERE run_id='r1' AND message_id=?",
                      (message_id,)).fetchone()
    return row["issue_id"] if row else None


# ── the priority order ──────────────────────────────────────────────────────────────────────
def test_every_assignment_records_a_rule_and_a_reason(pipeline):
    """Including the ones that fired nothing — 'why did this land here' must be answerable
    for every row, not just the interesting ones."""
    group.run("r1", con=pipeline)
    bad = pipeline.execute(
        "SELECT COUNT(*) FROM assignments WHERE run_id='r1' "
        "AND (rule IS NULL OR rule='' OR reason IS NULL OR reason='')").fetchone()[0]
    assert bad == 0


def test_threaded_replies_join_their_parent_at_full_confidence(pipeline):
    group.run("r1", con=pipeline)
    rows = pipeline.execute(
        "SELECT a.issue_id, a.confidence, m.thread_ref, m.channel_id FROM assignments a "
        "JOIN messages m USING(channel_id, message_id) "
        "WHERE a.run_id='r1' AND a.rule='thread_ref'").fetchall()
    assert rows
    for r in rows:
        assert r["confidence"] == 1.0
        assert r["issue_id"] == _issue_of(pipeline, r["thread_ref"])


def test_gated_messages_are_not_grouped(pipeline):
    group.run("r1", con=pipeline)
    n = pipeline.execute(
        "SELECT COUNT(*) FROM assignments a JOIN message_flags f "
        "USING(channel_id, message_id) WHERE a.run_id='r1' AND f.run_id='r1' AND f.gated=1"
    ).fetchone()[0]
    assert n == 0


# ── the bias: split, do not merge ───────────────────────────────────────────────────────────
def test_one_hub_two_issues_stays_two_by_default(pipeline, same_hub):
    """NQS raises a payout problem and, 26 hours later, a vehicle held at a check post. They
    share only the hub. The default must NOT merge them."""
    group.run("r1", con=pipeline, join_weak=False)
    a, b = same_hub["_anchors"]
    assert _issue_of(pipeline, a) != _issue_of(pipeline, b)


def test_joining_on_a_dc_code_merges_them_which_is_why_it_is_off(pipeline, same_hub):
    """The cost of the setting, as a measured fact rather than an argument: enabling the weak
    join collapses two unrelated issues into whichever came first."""
    group.run("r1", con=pipeline, join_weak=True)
    a, b = same_hub["_anchors"]
    assert _issue_of(pipeline, a) == _issue_of(pipeline, b)


def test_the_cross_post_is_two_issues_not_one(pipeline, cross_post):
    """Stage 5 must NOT merge across channels — stage 8 links them with duplicate_of instead,
    so both keep their own thread and closure state."""
    group.run("r1", con=pipeline)
    a, b = cross_post["_cross_channel_duplicate"]
    ia, ib = _issue_of(pipeline, a), _issue_of(pipeline, b)
    assert ia is not None and ib is not None
    assert ia != ib


def test_an_orphan_reply_becomes_unassigned_not_a_new_issue(pipeline):
    """A reply whose parent was gated is handed to stage 6. Inventing an issue out of an
    'any update ??' would be worse than admitting it is unplaced."""
    con = pipeline
    parent = con.execute("SELECT channel_id, message_id FROM messages "
                         "WHERE thread_ref IS NULL AND subtype IS NULL LIMIT 1").fetchone()
    con.execute("UPDATE message_flags SET gated=1, gate_rule='forced' "
                "WHERE run_id='r1' AND message_id=?", (parent["message_id"],))
    con.commit()
    group.run("r1", con=con)
    kids = con.execute("SELECT message_id FROM messages WHERE thread_ref=?",
                       (parent["message_id"],)).fetchall()
    for k in kids:
        row = con.execute("SELECT rule, issue_id FROM assignments "
                          "WHERE run_id='r1' AND message_id=?", (k["message_id"],)).fetchone()
        assert row["rule"] == "unassigned"
        assert row["issue_id"] is None


# ── the evaluation harness ──────────────────────────────────────────────────────────────────
def test_the_headline_number_is_first_and_costs_nothing(pipeline):
    group.run("r1", con=pipeline)
    r = evaluate.run("r1", con=pipeline)
    assert list(r)[1] == "THE_NUMBER_grouping_without_stage_6"
    assert r["cost"]["llm_calls"] == 0
    assert r["cost"]["messages_that_reached_claude"] == 0


def test_grouping_is_reported_per_channel_and_the_blend_is_labelled(pipeline):
    """A blended number would have hidden the firefighters-vs-pilot_support gap."""
    group.run("r1", con=pipeline)
    h = evaluate.run("r1", con=pipeline)["THE_NUMBER_grouping_without_stage_6"]
    per = h["entity_join_only"]["per_channel"]
    assert len(per) >= 2
    assert "blended_do_not_quote_alone" in h["entity_join_only"]


def test_synthetic_only_labels_are_flagged_loudly(pipeline):
    """A score against fixture-derived labels says the pipeline agrees with the fixture author.
    Quoting that as accuracy would be worse than quoting nothing, so evaluate says so itself."""
    group.run("r1", con=pipeline)
    r = evaluate.run("r1", con=pipeline)
    assert r["CAVEAT"] is not None
    assert "SYNTHETIC ONLY" in r["CAVEAT"]
    assert r["golden"]["real"] == 0


def test_dc_precision_and_recall_are_reported_per_tier_with_the_false_positives(pipeline):
    group.run("r1", con=pipeline)
    dc = evaluate.run("r1", con=pipeline)["dc_extraction"]
    assert "tier_A" in dc and "tier_B" in dc
    assert dc["tier_A"]["precision"] == 1.0
    assert dc["tier_A"]["recall"] == 1.0
    assert dc["tier_A"]["false_positive_list"] == []
    # listed, not just counted
    assert isinstance(dc["tier_B"]["false_positive_list"], list)


def test_registry_coverage_is_its_own_number(pipeline):
    """So partial registry coverage never reads as poor regex recall."""
    group.run("r1", con=pipeline)
    reg = evaluate.run("r1", con=pipeline)["dc_extraction"]["registry"]
    assert reg["size"] > 10000
    assert "seen_but_MISSING_from_registry" in reg
    assert "L9D" in reg["seen_but_MISSING_from_registry"]


def test_intent_reports_unavailable_rather_than_zero_when_stage_6_has_not_run(pipeline):
    group.run("r1", con=pipeline)
    i = evaluate.run("r1", con=pipeline)["intent"]
    assert i["available"] is False
    assert i["labelled_available"] > 0


def test_one_to_one_matching_is_deterministic_and_catches_degenerate_clusters():
    pred = {"a": "P1", "b": "P1", "c": "P2"}
    gold = {"a": "G1", "b": "G1", "c": "G2"}
    assert evaluate.one_to_one(pred, gold)["accuracy"] == 1.0

    everything_one = {"a": "P", "b": "P", "c": "P"}
    m = evaluate.one_to_one(everything_one, gold)
    assert m["accuracy"] < 1.0
    # the degenerate shape is visible rather than hidden inside the percentage
    assert m["pred_clusters"] == 1 and m["gold_clusters"] == 2


# ── the qualification gate must actually bite ───────────────────────────────────────────────
def test_an_excluded_channel_contributes_no_issues(pipeline):
    """Without this the gate is decorative: pilot_support_ams scores 11%, is excluded, and its
    sick-leave messages still become issues. This is the assertion that keeps stage 2 real."""
    from app.intake import qualify

    q = qualify.run("r1", con=pipeline)
    assert "pilot_support_ams" in q["excluded"]
    assert "11.1%" in q["excluded"]["pilot_support_ams"], "excluded WITH the number attached"

    group.run("r1", con=pipeline)
    leaked = pipeline.execute(
        "SELECT COUNT(*) FROM assignments WHERE run_id='r1' AND channel_id='C0AKEL49PEF'"
    ).fetchone()[0]
    assert leaked == 0

    # but the messages are still in the store — excluded, not deleted
    stored = pipeline.execute(
        "SELECT COUNT(*) FROM messages WHERE channel_id='C0AKEL49PEF'").fetchone()[0]
    assert stored == 9


def test_without_qualification_nothing_is_filtered(pipeline):
    """An unqualified run must be visibly UNfiltered rather than silently empty."""
    pipeline.execute("DELETE FROM channel_qualification WHERE run_id='r1'")
    pipeline.commit()
    stats = group.run("r1", con=pipeline)
    assert stats["channels_in_scope"] == "qualify not run"
    leaked = pipeline.execute(
        "SELECT COUNT(*) FROM assignments WHERE run_id='r1' AND channel_id='C0AKEL49PEF'"
    ).fetchone()[0]
    assert leaked > 0


def test_a_tiny_sample_cannot_overrule_an_in_scope_declaration(pipeline):
    """FOUND LIVE. A test channel with 5 substantive messages scored a 0.0% identifier rate,
    the gate excluded it, and a correctly-classified issue had nowhere to go — the tickets
    panel stayed empty while the feed showed `ev=issue`.

    An identifier rate over a handful of messages is noise. Below the minimum sample the human
    declaration stands, or every newly-added channel is killed before it has said anything."""
    from app.intake import qualify

    con = pipeline
    # Keep only a handful of messages in an in-scope channel, none carrying an identifier.
    con.execute("DELETE FROM entities WHERE run_id='r1'")
    con.execute("DELETE FROM messages WHERE channel_id != 'C08T6NLL77H'")
    keep = [r["message_id"] for r in con.execute(
        "SELECT message_id FROM messages WHERE thread_ref IS NULL LIMIT 4")]
    con.execute("DELETE FROM messages WHERE message_id NOT IN (%s)"
                % ",".join("?" * len(keep)), keep)
    con.commit()

    q = qualify.run("r1", con=con)
    ch = q["channels"]["valmo-lm-ams"]
    assert ch["identifier_rate"] == 0.0
    assert ch["in_scope"] is True, "a 4-message sample must not overrule the declaration"
    assert "insufficient_sample" in ch["reason"]

    # and the practical consequence: issues still get created
    group.run("r1", con=con)
    assert con.execute("SELECT COUNT(*) FROM assignments WHERE run_id='r1' "
                       "AND issue_id IS NOT NULL").fetchone()[0] > 0
