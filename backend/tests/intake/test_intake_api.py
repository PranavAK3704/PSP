"""The intake register inside PSP, and the boundary that keeps agents out of everything else.

The isolation test is the important one: `agent` exists so ops can work tickets without seeing
SOPs, calibration, captains or the ledger. If that boundary leaks, the role is pointless.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PSP_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)
    from app.main import app
    return TestClient(app)


def _token(role: str) -> dict:
    from app.auth import tokens
    return {"Authorization": f"Bearer {tokens.make_token(f"{role}@meesho.com", role)}"}


def _draft(key="k1", title="payment nahi aaya"):
    return {"idempotency_key": key, "title": title, "intent": "payment_not_received",
            "dc_code": "NQS", "raiser": "A Partner", "source_system": "slack",
            "source_id": "C1/1.1", "occurrence_count": 1}


# ── the register ─────────────────────────────────────────────────────────────────────────────

def test_a_ticket_is_created_once_however_many_times_it_is_posted(client):
    a = client.post("/api/intake/tickets", json=_draft(), headers=_token("agent")).json()
    b = client.post("/api/intake/tickets", json=_draft(), headers=_token("agent")).json()
    assert a["created"] is True and b["created"] is False
    assert a["ref"] == b["ref"] and a["status_token"] == b["status_token"]
    assert client.get("/api/intake/tickets", headers=_token("agent")).json()["total"] == 1


def test_a_ticket_without_a_key_is_refused(client):
    r = client.post("/api/intake/tickets", json={"title": "x"}, headers=_token("agent"))
    assert r.status_code == 400


def test_an_agent_can_move_a_ticket_along(client):
    client.post("/api/intake/tickets", json=_draft(), headers=_token("agent"))
    r = client.patch("/api/intake/tickets/VAL-1",
                     json={"state": "in_progress", "status_note": "Chasing finance"},
                     headers=_token("agent"))
    assert r.status_code == 200 and r.json()["ticket"]["state"] == "in_progress"


def test_an_invalid_state_is_refused(client):
    client.post("/api/intake/tickets", json=_draft(), headers=_token("agent"))
    r = client.patch("/api/intake/tickets/VAL-1", json={"state": "banana"},
                     headers=_token("agent"))
    assert r.status_code == 400


def test_a_viewer_can_read_but_not_write(client):
    client.post("/api/intake/tickets", json=_draft(), headers=_token("agent"))
    assert client.get("/api/intake/tickets", headers=_token("viewer")).status_code == 200
    assert client.patch("/api/intake/tickets/VAL-1", json={"state": "resolved"},
                        headers=_token("viewer")).status_code == 403


# ── the boundary ─────────────────────────────────────────────────────────────────────────────

def test_an_agent_cannot_reach_the_rest_of_psp(client):
    """The whole reason the role exists. An agent works tickets and sees nothing else."""
    h = _token("agent")
    for path in ("/api/calibration", "/api/connectors", "/api/growth", "/api/captains",
                 "/api/auth/teams"):
        r = client.get(path, headers=h)
        assert r.status_code == 403, f"{path} leaked to an agent (HTTP {r.status_code})"


def test_staff_still_reach_both(client):
    assert client.get("/api/intake/tickets", headers=_token("author")).status_code == 200
    assert client.get("/api/connectors", headers=_token("author")).status_code == 200


def test_the_register_needs_a_login_at_all(client):
    assert client.get("/api/intake/tickets").status_code == 401


# ── the partner status page ──────────────────────────────────────────────────────────────────

def test_the_status_page_needs_no_login_but_needs_the_token(client):
    """Partners are not Meesho staff and have no PSP login, so this route takes no session.
    What protects it is a per-ticket random token — never the sequential reference."""
    tok = client.post("/api/intake/tickets", json=_draft(),
                      headers=_token("agent")).json()["status_token"]
    ok = client.get(f"/t/{tok}")
    assert ok.status_code == 200 and "VAL-1" in ok.text
    assert client.get("/t/VAL-1").status_code == 404        # the ref must not work as a token
    assert client.get("/t/" + "0" * 32).status_code == 404


def test_the_status_token_is_never_listed_to_agents(client):
    """It is a capability: anyone holding it reads the ticket without logging in. It travels
    only in the link the raiser is sent."""
    client.post("/api/intake/tickets", json=_draft(), headers=_token("agent"))
    body = client.get("/api/intake/tickets", headers=_token("agent")).json()
    assert "status_token" not in body["tickets"][0]


def test_the_status_page_shows_the_agents_note_and_the_recurrence(client):
    d = _draft()
    d["occurrence_count"] = 7
    tok = client.post("/api/intake/tickets", json=d,
                      headers=_token("agent")).json()["status_token"]
    client.patch("/api/intake/tickets/VAL-1",
                 json={"state": "in_progress", "status_note": "Finance is reversing it"},
                 headers=_token("agent"))
    page = client.get(f"/t/{tok}").text
    assert "Being worked on" in page and "Finance is reversing it" in page
    assert "7" in page and "counted as one ticket" in page


def test_partner_text_is_escaped_on_the_status_page(client):
    d = _draft(title="<script>alert(1)</script> payment issue")
    tok = client.post("/api/intake/tickets", json=d,
                      headers=_token("agent")).json()["status_token"]
    page = client.get(f"/t/{tok}").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page
