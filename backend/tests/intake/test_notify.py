"""Acknowledgement — the first thing in this pipeline that reaches a real person.

Almost every test here is about NOT sending. The live dashboard re-runs the pipeline every few
seconds, so the difference between "idempotent" and "spams a partner every five seconds" is one
table lookup.
"""
from __future__ import annotations

import pytest

from app.intake import (classify, dedupe, emit, evidence, extract, group, loadstage, noise,
                        notify, qualify, register, store)


class Recorder(notify.EmailNotifier):
    """A notifier that records instead of sending, but is otherwise live (dry_run False)."""

    def __init__(self, **kw):
        super().__init__(notify.SmtpConfig(username="u", password="p"), dry_run=False, **kw)
        self.outbox: list[dict] = []

    def send(self, *, to, subject, text, html):
        target = self.redirect_to or to
        self.outbox.append({"to": target, "subject": subject, "text": text})
        return target


@pytest.fixture()
def run(tmp_path, monkeypatch):
    monkeypatch.setenv("INTAKE_DB", str(tmp_path / "t.db"))
    rid = "ack-test"
    loadstage.load("data/intake/fixtures", run_id=rid, skip_validate=True)
    con = store.connect()
    for fn in (noise.run, extract.run, evidence.run, qualify.run, group.run, register.run,
               classify.run, dedupe.run):
        fn(rid, con=con)
    sink = emit.DryRunTicketSink()
    emit.run(rid, con=con, sink=sink)
    keys = [r[0] for r in con.execute(
        "SELECT idempotency_key FROM ticket_drafts WHERE run_id=? AND suppressed=0", (rid,))]
    urls = {k: f"https://x/exec?t={k[:8]}" for k in keys}
    yield rid, con, urls
    con.close()


def test_a_re_run_sends_nothing(run):
    """THE test. The dashboard polls every few seconds; without this the same partner is
    emailed every few seconds."""
    rid, con, urls = run
    n = Recorder(redirect_to="demo@meesho.com")
    first = notify.run(rid, n, con=con, status_urls=urls)
    assert first["sent"] > 0, "fixtures should produce at least one acknowledgeable ticket"

    second = notify.run(rid, n, con=con, status_urls=urls)
    assert second["sent"] == 0
    assert second["already_acknowledged"] == first["sent"]
    assert len(n.outbox) == first["sent"], "the second pass must not have added to the outbox"


def test_a_dry_run_records_nothing_so_it_cannot_block_the_real_send(run):
    rid, con, urls = run
    dry = notify.EmailNotifier(dry_run=True, redirect_to="demo@meesho.com")
    notify.run(rid, dry, con=con, status_urls=urls)
    assert con.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0

    live = Recorder(redirect_to="demo@meesho.com")
    assert notify.run(rid, live, con=con, status_urls=urls)["sent"] > 0


def test_with_no_allowlist_and_no_redirect_it_writes_to_nobody(run):
    """Fail closed. An operator who wired real sending without saying who may receive it has
    made a mistake, and the safe reading of that mistake is 'nobody'."""
    rid, con, urls = run
    n = Recorder()                       # no redirect, no allowlist
    stats = notify.run(rid, n, con=con, status_urls=urls)
    assert stats["sent"] == 0 and n.outbox == []
    assert any("refusing to write" in why for why in stats["reasons"])


def test_the_redirect_captures_everything_and_records_who_it_was_really_for(run):
    """Demoing against a live channel without this mails actual partners."""
    rid, con, urls = run
    n = Recorder(redirect_to="demo@meesho.com")
    notify.run(rid, n, con=con, status_urls=urls)
    assert {m["to"] for m in n.outbox} == {"demo@meesho.com"}
    rows = con.execute("SELECT recipient, redirected_from FROM notifications").fetchall()
    assert all(r[0] == "demo@meesho.com" for r in rows)
    assert any(r[1] for r in rows), "the real recipient must stay auditable"


def test_a_ticket_with_no_status_link_is_skipped_not_sent_bare(run):
    """An acknowledgement with nowhere to look is exactly what was rejected in the first place."""
    rid, con, _ = run
    n = Recorder(redirect_to="demo@meesho.com")
    stats = notify.run(rid, n, con=con, status_urls={})
    assert stats["sent"] == 0
    assert any("no status link" in why for why in stats["reasons"])


def test_the_cap_bounds_the_blast_radius(run):
    """A grouping bug that turns 1 issue into 400 must cost 2 emails, not 400."""
    rid, con, urls = run
    n = Recorder(redirect_to="demo@meesho.com")
    stats = notify.run(rid, n, con=con, status_urls=urls, max_sends=2)
    assert stats["sent"] == 2 and stats["capped"] is True


def test_held_back_tickets_are_not_acknowledged(run):
    rid, con, urls = run
    con.execute("UPDATE ticket_drafts SET suppressed=1 WHERE run_id=?", (rid,))
    con.commit()
    stats = notify.run(rid, Recorder(redirect_to="d@meesho.com"), con=con, status_urls=urls)
    assert stats["sent"] == 0


def test_a_domain_outside_the_allowlist_is_refused(run):
    rid, con, urls = run
    n = Recorder(allow_domains=("nowhere.example",))
    assert notify.run(rid, n, con=con, status_urls=urls)["sent"] == 0


def test_the_message_carries_the_link_and_the_recurrence_count():
    text, html = notify._body("payment not received", "VAL-47", "https://x/exec?t=abc", 7)
    assert "https://x/exec?t=abc" in text and "https://x/exec?t=abc" in html
    assert "7" in text and "counted as one ticket" in html


def test_a_send_failure_is_recorded_rather_than_retried_forever(run):
    rid, con, urls = run

    class Broken(Recorder):
        def send(self, **kw):
            raise OSError("smtp down")

    stats = notify.run(rid, Broken(redirect_to="d@meesho.com"), con=con, status_urls=urls)
    assert stats["failed"] > 0 and stats["sent"] == 0
    row = con.execute("SELECT ok, error FROM notifications LIMIT 1").fetchone()
    assert row[0] == 0 and "smtp down" in row[1]
