"""The fixtures are the measurement. These tests read expected_*.json and assert against it.

Data-driven on purpose: the expected token sets live in a committed JSON file rather than in
Python, so regenerating the fixtures and changing what they assert are the same edit. A
fixture whose expectations drift silently is worse than no fixture.
"""
from __future__ import annotations

import json
import shutil

import pytest

from app.intake import extract as istage
from app.intake import loadstage, store

pytestmark = pytest.mark.usefixtures("intake_db")


@pytest.fixture()
def loaded(tmp_path, fixtures_dir):
    """The whole fixture corpus, loaded and extracted. validate.js runs if node is present."""
    raw = tmp_path / "raw"
    raw.mkdir()
    for f in sorted(fixtures_dir.glob("*.ndjson")):
        shutil.copy(f, raw / f.name)
    loadstage.load(raw, run_id="r1", skip_validate=not loadstage.validator_available())
    istage.run("r1")
    con = store.connect()
    yield con
    con.close()


@pytest.fixture(scope="session")
def expected_tier_a(fixtures_dir):
    return json.loads((fixtures_dir / "expected_tier_a.json").read_text())


@pytest.fixture(scope="session")
def expected_cross_post(fixtures_dir):
    return json.loads((fixtures_dir / "expected_cross_post.json").read_text())


def _codes(con, message_id, tier=None):
    q = ("SELECT value FROM entities WHERE run_id='r1' AND message_id=? AND kind='dc_code'"
         + (" AND tier=?" if tier else ""))
    args = (message_id, tier) if tier else (message_id,)
    return sorted(r[0] for r in con.execute(q, args))


def test_the_corpus_conforms_to_the_contract(fixtures_dir):
    if not loadstage.validator_available():
        pytest.skip("node not on PATH")
    out = loadstage.run_validator(fixtures_dir)
    assert "PASS" in out
    # A generated fixture that drifts out of contract is the failure mode this guards.
    assert "WARNINGS" not in out or "joins" not in out


def test_every_message_yields_its_exact_token_set(loaded, expected_tier_a):
    """THE assertion this project exists to make. Per message, both tiers, exact sets."""
    mismatches = {}
    for mid, want in expected_tier_a["by_message"].items():
        got = _codes(loaded, mid)
        if got != want:
            mismatches[mid] = {"want": want, "got": got}
    assert mismatches == {}


def test_the_measurement_reproduces(loaded, expected_tier_a):
    """7 of 18 substantive top-level messages are code-bearing, yielding 10 distinct codes."""
    by_msg = expected_tier_a["by_message"]
    bearing = [m for m in by_msg if _codes(loaded, m)]
    assert len(by_msg) == expected_tier_a["_substantive_top_level"] == 18
    assert len(bearing) == expected_tier_a["_code_bearing"] == 7

    union = sorted({c for m in by_msg for c in _codes(loaded, m)})
    assert union == expected_tier_a["_expected_union"]
    assert len(union) == 10


def test_zero_false_positives_on_the_clean_messages(loaded, expected_tier_a):
    """The 11 non-bearing messages are the precision half — the half a bad regex fails.
    They include the (?i) trap, 'DC CODE IS MISSING', '342 Tids', and bare ops acronyms."""
    fp = {m: _codes(loaded, m)
          for m, want in expected_tier_a["by_message"].items() if not want and _codes(loaded, m)}
    assert fp == {}


def test_342_is_present_in_the_corpus_and_never_extracted(loaded):
    """Guards against the case passing because the message went missing."""
    row = loaded.execute(
        "SELECT channel_id, message_id FROM messages WHERE text LIKE '%342 Tids%'").fetchone()
    assert row is not None, "the 342 message must actually be in the fixture"
    assert _codes(loaded, row["message_id"]) == []


def test_the_cross_post_is_two_messages_in_two_channels(loaded, expected_cross_post):
    """Same author, near-identical text, both in-scope channels, threads forked. Per-channel
    dedupe would make two tickets of this on day one."""
    a, b = expected_cross_post["_cross_channel_duplicate"]
    ra = loaded.execute("SELECT * FROM messages WHERE message_id=?", (a,)).fetchone()
    rb = loaded.execute("SELECT * FROM messages WHERE message_id=?", (b,)).fetchone()

    assert ra["channel_id"] != rb["channel_id"]
    assert ra["author_id"] == rb["author_id"]
    assert {ra["channel_name"], rb["channel_name"]} == {"valmo-firefighters", "valmo-lm-ams"}
    # the threads fork — 4 replies one side, 2 the other
    assert {ra["reply_count"], rb["reply_count"]} == {4, 2}
    assert abs(rb["ts_epoch"] - ra["ts_epoch"]) == pytest.approx(
        expected_cross_post["_seconds_apart"], abs=0.01)
    # and MX1 resolves on BOTH sides, which is what makes them joinable at all
    assert _codes(loaded, a) == _codes(loaded, b) == ["MX1"]


def test_mx1_is_a_tier_b_hit_so_tier_b_is_on_the_dedupe_critical_path(loaded, expected_cross_post):
    """MX1 carries no label, so without a registry this duplicate is invisible."""
    a = expected_cross_post["_cross_channel_duplicate"][0]
    assert _codes(loaded, a, tier="B") == ["MX1"]
    assert _codes(loaded, a, tier="A") == []


def test_joins_are_present_for_the_noise_gate(loaded):
    """3-20% of a real day, not the 40% originally assumed. Stage 3 drops by subtype, which
    cannot be tested without them in the corpus."""
    n = loaded.execute("SELECT COUNT(*) FROM messages WHERE subtype IN "
                       "('channel_join','channel_leave')").fetchone()[0]
    assert n == 3


def test_the_weather_borderline_message_exists(loaded):
    """One real message leads with festival manpower absenteeism and mentions rain second.
    Stage 3 must report it as borderline rather than silently counting it either way."""
    row = loaded.execute("SELECT text FROM messages WHERE text LIKE '%festival manpower%'").fetchone()
    assert row is not None
    assert "rain" in row["text"].lower()
    assert row["text"].lower().index("festival") < row["text"].lower().index("rain")
