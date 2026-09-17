"""The KPI funnel — where volume goes, tier by tier.

The funnel is what decides whether an expensive tier gets built: a tier only has to handle what
escapes the cheap tiers above it. So the arithmetic has to be right, and the rows have to
reconcile with each other rather than each being computed independently.
"""
from __future__ import annotations

import pytest

from app.intake import (
    classify, evaluate, evidence, extract, group, loadstage, noise, qualify, register, store,
)

FIXTURES = "data/intake/fixtures"


@pytest.fixture()
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("INTAKE_DB", str(tmp_path / "t.db"))
    monkeypatch.setattr(store, "_DB", None, raising=False)
    rid = "funnel-test"
    loadstage.load(FIXTURES, run_id=rid, skip_validate=True)
    for fn in (noise.run, extract.run, evidence.run, qualify.run, group.run, register.run,
               classify.run):
        fn(rid)
    con = store.connect()
    yield rid, con
    con.close()


def test_each_tier_only_sees_what_escaped_the_one_above(run):
    """The rows must chain. If a tier reports more input than the previous tier let through,
    something is counting the whole corpus instead of the remainder."""
    rid, con = run
    tiers = evaluate._funnel(con, rid)["tiers"]
    for prev, nxt in zip(tiers, tiers[1:]):
        assert nxt["in"] == prev["escaped"], f"{nxt['tier']} sees {nxt['in']}, but " \
                                             f"{prev['tier']} only let {prev['escaped']} through"


def test_grouping_is_reported_as_a_collapse_not_a_filter(run):
    """Several messages becoming one issue is not volume being dropped. Labelling it a filter
    would make the pipeline look like it discards messages it does not."""
    rid, con = run
    g = next(t for t in evaluate._funnel(con, rid)["tiers"] if t["tier"].startswith("1 "))
    assert "collapsed_into" in g and "resolved" not in g
    assert g["collapsed_into"] <= g["in"]


def test_no_deterministic_tier_costs_money(run):
    rid, con = run
    for t in evaluate._funnel(con, rid)["tiers"]:
        assert t["cost_usd"] == 0.0


def test_coverage_and_novel_rate_are_complementary(run):
    rid, con = run
    f = evaluate._funnel(con, rid)
    assert f["disposition_coverage"] + f["novel_rate"] == pytest.approx(1.0, abs=1e-3)


def test_an_unclassified_run_is_not_reported_as_full_coverage(run):
    """If the exemplar index is missing, classify leaves intent NULL. Reporting that as 100%
    coverage — nothing was marked NOVEL, after all — would be the most dangerous number here."""
    rid, con = run
    con.execute("UPDATE issues SET intent=NULL WHERE run_id=?", (rid,))
    con.commit()
    f = evaluate._funnel(con, rid)
    assert f["classifier_off"] is True
    assert f["disposition_coverage"] == 0.0


def test_the_human_row_counts_what_is_still_being_asked(run, tmp_path, monkeypatch):
    """A NOVEL item a person already answered is not still a question, even though a re-run
    gives it a fresh issue_id — the label is keyed on the text."""
    from app.intake import labels
    rid, con = run
    monkeypatch.setattr(labels, "STORE", tmp_path / "c.jsonl")

    before = evaluate._funnel(con, rid)["awaiting_a_human"]
    assert before > 0, "fixtures should leave something unclassified to ask about"

    text = con.execute(
        "SELECT m.text FROM issues i JOIN messages m "
        "ON m.channel_id=i.anchor_channel_id AND m.message_id=i.anchor_message_id "
        "WHERE i.run_id=? AND i.intent='NOVEL' LIMIT 1", (rid,)).fetchone()[0]
    labels.confirm(text, "label", disposition="something_new", confirmed_by="tester")

    assert evaluate._funnel(con, rid)["awaiting_a_human"] == before - 1


def test_the_channel_rollup_reconciles_with_the_funnel(run):
    """Both views read the same store, so their totals must agree — a panel that disagrees
    with the pipeline is worse than no panel."""
    from app.intake import evaluate, rollup
    rid, con = run
    rows = rollup.channel_rollup(con, rid)
    assert rows, "the fixtures span three channels"
    assert sum(r["messages"] for r in rows) == \
        con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert sum(r["issues"] for r in rows) == \
        con.execute("SELECT COUNT(*) FROM issues WHERE run_id=?", (rid,)).fetchone()[0]
    assert sum(r["gated"] for r in rows) == \
        evaluate._funnel(con, rid)["tiers"][0]["resolved"]


def test_an_excluded_channel_is_marked_with_its_reason(run):
    from app.intake import rollup
    rid, con = run
    rows = rollup.channel_rollup(con, rid)
    excluded = [r for r in rows if r["qualified"] is False]
    for r in excluded:
        assert r["reason"], f"{r['name']} is excluded but says nothing about why"
