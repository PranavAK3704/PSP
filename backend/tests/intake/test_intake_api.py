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


def test_the_agent_role_is_actually_creatable(client):
    """It was not, at first. `agent` existed in the backend and in the nav filter but was
    missing from the admin form's role list, so the account the pipeline signs in as could not
    be created through the UI and the sink could never authenticate. The server side is pinned
    here; the dropdown that feeds it is ADMIN_ROLES in App.jsx."""
    from app.auth import store as auth_store
    assert "agent" in auth_store.ROLES

    r = client.post("/api/auth/users", headers=_token("approver"),
                    json={"email": "bot@meesho.com", "name": "Intake pipeline",
                          "role": "agent", "password": "a-long-password"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["role"] == "agent"

    # And it can immediately do the one thing it exists to do.
    login = client.post("/api/auth/login",
                        json={"email": "bot@meesho.com", "password": "a-long-password"})
    assert login.status_code == 200
    tok = {"Authorization": f"Bearer {login.json()['token']}"}
    assert client.post("/api/intake/tickets", json=_draft("via-bot"),
                       headers=tok).status_code == 200
    assert client.get("/api/connectors", headers=tok).status_code == 403


# ── changing a role ──────────────────────────────────────────────────────────────────────────

def test_a_role_can_be_corrected_without_recreating_the_account(client):
    """Recreating a service account means rotating its password too, which is why this exists."""
    # A real human approver has to exist, or the last-approver guard correctly refuses —
    # which is exactly what happened the first time this test ran.
    client.post("/api/auth/users", headers=_token("approver"),
                json={"email": "boss@meesho.com", "name": "Boss", "role": "approver",
                      "password": "pw-long-enough"})
    client.post("/api/auth/users", headers=_token("approver"),
                json={"email": "bot@meesho.com", "name": "Bot", "role": "approver",
                      "password": "pw-long-enough"})
    r = client.post("/api/auth/role", headers=_token("approver"),
                    json={"email": "bot@meesho.com", "role": "agent"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "agent"

    # The password still works, and the new role is in the new token.
    login = client.post("/api/auth/login",
                        json={"email": "bot@meesho.com", "password": "pw-long-enough"})
    assert login.json()["user"]["role"] == "agent"
    tok = {"Authorization": f"Bearer {login.json()['token']}"}
    assert client.get("/api/connectors", headers=tok).status_code == 403
    assert client.get("/api/intake/tickets", headers=tok).status_code == 200


def test_the_last_approver_cannot_be_demoted(client):
    """Not a permission check — an approver is allowed to do this. It is a check that the
    system is still administrable afterwards, because role is the only thing that grants it."""
    client.post("/api/auth/users", headers=_token("approver"),
                json={"email": "solo@meesho.com", "name": "Solo", "role": "approver",
                      "password": "pw-long-enough"})
    from app.auth import store as auth_store
    approvers = [u["email"] for u in auth_store.list_users() if u["role"] == "approver"]
    # Demote every approver but the last; the last one must be refused.
    for e in approvers[:-1]:
        assert client.post("/api/auth/role", headers=_token("approver"),
                           json={"email": e, "role": "viewer"}).status_code == 200
    r = client.post("/api/auth/role", headers=_token("approver"),
                    json={"email": approvers[-1], "role": "viewer"})
    assert r.status_code == 400 and "only approver" in r.json()["detail"]


def test_a_non_approver_cannot_change_roles(client):
    r = client.post("/api/auth/role", headers=_token("agent"),
                    json={"email": "x@meesho.com", "role": "approver"})
    assert r.status_code == 403


def test_an_unknown_role_is_refused(client):
    client.post("/api/auth/users", headers=_token("approver"),
                json={"email": "b2@meesho.com", "name": "B", "role": "viewer",
                      "password": "pw-long-enough"})
    r = client.post("/api/auth/role", headers=_token("approver"),
                    json={"email": "b2@meesho.com", "role": "superuser"})
    assert r.status_code == 400


def test_a_mistyped_password_is_recoverable(client):
    """It was not. There is no delete endpoint either, so before this a wrong password stranded
    the account permanently — which is exactly how the intake bot ended up unusable."""
    client.post("/api/auth/users", headers=_token("approver"),
                json={"email": "bot@meesho.com", "name": "Bot", "role": "agent",
                      "password": "what-i-typed"})
    assert client.post("/api/auth/login",
                       json={"email": "bot@meesho.com",
                             "password": "what-the-file-says"}).status_code == 401

    r = client.post("/api/auth/password", headers=_token("approver"),
                    json={"email": "bot@meesho.com", "password": "what-the-file-says"})
    assert r.status_code == 200
    ok = client.post("/api/auth/login",
                     json={"email": "bot@meesho.com", "password": "what-the-file-says"})
    assert ok.status_code == 200 and ok.json()["user"]["role"] == "agent"


def test_a_reset_re_salts_rather_than_reusing_the_old_salt(client):
    from app.auth import store as auth_store
    client.post("/api/auth/users", headers=_token("approver"),
                json={"email": "s@meesho.com", "name": "S", "role": "viewer",
                      "password": "same-password"})
    before = auth_store.get_user("s@meesho.com")["salt"]
    client.post("/api/auth/password", headers=_token("approver"),
                json={"email": "s@meesho.com", "password": "same-password"})
    after = auth_store.get_user("s@meesho.com")
    assert after["salt"] != before
    assert auth_store.verify_password("s@meesho.com", "same-password")


def test_a_short_password_is_refused(client):
    client.post("/api/auth/users", headers=_token("approver"),
                json={"email": "t@meesho.com", "name": "T", "role": "viewer",
                      "password": "long-enough"})
    r = client.post("/api/auth/password", headers=_token("approver"),
                    json={"email": "t@meesho.com", "password": "short"})
    assert r.status_code == 400


def test_only_an_approver_can_reset_a_password(client):
    r = client.post("/api/auth/password", headers=_token("agent"),
                    json={"email": "x@meesho.com", "password": "long-enough"})
    assert r.status_code == 403


# ── listening channels ───────────────────────────────────────────────────────────────────────

def test_channels_are_reported_and_read_back(client):
    rows = [{"channel_id": "C1", "name": "valmo-firefighters", "messages": 42,
             "issues": 9, "tickets": 7, "qualified": True, "reason": None},
            {"channel_id": "C2", "name": "pilot_support_ams", "messages": 9, "issues": 0,
             "tickets": 0, "qualified": False, "reason": "the support team's own channel"}]
    r = client.post("/api/intake/channels", json={"channels": rows}, headers=_token("agent"))
    assert r.status_code == 200 and r.json()["channels"] == 2
    got = client.get("/api/intake/channels", headers=_token("agent")).json()
    assert [c["name"] for c in got["channels"]] == [x["name"] for x in rows]
    assert got["updated_at"]


def test_reporting_replaces_rather_than_appends(client):
    """A snapshot, not a log. A channel we stopped listening to must disappear rather than
    linger with counts that will never change again."""
    client.post("/api/intake/channels", headers=_token("agent"),
                json={"channels": [{"channel_id": "C1", "name": "old", "messages": 1}]})
    client.post("/api/intake/channels", headers=_token("agent"),
                json={"channels": [{"channel_id": "C2", "name": "new", "messages": 2}]})
    got = client.get("/api/intake/channels", headers=_token("agent")).json()
    assert [c["name"] for c in got["channels"]] == ["new"]


def test_an_excluded_channel_carries_its_reason(client):
    """Zero tickets from a live channel, a silent one and an excluded one look identical
    without this."""
    client.post("/api/intake/channels", headers=_token("agent"), json={"channels": [
        {"channel_id": "C2", "name": "pilot_support_ams", "messages": 9, "issues": 0,
         "tickets": 0, "qualified": False, "reason": "internal channel, not an escalation one"}]})
    c = client.get("/api/intake/channels", headers=_token("agent")).json()["channels"][0]
    assert c["qualified"] is False and "internal channel" in c["reason"]


def test_channels_need_a_login(client):
    assert client.get("/api/intake/channels").status_code == 401
    assert client.post("/api/intake/channels", json={"channels": []}).status_code == 401


def test_a_malformed_report_is_refused(client):
    assert client.post("/api/intake/channels", json={"channels": "nope"},
                       headers=_token("agent")).status_code == 400


# ── clearing the register ────────────────────────────────────────────────────────────────────

def test_reset_needs_the_words_and_the_role(client):
    """Destructive, irreversible, and it kills every partner-facing status link with it. One
    click or one stray request should not be enough."""
    client.post("/api/intake/tickets", json=_draft(), headers=_token("agent"))
    assert client.post("/api/intake/reset", json={}, headers=_token("agent")).status_code == 403
    assert client.post("/api/intake/reset", json={},
                       headers=_token("approver")).status_code == 400
    r = client.post("/api/intake/reset", json={"confirm": "delete all tickets"},
                    headers=_token("approver"))
    assert r.status_code == 200 and r.json()["deleted"] == 1
    assert client.get("/api/intake/tickets", headers=_token("agent")).json()["total"] == 0


def test_a_single_ticket_can_be_deleted(client):
    client.post("/api/intake/tickets", json=_draft("a"), headers=_token("agent"))
    client.post("/api/intake/tickets", json=_draft("b"), headers=_token("agent"))
    assert client.delete("/api/intake/tickets/VAL-1",
                         headers=_token("approver")).status_code == 200
    assert client.delete("/api/intake/tickets/VAL-99",
                         headers=_token("approver")).status_code == 404
    assert client.get("/api/intake/tickets", headers=_token("agent")).json()["total"] == 1


def test_references_do_not_restart_after_a_delete(client):
    """Reusing a reference makes two different tickets share the identifier people quote."""
    client.post("/api/intake/tickets", json=_draft("a"), headers=_token("agent"))
    client.post("/api/intake/tickets", json=_draft("b"), headers=_token("agent"))
    client.delete("/api/intake/tickets/VAL-2", headers=_token("approver"))
    r = client.post("/api/intake/tickets", json=_draft("c"), headers=_token("agent"))
    assert r.json()["ref"] == "VAL-3"


# ── the exemplar index ───────────────────────────────────────────────────────────────────────

def test_the_index_uploads_and_reports_itself(client):
    """It cannot ship in the image — every exemplar is a real partner's sentence — so it
    travels over authenticated HTTPS and lives in the durable store."""
    rows = [{"text": "paisa nahi aaya", "disposition": "payment_not_received"},
            {"text": "captain panel down", "disposition": "capacity_panel_issue"}]
    r = client.post("/api/intake/exemplars", json={"exemplars": rows},
                    headers=_token("approver"))
    assert r.status_code == 200 and r.json()["count"] == 2
    info = client.get("/api/intake/exemplars", headers=_token("agent")).json()
    assert info["loaded"] is True and info["count"] == 2
    assert info["dispositions"] == ["capacity_panel_issue", "payment_not_received"]


def test_the_exemplars_themselves_are_never_served(client):
    """Knowing the classifier is loaded does not require reading what it was taught, and what
    it was taught is partner text."""
    client.post("/api/intake/exemplars", headers=_token("approver"),
                json={"exemplars": [{"text": "a partner's own words", "disposition": "x"}]})
    body = client.get("/api/intake/exemplars", headers=_token("agent")).text
    assert "a partner's own words" not in body


def test_only_an_approver_can_replace_the_classifier(client):
    """Replacing the index changes how every future ticket is categorised."""
    rows = [{"text": "t", "disposition": "d"}]
    assert client.post("/api/intake/exemplars", json={"exemplars": rows},
                       headers=_token("agent")).status_code == 403
    assert client.post("/api/intake/exemplars", json={"exemplars": rows},
                       headers=_token("viewer")).status_code == 403


def test_a_malformed_index_is_refused(client):
    for bad in ({"exemplars": []}, {"exemplars": "nope"},
                {"exemplars": [{"text": "t"}]},
                {"exemplars": [{"disposition": "d"}]}):
        assert client.post("/api/intake/exemplars", json=bad,
                           headers=_token("approver")).status_code == 400


def test_an_absent_index_reports_not_loaded_rather_than_empty(client):
    """'Classification is off' and 'the matcher refused' are different states, and a ticket
    shows them identically unless the page can ask which it is."""
    info = client.get("/api/intake/exemplars", headers=_token("agent")).json()
    assert info["loaded"] is False and info["count"] == 0


def test_a_later_poll_fills_in_a_category_the_first_one_could_not(client):
    """A ticket raised before the exemplar index was uploaded has no category. Freezing it at
    creation means a classifier that improves never reaches the tickets an agent is looking at."""
    d = _draft()
    d["intent"] = ""
    client.post("/api/intake/tickets", json=d, headers=_token("agent"))
    assert client.get("/api/intake/tickets", headers=_token("agent")
                      ).json()["tickets"][0]["disposition"] == ""

    d["intent"] = "payment_not_received"
    r = client.post("/api/intake/tickets", json=d, headers=_token("agent"))
    assert r.json()["created"] is False, "it must still be the same ticket"
    assert client.get("/api/intake/tickets", headers=_token("agent")
                      ).json()["tickets"][0]["disposition"] == "payment_not_received"


def test_a_later_poll_raises_the_recurrence_count(client):
    d = _draft()
    client.post("/api/intake/tickets", json=d, headers=_token("agent"))
    d["occurrence_count"] = 3
    client.post("/api/intake/tickets", json=d, headers=_token("agent"))
    assert client.get("/api/intake/tickets", headers=_token("agent")
                      ).json()["tickets"][0]["occurrence_count"] == 3


def test_a_poll_never_overwrites_what_the_agent_decided(client):
    """State and the partner-facing note belong to the human. The pipeline must not replace a
    person's decision with a stale one on its next pass."""
    d = _draft()
    client.post("/api/intake/tickets", json=d, headers=_token("agent"))
    client.patch("/api/intake/tickets/VAL-1", headers=_token("agent"),
                 json={"state": "resolved", "status_note": "Refunded on the 4th."})
    client.post("/api/intake/tickets", json=d, headers=_token("agent"))
    t = client.get("/api/intake/tickets", headers=_token("agent")).json()["tickets"][0]
    assert t["state"] == "resolved" and t["status_note"] == "Refunded on the 4th."


def test_a_novel_answer_does_not_overwrite_a_real_category(client):
    """NOVEL is the matcher declining. Writing that over a category it previously found would
    be losing information, not updating it."""
    d = _draft()
    d["intent"] = "technical_issue"
    client.post("/api/intake/tickets", json=d, headers=_token("agent"))
    d["intent"] = "NOVEL"
    client.post("/api/intake/tickets", json=d, headers=_token("agent"))
    assert client.get("/api/intake/tickets", headers=_token("agent")
                      ).json()["tickets"][0]["disposition"] == "technical_issue"


def test_the_index_round_trips_through_compression(client):
    """366 KB of raw JSON did not survive a redeploy — it lived in the in-process cache, so the
    read-back straight after upload looked fine and the durable copy was never written."""
    rows = [{"text": f"message number {i} about payment", "disposition": "payment_not_received",
             "label_provenance": "gold" if i % 3 == 0 else "silver"} for i in range(200)]
    r = client.post("/api/intake/exemplars", json={"exemplars": rows},
                    headers=_token("approver"))
    assert r.status_code == 200, r.text
    assert r.json()["packed_bytes"] < 20_000, "compression should beat the raw size heavily"

    back = intake_api.exemplar_index()["exemplars"]
    assert len(back) == 200
    assert back[0]["text"] == rows[0]["text"]
    assert sum(1 for x in back if x["label_provenance"] == "gold") == \
        sum(1 for x in rows if x["label_provenance"] == "gold"), \
        "gold provenance drives gold_weight at match time and must survive the round trip"


def test_a_corrupt_blob_reads_as_no_index_rather_than_crashing(client, monkeypatch):
    """Classification reporting itself as off is recoverable; a poll that raises every minute
    is not."""
    from app.durable_state import durable_path
    durable_path(intake_api.EXEMPLARS).write_text('{"packed": "not-valid-base64-gzip"}')
    assert intake_api.exemplar_index()["exemplars"] == []
