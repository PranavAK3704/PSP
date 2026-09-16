"""The human label store and the NOVEL-queue endpoint.

A confirmation is the only data in this pipeline nothing can regenerate, so the tests here are
mostly about not losing it and not silently ignoring it.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from app.intake import labels, live_demo


# ── the store ────────────────────────────────────────────────────────────────────────────────

def test_a_confirmation_is_recorded_with_who_and_when(tmp_path):
    p = tmp_path / "c.jsonl"
    row = labels.confirm("paisa nahi aaya", "label", disposition="payment_not_received",
                         confirmed_by="pranav", path=p)
    assert row["label_provenance"] == "gold"
    assert row["confirmed_by"] == "pranav" and row["confirmed_at"]
    assert labels.load(p)[row["id"]]["disposition"] == "payment_not_received"


def test_a_correction_supersedes_but_does_not_erase(tmp_path):
    """Both lines survive. If two people disagree, the disagreement stays visible."""
    p = tmp_path / "c.jsonl"
    labels.confirm("paisa nahi aaya", "label", disposition="cod_shortfall",
                   confirmed_by="a", path=p)
    labels.confirm("paisa nahi aaya", "label", disposition="payment_not_received",
                   confirmed_by="b", path=p)
    assert len(p.read_text().strip().splitlines()) == 2      # history kept
    resolved = labels.load(p)
    assert len(resolved) == 1                                 # one live answer
    assert list(resolved.values())[0]["disposition"] == "payment_not_received"


def test_the_id_matches_the_corpus_id_scheme(tmp_path):
    """This shared definition is what lets a confirmation promote a corpus row to gold. If the
    two drift apart the promotion silently stops happening, so it is pinned here."""
    import hashlib
    import re
    text = "  Riders   get 5 shipments\npayment  "
    expected = hashlib.sha256(
        re.sub(r"\s+", " ", text).strip().encode()).hexdigest()[:16]
    assert labels.text_id(text) == expected


def test_whitespace_variants_are_the_same_label(tmp_path):
    p = tmp_path / "c.jsonl"
    labels.confirm("paisa  nahi\naaya", "label", disposition="x", confirmed_by="a", path=p)
    labels.confirm("paisa nahi aaya", "label", disposition="y", confirmed_by="a", path=p)
    assert len(labels.load(p)) == 1


def test_a_label_without_a_disposition_is_refused(tmp_path):
    with pytest.raises(labels.LabelError, match="needs a disposition"):
        labels.confirm("x", "label", confirmed_by="a", path=tmp_path / "c.jsonl")


def test_a_human_cannot_assign_novel(tmp_path):
    """NOVEL is the classifier saying it does not know. Storing it as a human answer would put a
    row in the index that means nothing and looks authoritative."""
    with pytest.raises(labels.LabelError, match="not a label a human can assign"):
        labels.confirm("x", "label", disposition="NOVEL", confirmed_by="a",
                       path=tmp_path / "c.jsonl")


def test_an_unattributed_label_is_refused(tmp_path):
    with pytest.raises(labels.LabelError, match="confirmed_by is required"):
        labels.confirm("x", "label", disposition="y", confirmed_by="  ",
                       path=tmp_path / "c.jsonl")


def test_a_corrupt_line_does_not_take_the_others_with_it(tmp_path):
    """This file cannot be regenerated. One bad line must not make the rest unreadable."""
    p = tmp_path / "c.jsonl"
    labels.confirm("a", "label", disposition="x", confirmed_by="me", path=p)
    with p.open("a") as fh:
        fh.write("{not json at all\n")
    labels.confirm("b", "label", disposition="y", confirmed_by="me", path=p)
    assert len(labels.load(p)) == 2


def test_not_an_issue_carries_no_disposition(tmp_path):
    p = tmp_path / "c.jsonl"
    r = labels.confirm("can we go to play arena?", "not_an_issue", confirmed_by="me", path=p)
    assert r["disposition"] == labels.NOT_AN_ISSUE
    assert labels.labelled(p) == []
    assert len(labels.not_an_issue(p)) == 1
    with pytest.raises(labels.LabelError, match="cannot also carry a disposition"):
        labels.confirm("y", "not_an_issue", disposition="z", confirmed_by="me", path=p)


def test_an_export_can_drop_the_text(tmp_path):
    """Confirmations carry partner wording, so the backup that leaves this machine can omit it."""
    p, dest = tmp_path / "c.jsonl", tmp_path / "out.json"
    labels.confirm("paisa nahi aaya", "label", disposition="x", confirmed_by="me", path=p)
    labels.export_confirmations(dest, include_text=False, path=p)
    blob = dest.read_text()
    assert "paisa nahi aaya" not in blob
    assert json.loads(blob)["confirmations"][0]["disposition"] == "x"


# ── the endpoint ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(labels, "STORE", tmp_path / "c.jsonl")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), live_demo.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path / "c.jsonl"
    srv.shutdown()


def _post(url, body, headers=None):
    req = urllib.request.Request(
        url + "/api/confirm", data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_the_endpoint_records_a_confirmation(server):
    url, store = server
    code, body = _post(url, {"text": "paisa nahi aaya", "decision": "label",
                             "disposition": "payment_not_received", "confirmed_by": "pranav"},
                       {"X-Intake-Confirm": "1"})
    assert code == 200 and body["ok"]
    assert store.exists() and len(labels.load(store)) == 1


def test_a_page_that_is_not_ours_cannot_post(server):
    """Loopback keeps other machines out; it does not keep other web pages out. Any site the
    browser has open can POST to 127.0.0.1, so the write needs its own guard."""
    url, store = server
    code, body = _post(url, {"text": "x", "decision": "label", "disposition": "y",
                             "confirmed_by": "z"})                    # no custom header
    assert code == 403 and body["error"] == "missing_confirm_header"

    code, body = _post(url, {"text": "x", "decision": "label", "disposition": "y",
                             "confirmed_by": "z"},
                       {"X-Intake-Confirm": "1", "Origin": "https://evil.example.com"})
    assert code == 403 and body["error"] == "cross_origin_refused"
    assert not store.exists(), "a refused request must not have written anything"


def test_a_bad_confirmation_explains_itself_rather_than_500ing(server):
    url, _ = server
    code, body = _post(url, {"text": "x", "decision": "label", "confirmed_by": "z"},
                       {"X-Intake-Confirm": "1"})
    assert code == 400 and "needs a disposition" in body["error"]


def test_the_endpoint_cannot_reach_slack():
    import inspect
    src = inspect.getsource(live_demo.Handler)
    for banned in ("chat.postMessage", "slack_sdk", "WebClient", "xoxb"):
        assert banned not in src
