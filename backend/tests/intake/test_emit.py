"""Stage 10 — ticket drafts. The product, and the promise that a retry cannot double-create.

Phase 1 creates nothing, so what is tested here is the PAYLOAD and the SEAM: the two things
that are painful to retrofit once a sink exists.
"""
from __future__ import annotations

import shutil

import pytest

from app.intake import (
    dedupe, emit, extract, group, loadstage, noise, qualify, register, store,
)

pytestmark = pytest.mark.usefixtures("intake_db")


@pytest.fixture()
def emitted(tmp_path, fixtures_dir):
    raw = tmp_path / "raw"
    raw.mkdir()
    for f in sorted(fixtures_dir.glob("*.ndjson")):
        shutil.copy(f, raw / f.name)
    loadstage.load(raw, run_id="r1", skip_validate=True)
    noise.run("r1")
    extract.run("r1")
    qualify.run("r1")
    group.run("r1")
    register.run("r1")
    dedupe.run("r1")
    stats = emit.run("r1")
    con = store.connect()
    yield con, stats
    con.close()


# ── the idempotency key ─────────────────────────────────────────────────────────────────────
def test_the_key_is_a_pure_function_of_the_source():
    """Not a uuid, not a row id, not minted by the sink. Same message, same key — on every run,
    every machine, through every sink. That is what makes a retry safe independently of where
    tickets land."""
    a = emit.idempotency_key("slack", "C08T6NLL77H", "1788482771.760339")
    b = emit.idempotency_key("slack", "C08T6NLL77H", "1788482771.760339")
    assert a == b
    assert len(a) == 64

    # the composite key really is composite: the same ts in another channel is another ticket
    assert emit.idempotency_key("slack", "C09JY7YLB3L", "1788482771.760339") != a
    assert emit.idempotency_key("whatsapp", "C08T6NLL77H", "1788482771.760339") != a


def test_keys_are_stable_across_a_re_run(emitted):
    con, _ = emitted
    before = {r["issue_id"]: r["idempotency_key"] for r in
              con.execute("SELECT issue_id, idempotency_key FROM ticket_drafts "
                          "WHERE run_id='r1'")}
    emit.run("r1", con=con)
    after = {r["issue_id"]: r["idempotency_key"] for r in
             con.execute("SELECT issue_id, idempotency_key FROM ticket_drafts "
                         "WHERE run_id='r1'")}
    assert before == after and before


def test_keys_are_unique_per_issue(emitted):
    con, stats = emitted
    keys = [r[0] for r in con.execute(
        "SELECT idempotency_key FROM ticket_drafts WHERE run_id='r1'")]
    assert len(set(keys)) == len(keys) == stats["issues"]


def test_the_key_does_not_depend_on_the_run_id(emitted):
    """A second run over the same export must not produce a second set of tickets."""
    con, _ = emitted
    rows = con.execute("SELECT issue_id, anchor_channel_id, anchor_message_id FROM issues "
                       "WHERE run_id='r1'").fetchall()
    for r in rows:
        stored = con.execute("SELECT idempotency_key FROM ticket_drafts "
                             "WHERE run_id='r1' AND issue_id=?", (r["issue_id"],)).fetchone()[0]
        assert stored == emit.idempotency_key("slack", r["anchor_channel_id"],
                                              r["anchor_message_id"])


# ── phase 1 creates nothing ─────────────────────────────────────────────────────────────────
def test_no_ticket_is_created_and_the_reference_says_so(emitted):
    con, stats = emitted
    assert stats["tickets_actually_created"] == 0
    assert stats["sink"] == "dry_run"
    refs = [r[0] for r in con.execute(
        "SELECT external_ref FROM ticket_drafts WHERE run_id='r1' AND suppressed=0")]
    assert refs and all(r.startswith("DRY-") for r in refs), \
        "a dry-run reference must never be mistakable for a ticket id in a spreadsheet"


def test_the_module_imports_no_http_client():
    """The 'no calls to any support backend' constraint, kept structurally rather than by
    intention."""
    import inspect
    src = inspect.getsource(emit)
    for banned in ("import requests", "import httpx", "import urllib", "import http.client",
                   "from requests", "from httpx"):
        assert banned not in src


# ── what must NOT become a ticket ───────────────────────────────────────────────────────────
def test_informational_issues_are_held_back_with_a_reason(emitted):
    con, stats = emitted
    rows = con.execute(
        "SELECT d.suppressed_reason FROM ticket_drafts d JOIN issues i "
        "  ON i.run_id=d.run_id AND i.issue_id=d.issue_id "
        "WHERE d.run_id='r1' AND i.informational=1").fetchall()
    assert rows
    for r in rows:
        assert r["suppressed_reason"].startswith("informational")


