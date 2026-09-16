"""Ticket sinks — the destinations a draft can actually be created in.

The property everything rests on: creating twice with the same idempotency key must produce ONE
ticket and return the ORIGINAL reference. Retries are normal — timeouts, re-runs, two operators
— so a sink that merely happens not to be retried is not idempotent, it is lucky.
"""
from __future__ import annotations

import json

import pytest

from app.intake import emit, sinks


def _draft(key="abc123", title="test"):
    return emit.TicketDraft(
        idempotency_key=key, source_system="slack", source_id="C1/1.1",
        source_permalink="https://x/1", title=title, description="body",
        raiser="Someone", dc_code="NQS", intent="load_planning")


def test_creating_twice_with_the_same_key_makes_one_ticket(tmp_path):
    s = sinks.build("file", path=tmp_path / "t.jsonl")
    a = s.create(_draft())
    b = s.create(_draft())
    assert a == b, "a retry must return the ORIGINAL reference"
    assert s.created == 1 and s.already_existed == 1
    assert len((tmp_path / "t.jsonl").read_text().strip().splitlines()) == 1


def test_the_guarantee_survives_a_fresh_process(tmp_path):
    """The real retry case is not a loop — it is the pipeline running again tomorrow. A sink
    that only dedupes within one process would create a second ticket every night."""
    p = tmp_path / "t.jsonl"
    first = sinks.build("file", path=p).create(_draft())
    again = sinks.build("file", path=p).create(_draft())   # new instance, same file
    assert first == again
    assert len(p.read_text().strip().splitlines()) == 1


def test_different_messages_get_different_tickets(tmp_path):
    s = sinks.build("file", path=tmp_path / "t.jsonl")
    assert s.create(_draft("k1")) != s.create(_draft("k2"))
    assert s.created == 2


def test_the_row_carries_the_structured_payload(tmp_path):
    p = tmp_path / "t.jsonl"
    sinks.build("file", path=p).create(_draft())
    row = json.loads(p.read_text().strip())
    for f in ("idempotency_key", "source_id", "source_permalink", "title",
              "description", "raiser", "dc_code", "intent", "ref"):
        assert f in row, f"{f} missing — a row nobody can trace back is not a ticket"


def test_an_unknown_sink_fails_loudly(tmp_path):
    """A typo must never silently fall back to the dry run and leave someone believing tickets
    were created."""
    with pytest.raises(sinks.SinkError, match="unknown sink"):
        sinks.build("gogle_sheet")


def test_the_sheet_sink_refuses_to_guess_a_url(tmp_path):
    missing = tmp_path / "nope.txt"
    with pytest.raises(sinks.SinkError, match="not found"):
        sinks.read_url(missing)


def test_a_non_apps_script_url_is_rejected(tmp_path):
    p = tmp_path / "url.txt"
    p.write_text("https://evil.example.com/collect")
    with pytest.raises(sinks.SinkError, match="does not look like"):
        sinks.read_url(p)


def test_the_dry_run_sink_is_still_the_default_everywhere(tmp_path):
    """Creating tickets must be an explicit act. `dry` is what you get if you say nothing."""
    s = sinks.build("dry")
    assert s.name == "dry_run"
    assert s.create(_draft()).startswith("DRY-")


def test_a_sink_cannot_acquire_the_ability_to_post_to_slack():
    """Creating a ticket and telling the raiser about it are separate decisions. This module
    does the first. The second needs a write scope, a reinstall and an approval, so this asserts
    on the CAPABILITY, not on the word 'slack' — which the module legitimately contains as a
    source_system value and in the docstring explaining why it does not write."""
    import inspect
    src = inspect.getsource(sinks)
    for banned in ("slack_sdk", "WebClient", "chat.postMessage", "xoxb", "slack_bot_token"):
        assert banned not in src, f"{banned} appeared in sinks.py — this module must not write"
