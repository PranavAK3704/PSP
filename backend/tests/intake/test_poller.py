"""The in-process Slack poller — the thing that makes the DEPLOYED app self-sufficient.

Without it, tickets only appear when somebody runs the CLI on a laptop, so the deployed register
looks broken every time a message is posted when in fact nothing was listening.
"""
from __future__ import annotations

import pytest

from app import intake_api, intake_poller
from app.intake import slack_source


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("PSP_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    for k in ("SLACK_BOT_TOKEN", "INTAKE_CHANNELS", "INTAKE_POLL_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(intake_poller, "_thread", None)


# ── it stays off unless asked ────────────────────────────────────────────────────────────────

def test_it_does_not_start_without_a_token():
    """A deployment that silently begins reading a Slack workspace because a default was left
    on would be a bad surprise."""
    assert "SLACK_BOT_TOKEN is unset" in intake_poller.start()


def test_it_does_not_start_without_channels(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    assert "INTAKE_CHANNELS is unset" in intake_poller.start()


def test_it_can_be_held_off_with_everything_configured(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setenv("INTAKE_CHANNELS", "C1")
    monkeypatch.setenv("INTAKE_POLL_ENABLED", "0")
    assert "disabled" in intake_poller.start()


def test_the_interval_is_floored(monkeypatch):
    """A poll faster than Slack's tier-3 limit gets rate-limited, which looks like an outage."""
    started = {}
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setenv("INTAKE_CHANNELS", "C1")
    monkeypatch.setenv("INTAKE_POLL_SECONDS", "1")

    class FakeThread:
        def __init__(self, *a, **kw): started["every"] = kw["args"][1]
        def start(self): pass
        def is_alive(self): return True

    monkeypatch.setattr(intake_poller.threading, "Thread", FakeThread)
    intake_poller.start()
    assert started["every"] >= 15.0


# ── the token comes from the environment in a deployment ─────────────────────────────────────

def test_the_token_is_read_from_the_environment(monkeypatch, tmp_path):
    """.dockerignore excludes backend/data/*.txt, so the deployed image holds no token file at
    all. A file-only reader could never work on Render."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-from-env")
    assert slack_source.read_token() == "xoxb-from-env"


def test_a_missing_token_names_both_places_to_look(monkeypatch):
    monkeypatch.setattr(slack_source, "_BACKEND", __import__("pathlib").Path("/nonexistent"))
    with pytest.raises(slack_source.SlackError) as e:
        slack_source.read_token()
    assert "SLACK_BOT_TOKEN" in str(e.value) and "does not exist" in str(e.value)


# ── creating a ticket means the same thing over HTTP and in-process ──────────────────────────

def test_the_in_process_sink_shares_the_route_s_idempotency():
    """Both paths land in create_ticket_record, so the check cannot be true over HTTP and false
    in-process — the kind of divergence that produces duplicates nobody can explain."""
    from app.intake_sink import InProcessTicketSink
    from app.intake import emit

    d = emit.TicketDraft(
        idempotency_key="abc", source_system="slack", source_id="C1/1.1",
        source_permalink=None, title="t", description="d", raiser="r", dc_code=None,
        intent="technical_issue")

    s = InProcessTicketSink()
    first, again = s.create(d), s.create(d)
    assert first == again
    assert s.created == 1 and s.already_existed == 1
    assert intake_api.create_ticket_record({"idempotency_key": "abc"})["created"] is False


def test_status_distinguishes_never_ran_from_found_nothing():
    """On the page, "no new tickets" and "nothing is listening" look identical — and the second
    is the one worth knowing about."""
    st = intake_poller.status()
    assert st["running"] is False and st["polls"] == 0
    assert "last_error" in st