def test_the_far_side_of_a_cross_channel_duplicate_is_held_back(emitted):
    """The MX1 issue was posted in both channels 47s apart. One ticket, not two — and the
    suppressed side names its twin so closure is traceable from either."""
    con, _ = emitted
    dup = con.execute(
        "SELECT d.suppressed, d.suppressed_reason, i.duplicate_of FROM ticket_drafts d "
        "JOIN issues i ON i.run_id=d.run_id AND i.issue_id=d.issue_id "
        "WHERE d.run_id='r1' AND i.duplicate_of IS NOT NULL").fetchone()
    assert dup is not None
    assert dup["suppressed"] == 1
    assert dup["duplicate_of"] in dup["suppressed_reason"]

    # and the twin IS raised — suppressing both would lose the issue entirely
    twin = con.execute("SELECT suppressed FROM ticket_drafts WHERE run_id='r1' AND issue_id=?",
                       (dup["duplicate_of"],)).fetchone()
    assert twin["suppressed"] == 0


def test_a_draft_exists_for_every_issue_including_the_suppressed_ones(emitted):
    """Same discipline as the noise gate: 'what would we have raised, and what did we hold
    back' must be answerable."""
    con, stats = emitted
    n = con.execute("SELECT COUNT(*) FROM ticket_drafts WHERE run_id='r1'").fetchone()[0]
    assert n == stats["issues"] == stats["would_create"] + stats["suppressed"]


def test_require_identifier_is_off_by_default_and_holds_back_when_on(emitted):
    con, stats = emitted
    on = emit.run("r1", con=con, require_identifier=True)
    assert on["would_create"] < stats["would_create"]
    assert "no actionable identifier" in " ".join(on["suppressed_by_reason"])


# ── the payload ─────────────────────────────────────────────────────────────────────────────
def test_the_title_is_deterministic_and_leads_with_the_dc_code():
    """No model. The raiser already wrote the summary, and a triager asks 'which DC' first."""
    t = emit.make_title("DC Code: NQS. Daily closure not happening since morning.", "NQS")
    assert t == "[NQS] DC Code: NQS."

    # deterministic: the same input twice is the same title, and no run state leaks in
    assert emit.make_title("DC Code: NQS. Daily closure not happening since morning.",
                           "NQS") == t
    # only the FIRST sentence is the title; the rest belongs in the description
    assert emit.make_title("Panel down. Please check urgently.", None) == "Panel down."

    # markup is stripped, and an empty body still yields a usable title
    assert "<@U09HH8QKZ43>" not in emit.make_title("<@U09HH8QKZ43> panel down", None)
    assert emit.make_title("", None) == "(no text — see attachment)"


def test_a_long_title_is_truncated_on_a_word_boundary():
    long = "x" * 40 + " " + "y" * 200
    t = emit.make_title(long, None)
    assert len(t) <= 91 and t.endswith("…")


def test_the_description_carries_the_source_link_and_the_identifiers():
    d = emit.make_description("payout not received", "https://meesho.slack.com/archives/C1/p1",
                              3, {"mobile": ["9900000001"]})
    assert "payout not received" in d
    assert "https://meesho.slack.com/archives/C1/p1" in d
    assert "mobile=9900000001" in d
    assert "Thread replies: 3" in d


# ── portability: the seam is the whole contract ─────────────────────────────────────────────
def test_any_sink_drops_in_without_the_pipeline_changing(emitted):
    """The point of the protocol. A different destination is three lines, and nothing upstream
    of this file knows which one is in use — which is what lets the pipeline outlive any
    particular ticketing system."""
    con, _ = emitted
    seen = []

    class RecordingSink:
        name = "test_recording"

        def create(self, draft: emit.TicketDraft) -> str:
            seen.append(draft)
            return f"EXT-{len(seen)}"

    stats = emit.run("r1", con=con, sink=RecordingSink())
    assert stats["sink"] == "test_recording"
    assert len(seen) == stats["would_create"]
    assert all(isinstance(d, emit.TicketDraft) for d in seen)
    assert all(d.idempotency_key and d.title and d.source_id for d in seen)
    # the sink's reference is what gets stored
    refs = {r[0] for r in con.execute(
        "SELECT external_ref FROM ticket_drafts WHERE run_id='r1' AND suppressed=0")}
    assert all(r.startswith("EXT-") for r in refs)
    emit.run("r1", con=con)          # restore the dry-run drafts


def test_the_draft_is_a_plain_serialisable_payload(emitted):
    con, _ = emitted
    d = emit.draft_for(
        dict(con.execute("SELECT * FROM issues WHERE run_id='r1' LIMIT 1").fetchone()),
        "DC Code: NQS closure issue")
    import json
    json.dumps(d.as_dict())          # must not raise: any sink can put this on a wire
    assert set(d.as_dict()) >= {"idempotency_key", "source_system", "source_id", "title",
                                "description", "suppressed"}


# ── the silent-zero guard ───────────────────────────────────────────────────────────────────
def test_an_unregistered_run_warns_instead_of_returning_a_bland_zero(emitted):
    con, _ = emitted
    r = emit.run("no-such-run", con=con)
    assert r["issues"] == 0
    assert "register" in r["_warning"]
