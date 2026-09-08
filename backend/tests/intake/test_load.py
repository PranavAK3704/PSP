"""Stage 1. The invariants here are the ones that silently destroy data if they lapse."""
from __future__ import annotations

import json
import shutil

import pytest

from app.intake import loadstage, store

pytestmark = pytest.mark.usefixtures("intake_db")


@pytest.fixture()
def raw(tmp_path, fixtures_dir):
    """A writable copy of the committed fixture, day-partitioned as validate.js expects."""
    d = tmp_path / "raw"
    d.mkdir()
    shutil.copy(fixtures_dir / "2026-09-04.ndjson", d / "2026-09-04.ndjson")
    return d


def _first(raw_dir):
    return json.loads((raw_dir / "2026-09-04.ndjson").read_text().split("\n")[0])


def _write_one(raw_dir, rec):
    (raw_dir / "2026-09-04.ndjson").write_text(json.dumps(rec) + "\n")


# ── the contract gate ───────────────────────────────────────────────────────────────────────
needs_node = pytest.mark.skipif(
    not loadstage.validator_available(),
    reason="tools/validate.js needs node on PATH; the JS validator is the exporter's own "
           "contract gate and is deliberately not reimplemented in Python")


@needs_node
def test_validator_passes_on_the_committed_fixture(raw):
    assert "PASS" in loadstage.run_validator(raw)


@needs_node
def test_validator_blocks_utc_z_and_nothing_is_loaded(raw):
    """field-map.md is explicit: ts_iso is IST +05:30 and UTC 'Z' fails. If the gate ever
    stops enforcing that, every latency number silently shifts by 5.5 hours."""
    rec = _first(raw)
    rec["ts_iso"] = "2026-09-04T00:46:11Z"
    _write_one(raw, rec)

    with pytest.raises(loadstage.ContractError, match="ts_iso"):
        loadstage.load(raw, run_id="r1")

    con = store.connect()
    assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
    con.close()


# ── load-time invariants ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [1788482771.760339, 1788482771, None, ["1788482771.760339"]])
def test_non_string_message_id_is_rejected_loudly(raw, bad):
    """A float cast loses precision and breaks every thread_ref join. Rejecting is the point."""
    rec = _first(raw)
    rec["message_id"] = bad
    _write_one(raw, rec)
    with pytest.raises(loadstage.ContractError, match="message_id must be a string"):
        loadstage.load(raw, run_id="r1", skip_validate=True)


def test_missing_v11_key_is_rejected(raw):
    rec = _first(raw)
    rec.pop("permalink")
    _write_one(raw, rec)
    with pytest.raises(loadstage.ContractError, match="missing keys: permalink"):
        loadstage.load(raw, run_id="r1", skip_validate=True)


def test_a_file_path_is_rejected_because_the_gate_needs_the_corpus(raw):
    """validate.js's orphan-thread_ref check is corpus-level, so the unit of validation is a
    directory. Handing it one file would fail every reply whose parent is in another day."""
    with pytest.raises(loadstage.ContractError, match="not a directory"):
        loadstage.load(raw / "2026-09-04.ndjson", run_id="r1", skip_validate=True)


# ── idempotency and immutability ────────────────────────────────────────────────────────────
def test_loading_twice_is_a_no_op(raw):
    s1 = loadstage.load(raw, run_id="r1", skip_validate=True)
    s2 = loadstage.load(raw, run_id="r1", skip_validate=True)

    assert s1["inserted"] == 3
    assert s2["inserted"] == 0
    assert s2["already_present"] == 3

    con = store.connect()
    assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3
    con.close()


def test_thread_ref_joins_to_its_parent_at_full_precision(raw):
    loadstage.load(raw, run_id="r1", skip_validate=True)
    con = store.connect()
    rows = con.execute(
        "SELECT c.message_id AS child, p.message_id AS parent FROM messages c "
        "JOIN messages p ON p.channel_id = c.channel_id AND p.message_id = c.thread_ref "
        "WHERE c.thread_ref IS NOT NULL").fetchall()
    con.close()
    assert [(r["child"], r["parent"]) for r in rows] == [
        ("1788483900.114201", "1788482771.760339")]


def test_a_changed_re_export_is_reported_and_the_row_is_not_overwritten(raw):
    """messages is immutable, but a key reappearing with different content is a data problem —
    it gets counted, not swallowed by INSERT OR IGNORE."""
    loadstage.load(raw, run_id="r1", skip_validate=True)
    rec = _first(raw)
    original_text = rec["text"]
    rec["text"] = original_text + " (changed upstream)"
    _write_one(raw, rec)

    s = loadstage.load(raw, run_id="r2", skip_validate=True)

    assert s["conflicts"] == [f"{rec['channel_id']}/{rec['message_id']}"]
    con = store.connect()
    assert con.execute("SELECT text FROM messages WHERE message_id = ?",
                       (rec["message_id"],)).fetchone()[0] == original_text
    assert con.execute("SELECT status FROM stage_runs WHERE run_id='r2'").fetchone()[0] \
        == "conflicts"
    con.close()


# ── the thing the contract gate cannot see ──────────────────────────────────────────────────
def test_attachment_coverage_is_recorded(raw):
    """An export whose tool could not read files emits attachments: [] and has_media: false
    everywhere, and PASSES the validator — the agreement check is satisfied by two falsehoods.
    Coverage is surfaced as a number so that emptiness is visible before a noise gate drops
    screenshot-only issues."""
    s = loadstage.load(raw, run_id="r1", skip_validate=True)
    assert s["records_with_attachments"] == 2
    assert s["attachment_coverage"] == pytest.approx(2 / 3, abs=1e-4)

    rec = _first(raw)
    rec["attachments"], rec["has_media"] = [], False
    _write_one(raw, rec)
    blind = loadstage.load(raw, run_id="r3", skip_validate=True)
    assert blind["attachment_coverage"] == 0.0


def test_empty_text_with_attachment_is_loaded(raw):
    """5% of lm-ams records are empty text with a screenshot — the content is only in the file.
    Stage 1 must keep them; stage 3 must never gate them."""
    loadstage.load(raw, run_id="r1", skip_validate=True)
    con = store.connect()
    n = con.execute("SELECT COUNT(*) FROM messages "
                    "WHERE TRIM(text) = '' AND has_media = 1").fetchone()[0]
    con.close()
    assert n == 1
