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


# ── acknowledgement ──────────────────────────────────────────────────────────────────────────

def test_acknowledgement_is_off_unless_asked():
    """This is a server that can mail real partners with nobody watching — the one thing here
    with consequences outside the building."""
    assert intake_poller._acknowledge("r") == 0


def test_it_refuses_to_send_without_a_public_url(monkeypatch):
    """A relative status link in an email is worse than no email: it promises somewhere to look
    and then does not go there."""
    monkeypatch.setenv("INTAKE_NOTIFY", "email")
    monkeypatch.delenv("PSP_PUBLIC_URL", raising=False)
    assert intake_poller._acknowledge("r") == 0


def test_the_ledger_survives_a_restart(tmp_path, monkeypatch):
    """THE BUG THIS FIXES. The old ledger was a table in the intake SQLite, which sits on
    Render's ephemeral disk — so every restart forgot every send and mailed the same partner
    again. That is precisely the failure the ledger exists to prevent, firing on every deploy.
    Acknowledgement state now lives on the durable ticket."""
    rec = intake_api.create_ticket_record(
        {"idempotency_key": "k1", "title": "t", "source_id": "C1/1.1"})
    assert intake_api.unacknowledged("email"), "a fresh ticket is owed an acknowledgement"

    intake_api.mark_acknowledged("k1", channel="email", recipient="p@meesho.com")
    assert not [t for t in intake_api.unacknowledged("email")
                if t["idempotency_key"] == "k1"]

    # A restart wipes the intake SQLite; the durable ticket is what remains, and it remembers.
    t = [x for x in intake_api._load()["tickets"] if x["idempotency_key"] == "k1"][0]
    assert t["acknowledged_at"] and t["acknowledged_to"] == "p@meesho.com"
    assert rec["created"] is True


def test_a_failed_send_is_recorded_but_retried(tmp_path):
    """Marking a failure as 'acknowledged' would leave a partner believing nobody read their
    message — the exact failure this project exists to fix."""
    intake_api.create_ticket_record({"idempotency_key": "k2", "title": "t"})
    intake_api.mark_acknowledged("k2", channel="email", recipient=None, error="smtp down")
    still = [t for t in intake_api.unacknowledged("email") if t["idempotency_key"] == "k2"]
    assert still, "a failed send must remain outstanding"
    assert still[0]["acknowledge_error"] == "smtp down"


# ── concurrency and cost, from the deployment audit ──────────────────────────────────────────

def test_two_writers_do_not_lose_each_other_s_changes():
    """The poller thread creates tickets while request threads patch them, and both are
    read-modify-write on one JSON document. Unsynchronized, one silently wins."""
    import threading
    done = []

    def create(i):
        done.append(intake_api.create_ticket_record(
            {"idempotency_key": f"k{i}", "title": f"t{i}"}))

    threads = [threading.Thread(target=create, args=(i,)) for i in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()

    tickets = intake_api._load()["tickets"]
    assert len(tickets) == 12, "a concurrent create was lost"
    refs = [t["ref"] for t in tickets]
    assert len(set(refs)) == 12, f"duplicate references: {refs}"


def test_the_reference_comes_from_a_counter_not_from_the_tickets_present():
    """Two wrong answers were tried first. `len()+1` makes racing creates agree on the same
    number; "one past the highest" reuses a reference after a delete. Both end with two
    different tickets sharing the identifier people quote."""
    assert intake_api._next_ref({"tickets": [{"ref": "VAL-7"}, {"ref": "VAL-3"}]}) == 8
    assert intake_api._next_ref({"tickets": []}) == 1
    assert intake_api._next_ref({"tickets": [{"ref": "junk"}, {"ref": "VAL-2"}]}) == 3
    # Once stored, the counter is the truth and the tickets present are irrelevant.
    d = {"tickets": [], "next_ref": 42}
    assert intake_api._next_ref(d) == 42 and d["next_ref"] == 43


def test_status_reports_a_dead_poller_as_not_running():
    """A flag set once says 'running' forever, including after the thread has died — worse than
    reporting nothing."""
    assert intake_poller.status()["running"] is False


def test_a_cold_start_is_bounded(monkeypatch):
    """Without a bound the first poll after every deploy re-reads the channel's entire history.
    Render's free plan explicitly reserves the right to suspend a service that initiates an
    uncommonly high volume of external traffic."""
    assert intake_poller.COLD_START_DAYS <= 30
    seen = {}

    class FakeReader:
        def __init__(self, **kw): pass
        def fetch(self, ch, oldest=None):
            seen[ch] = oldest
            return []

    monkeypatch.setattr(intake_poller, "_watermarks", lambda: {})
    monkeypatch.setattr(intake_poller, "_save_watermarks", lambda w: None)
    from app.intake import slack_source as ss
    monkeypatch.setattr(ss, "SlackReader", FakeReader)
    import time as _t
    try:
        intake_poller._poll_once(["C1"], __import__("pathlib").Path("/tmp/x"), "r")
    except Exception:
        pass
    assert seen.get("C1"), "fetch was called with no oldest cursor"
    assert float(seen["C1"]) > _t.time() - (intake_poller.COLD_START_DAYS + 1) * 86400
