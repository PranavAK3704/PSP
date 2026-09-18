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


def test_the_reference_does_not_change_shape_with_the_backend(tmp_path):
    """A ref that reads FILE-3 here and VAL-3 there is one nobody can quote in a conversation."""
    s = sinks.build("file", path=tmp_path / "t.jsonl")
    assert s.create(_draft()).startswith("VAL-")


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





def test_both_sinks_expose_status_urls(tmp_path):
    f = sinks.build("file", path=tmp_path / "t.jsonl")
    f.create(_draft())
    assert _draft().idempotency_key in f.status_urls


# ── the PSP sink ─────────────────────────────────────────────────────────────────────────────

def test_the_psp_sink_reports_a_missing_credential_with_the_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(sinks, "PSP_URL_FILE", tmp_path / "nope.txt")
    with pytest.raises(sinks.SinkError, match="base URL"):
        sinks.build("psp")


def _psp(monkeypatch, tmp_path, cred="bot@meesho.com:pw"):
    monkeypatch.setattr(sinks, "PSP_URL_FILE", tmp_path / "u.txt")
    monkeypatch.setattr(sinks, "PSP_LOGIN_FILE", tmp_path / "l.txt")
    (tmp_path / "u.txt").write_text("https://psp.example")
    (tmp_path / "l.txt").write_text(cred)
    return sinks.build("psp")


def test_it_logs_in_itself_rather_than_holding_a_pasted_token(monkeypatch, tmp_path):
    """PSP tokens expire after 12 hours. A token in a file works today and fails silently
    tomorrow night, which is the worst possible failure for a scheduled pipeline."""
    calls = []

    class R:
        status_code = 200
        def __init__(self, body): self._b = body
        def json(self): return self._b

    def fake_post(url, **kw):
        calls.append(url)
        if url.endswith("/api/auth/login"):
            return R({"token": "fresh"})
        return R({"ok": True, "ref": "VAL-1", "created": True, "status_token": "st"})

    monkeypatch.setattr(sinks.requests, "post", fake_post)
    s = _psp(monkeypatch, tmp_path)
    assert s.create(_draft()) == "VAL-1"
    assert any(u.endswith("/api/auth/login") for u in calls)


def test_an_expired_token_mid_run_is_re_authenticated_once(monkeypatch, tmp_path):
    """The 12h expiry can land in the middle of a backfill. Re-POSTing is safe because the
    server deduplicates on the key, so one retry is correct — and only one."""
    seq = []

    class R:
        def __init__(self, code, body): self.status_code, self._b = code, body
        def json(self): return self._b

    def fake_post(url, **kw):
        seq.append(url.rsplit("/", 1)[-1])
        if url.endswith("/api/auth/login"):
            return R(200, {"token": "t"})
        # First ticket POST 401s (token aged out), the second succeeds.
        return R(200, {"ok": True, "ref": "VAL-3", "created": True}) \
            if seq.count("tickets") > 1 else R(401, {})

    monkeypatch.setattr(sinks.requests, "post", fake_post)
    s = _psp(monkeypatch, tmp_path)
    assert s.create(_draft()) == "VAL-3"
    assert seq.count("login") == 2, "one login at the start, one after the 401"


def test_a_second_refusal_is_reported_not_retried_forever(monkeypatch, tmp_path):
    class R:
        def __init__(self, code, body): self.status_code, self._b = code, body
        def json(self): return self._b

    monkeypatch.setattr(sinks.requests, "post", lambda url, **kw:
                        R(200, {"token": "t"}) if url.endswith("login") else R(403, {}))
    with pytest.raises(sinks.SinkError, match="after re-authenticating"):
        _psp(monkeypatch, tmp_path).create(_draft())


def test_bad_credentials_say_what_to_check(monkeypatch, tmp_path):
    class R:
        status_code = 401
        def json(self): return {}

    monkeypatch.setattr(sinks.requests, "post", lambda *a, **k: R())
    with pytest.raises(sinks.SinkError, match="rejected these credentials"):
        _psp(monkeypatch, tmp_path).create(_draft())


def test_a_malformed_login_file_is_caught_at_construction(monkeypatch, tmp_path):
    with pytest.raises(sinks.SinkError, match="email:password"):
        _psp(monkeypatch, tmp_path, cred="just-an-email")




def test_an_unknown_sink_name_lists_every_real_one():
    with pytest.raises(sinks.SinkError, match="psp"):
        sinks.build("nope")


def test_an_identical_repost_in_one_channel_is_counted_not_duplicated():
    """MEASURED FAILURE on the live channel: the same sentence posted twice, forty minutes
    apart, by one person became two tickets. Dedupe skipped same-channel pairs entirely and
    deferred them to grouping — but grouping joins on IDENTIFIERS, and these messages had none,
    so they fell through both stages. Recurrence is the signal the design exists to preserve."""
    from app.intake import dedupe
    assert dedupe.SAME_CHANNEL_SIMILARITY > dedupe.TEXT_SIMILARITY, \
        "a same-channel pair must be held to a HIGHER bar — splitting is the safer default"
    assert dedupe.SAME_CHANNEL_SIMILARITY >= 0.9, \
        "merely-similar must not merge: two different problems from one person in one hour " \
        "is ordinary, and merging those is the worse error"
