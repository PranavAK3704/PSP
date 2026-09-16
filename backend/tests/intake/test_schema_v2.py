"""Schema v2 — the contract gate, once it stops meaning "a Slack message".

v1.1 does not describe a message; it describes a SLACK message. Three of its checks are pure
Slack and would reject a valid WhatsApp or email record. They are also the checks that caught
real bugs, so v2 keeps every one of them FOR SLACK and dispatches on `source_system` rather
than weakening them for everybody.

These tests run the real validator through node, because the gate is only worth what it
actually rejects.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
VALIDATE = ROOT / "tools" / "validate.js"
SCHEMA = ROOT / "tools" / "schema.json"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

E = 1788511676.054669


def _stamp(e: float) -> str:
    return datetime.fromtimestamp(e + 19800, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "+05:30"


def rec(**over) -> dict:
    base = dict(
        schema_version="2", source="test", source_system="slack", workspace_id="W",
        channel_id="C1", channel_name="c", message_id="1788511676.054669",
        ts_epoch=E, ts_iso=_stamp(E), author_id="U1", author_name=None, author_email=None,
        author_is_bot=False, text="x", subtype=None, thread_ref=None, is_thread_parent=False,
        reply_count=0, mentions=[], channel_mention=False, subteam_mentions=[], attachments=[],
        has_media=False, reactions=[],
        permalink="https://meesho.slack.com/archives/C1/p1788511676054669",
        edited_ts=None, fetched_at=_stamp(E))
    base.update(over)
    return {k: v for k, v in base.items() if v is not ...}


def validate(tmp_path, *records) -> tuple[int, str]:
    d = tmp_path / "corpus"
    d.mkdir(exist_ok=True)
    (d / "2026-09-04.ndjson").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    p = subprocess.run(["node", str(VALIDATE), str(d), "--schema", str(SCHEMA)],
                       capture_output=True, text=True)
    return p.returncode, p.stdout


def errors(out: str) -> list[str]:
    return [line.strip()[2:] for line in out.splitlines() if line.strip().startswith("x ")]


# ── the point of v2 ──────────────────────────────────────────────────────────────────────────

def test_three_surfaces_validate_in_one_corpus(tmp_path):
    code, out = validate(
        tmp_path,
        rec(),
        rec(source_system="whatsapp", message_id="wamid.HBgMOTE5ODc2NTQzMjEwFQIAEhgg",
            ts_epoch=E, permalink=None),
        rec(source_system="email", message_id="CAF7x9y2@mail.meesho.com", ts_epoch=E,
            permalink="https://mail.google.com/mail/u/0/#inbox/abc"))
    assert code == 0, out


def test_v1_1_records_still_pass_untouched(tmp_path):
    """The committed fixtures are v1.1 and must not need regenerating for v2 to exist."""
    r = rec(schema_version="1.1")
    del r["source_system"]
    code, out = validate(tmp_path, r)
    assert code == 0, out


# ── what it must still reject ────────────────────────────────────────────────────────────────

def test_a_v2_record_must_declare_its_surface(tmp_path):
    """Defaulting to slack would let an unlabelled WhatsApp message through with a Slack-shaped
    idempotency key, and the retry guarantee would stop holding with nothing failing."""
    r = rec()
    del r["source_system"]
    code, out = validate(tmp_path, r)
    assert code == 1
    assert "has no format rules" in out


def test_an_unknown_surface_says_how_to_add_it(tmp_path):
    code, out = validate(tmp_path, rec(source_system="telegram"))
    assert code == 1
    assert "add it to SURFACES" in out and "slack, whatsapp, email" in out


def test_one_bad_record_does_not_hide_the_rest_of_the_corpus(tmp_path):
    """REGRESSION. The per-record loop runs at module top level, so an early `return` exits the
    whole module — no summary, no errors, exit 0. A single unlabelled record silently passed the
    entire corpus and the gate went quiet, which is worse than anything it was meant to catch.
    """
    unlabelled = rec()
    del unlabelled["source_system"]
    also_broken = rec(ts_epoch=1788511676.0)          # truncated — a real bug, AFTER the bad row
    code, out = validate(tmp_path, unlabelled, also_broken)
    assert code == 1
    assert len(errors(out)) >= 2, f"the second record's bug was swallowed: {out}"
    assert any("drift" in e for e in errors(out))


def test_slack_keeps_every_check_that_caught_a_real_bug(tmp_path):
    for name, over, needle in [
            ("float-cast key", {"message_id": 1788511676.054669}, "must be a string"),
            ("truncated ts", {"ts_epoch": 1788511676.0}, "drift"),
            ("permalink for another message",
             {"permalink": "https://meesho.slack.com/archives/C1/p1700000000000000"},
             "does not point at"),
    ]:
        code, out = validate(tmp_path, rec(**over))
        assert code == 1, f"{name} was accepted"
        assert needle in out, f"{name}: {out}"


def test_a_surface_cannot_borrow_another_surfaces_id_format(tmp_path):
    code, out = validate(tmp_path, rec(source_system="whatsapp",
                                       message_id="1788511676.054669", permalink=None))
    assert code == 1 and "does not match the whatsapp form" in out


def test_a_surface_whose_id_carries_no_time_must_still_report_one(tmp_path):
    """Slack's id encodes the send time, so ts_epoch can be checked against it. Email's does
    not — which makes ts_epoch the only record of when the message was sent, not a field that
    can be skipped."""
    code, out = validate(tmp_path, rec(source_system="email", message_id="a@b.com",
                                       ts_epoch=0, permalink=None))
    assert code == 1 and "carry no time" in out
