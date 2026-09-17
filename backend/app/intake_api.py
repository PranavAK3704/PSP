"""The intake ticket register, served by PSP itself.

    POST   /api/intake/tickets        the pipeline creates a ticket   (idempotent, server-side)
    GET    /api/intake/tickets        the register, for agents
    PATCH  /api/intake/tickets/{ref}  an agent moves one along
    GET    /t/{token}                 one ticket's status, for the person who raised it

── WHY THIS LIVES IN PSP AND NOT IN A SPREADSHEET ────────────────────────────────────────────
Two Google-backed registers were built and both failed on the same thing: an auth system we do
not control. An Apps Script Web App restricted to the domain cannot be called by a script at
all, and the Sheets API needs a GCP project nobody here has rights on.

PSP's auth is ours. Agents already log in here, tokens are ours to issue, and `durable_state`
already makes data survive a redeploy on a tier with no disk. The register belongs next to the
work, not in a tab nobody opens.

── IDEMPOTENCY IS ENFORCED HERE, NOT IN THE CLIENT ───────────────────────────────────────────
`idempotency_key` is derived from the SOURCE MESSAGE — sha256(source_system, channel_id,
message_id) — so the same Slack message produces the same key on every run from every machine.
A repeat POST returns the EXISTING ticket with `created: false`. That has to be the server's
job: a client that promises not to retry is making a promise it cannot keep across timeouts,
re-runs and two operators, and duplicate tickets are the exact failure this project exists to
prevent.

── THE STATUS ROUTE IS DELIBERATELY UNAUTHENTICATED ──────────────────────────────────────────
`/t/{token}` is the one route a partner opens, and partners are not Meesho staff — they have no
PSP login and never will. So it takes no session. What protects it is the token: 32 random hex
characters, per ticket, serving exactly that ticket and nothing else. A sequential reference
would be enumerable and is never used in a URL.
"""
from __future__ import annotations

import html
import json
import secrets
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import HTMLResponse

from .auth.deps import current_user, require_role
from .durable_state import durable_path, read_json_from

router = APIRouter()

STORE = "intake_tickets.json"
CHANNELS = "intake_channels.json"

#: Every write to the register is read-modify-write on one JSON document, and there are now TWO
#: kinds of writer: the poller thread creating tickets, and request threads patching them. Without
#: this lock they interleave and one of them silently loses — an agent's state change overwritten
#: by a ticket created a millisecond later, with nothing to show it happened.
_WRITE_LOCK = threading.Lock()

#: An agent works the register and sees nothing else in PSP. Staff roles see it too.
intake_access = require_role("agent", "viewer", "author", "approver")

#: Only these may move a ticket along. A viewer reads; an agent is here to work.
intake_write = require_role("agent", "author", "approver")

STATES = ("open", "in_progress", "resolved")


def _load() -> dict:
    return read_json_from(durable_path(STORE), {"tickets": []})


def _save(data: dict) -> None:
    durable_path(STORE).write_text(json.dumps(data, indent=1, ensure_ascii=False))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _next_ref(data: dict) -> int:
    """The next reference, from a stored counter that only ever goes up.

    NOT derived from the tickets present, which was the first attempt and is wrong twice over:
    `len() + 1` makes two racing creates agree on the same number, and "one past the highest"
    reuses a reference after a delete — so two different tickets end up sharing the identifier
    people quote to each other. A counter is the only thing that survives both.

    Seeded from the highest existing reference so a store written before this still counts on
    from the right place rather than restarting at 1.
    """
    seq = data.get("next_ref")
    if not isinstance(seq, int) or seq < 1:
        seq = 1
        for t in data.get("tickets", []):
            try:
                seq = max(seq, int(str(t.get("ref", "")).split("-")[-1]) + 1)
            except (ValueError, IndexError):
                continue
    data["next_ref"] = seq + 1
    return seq


def _public(t: dict) -> dict:
    """What an agent sees. `status_token` is withheld — it is a capability, not a field: anyone
    holding it can read the ticket without logging in, so it travels only in the link the
    raiser is sent."""
    return {k: v for k, v in t.items() if k != "status_token"}


@router.post("/api/intake/tickets")
def create_ticket(payload: dict = Body(...), user: dict = Depends(intake_write)) -> dict:
    key = (payload.get("idempotency_key") or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="idempotency_key is required")
    return {"ok": True, **create_ticket_record(payload, created_by=user.get("email"))}


def unacknowledged(channel: str = "email") -> list[dict]:
    """Tickets that have not been acknowledged on this channel yet.

    Keyed on the ticket rather than on a separate ledger, so "did we tell them" is a property of
    the thing itself and cannot drift out of step with it.
    """
    return [t for t in _load()["tickets"]
            if not t.get("acknowledged_at") or t.get("acknowledged_channel") != channel]


def mark_acknowledged(key: str, *, channel: str, recipient: str | None,
                      error: str | None = None) -> bool:
    """Record that we told the raiser — or that we tried and failed.

    A failure is recorded too, with its reason, and does NOT mark the ticket acknowledged: the
    next poll retries it. Silently dropping a failed acknowledgement would leave a partner
    believing nobody read their message, which is the whole failure this project exists to fix.
    """
    with _WRITE_LOCK:
        data = _load()
        for t in data["tickets"]:
            if t["idempotency_key"] != key:
                continue
            if error:
                t["acknowledge_error"] = error
            else:
                t.update(acknowledged_at=_now(), acknowledged_to=recipient,
                         acknowledged_channel=channel, acknowledge_error=None)
            _save(data)
            return True
    return False


def store_channels(rows: list, by: str = "") -> int:
    """Replace the listening-channel snapshot. Shared by the route and the in-process poller."""
    durable_path(CHANNELS).write_text(json.dumps(
        {"channels": rows, "updated_at": _now(), "updated_by": by},
        indent=1, ensure_ascii=False))
    return len(rows)


def create_ticket_record(payload: dict, *, created_by: str | None = None) -> dict:
    """Create a ticket, or return the existing one for this key.

    The route and the in-process poller both land here, so there is exactly one definition of
    what creating a ticket means — and the idempotency check cannot be true over HTTP and false
    in-process, which is the kind of divergence that produces duplicates nobody can explain.
    """
    key = (payload.get("idempotency_key") or "").strip()
    with _WRITE_LOCK:
        return _create_locked(payload, key, created_by)


def _create_locked(payload: dict, key: str, created_by: str | None) -> dict:
    data = _load()
    for t in data["tickets"]:
        if t["idempotency_key"] == key:
            # ALREADY EXISTS. Return the original reference and its original token, so a
            # retried acknowledgement points at the same page rather than a second one.
            return {"ref": t["ref"], "created": False, "status_token": t["status_token"]}

    # NOT len()+1. Two creates racing on a stale read both compute the same number and two
    # tickets end up sharing a reference — the one identifier everybody quotes. Derived from the
    # highest reference actually present instead, under the lock.
    ref = f"VAL-{_next_ref(data)}"
    ticket = {
        "ref": ref,
        "idempotency_key": key,
        "status_token": secrets.token_hex(16),
        "state": "open",
        "title": payload.get("title") or "",
        "description": payload.get("description") or "",
        "disposition": payload.get("intent") or payload.get("disposition") or "",
        "dc_code": payload.get("dc_code") or "",
        "raiser": payload.get("raiser") or "",
        "source_system": payload.get("source_system") or "",
        "source_id": payload.get("source_id") or "",
        "permalink": payload.get("source_permalink") or "",
        "occurrence_count": payload.get("occurrence_count") or 1,
        "first_raised_at": payload.get("first_raised_at") or "",
        "last_raised_at": payload.get("last_raised_at") or "",
        "reply_count": payload.get("reply_count") or 0,
        "flags": payload.get("flags") or [],
        "entities": payload.get("entities") or {},
        "status_note": "",
        # Acknowledgement state lives HERE, on the durable ticket, and not in the intake
        # SQLite — that database sits on the container filesystem, which Render wipes, so a
        # ledger there would forget every send on each restart and mail the same partner again.
        # That is the precise failure the ledger exists to prevent, firing on every deploy.
        "acknowledged_at": None,
        "acknowledged_to": None,
        "acknowledged_channel": None,
        "acknowledge_error": None,
        "created_at": _now(),
        "updated_at": _now(),
        "created_by": created_by,
    }
    data["tickets"].append(ticket)
    _save(data)
    return {"ref": ref, "created": True, "status_token": ticket["status_token"]}


@router.post("/api/intake/channels")
def report_channels(payload: dict = Body(...), user: dict = Depends(intake_write)) -> dict:
    """The pipeline reports what each listening channel produced.

    A REPLACE, not an append: this is a snapshot of the current state of every channel, and a
    channel that stopped being listened to should disappear rather than linger with stale
    counts.
    """
    rows = payload.get("channels")
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="channels must be a list")
    return {"ok": True, "channels": store_channels(rows, by=user.get("email") or "")}


@router.get("/api/intake/channels")
def list_channels(user: dict = Depends(intake_access)) -> dict:
    return read_json_from(durable_path(CHANNELS), {"channels": [], "updated_at": None})


@router.get("/api/intake/poller")
def poller_status(user: dict = Depends(intake_access)) -> dict:
    """What the listener has been doing.

    Without this, "no new tickets" and "nothing is listening" look identical on the page — and
    the second is the one worth knowing about.
    """
    from . import intake_poller
    return intake_poller.status()


@router.get("/api/intake/tickets")
def list_tickets(state: str | None = None, q: str | None = None,
                 user: dict = Depends(intake_access)) -> dict:
    tickets = _load()["tickets"]
    if state:
        tickets = [t for t in tickets if t["state"] == state]
    if q:
        needle = q.lower()
        tickets = [t for t in tickets
                   if needle in (t["title"] + t["description"] + t["dc_code"]
                                 + t["raiser"] + t["disposition"]).lower()]
    # Open first, then most recently touched — the order an agent wants to work in.
    tickets = sorted(tickets, key=lambda t: (t["state"] != "open", t["updated_at"]),
                     reverse=False)
    counts: dict[str, int] = {}
    for t in _load()["tickets"]:
        counts[t["state"]] = counts.get(t["state"], 0) + 1
    return {"tickets": [_public(t) for t in tickets], "counts": counts,
            "total": len(_load()["tickets"])}


@router.patch("/api/intake/tickets/{ref}")
def update_ticket(ref: str, payload: dict = Body(...),
                  user: dict = Depends(intake_write)) -> dict:
    with _WRITE_LOCK:
        data = _load()
        target = next((t for t in data["tickets"] if t["ref"] == ref), None)
        if target is None:
            raise HTTPException(status_code=404, detail=f"no ticket {ref}")
        if "state" in payload:
            if payload["state"] not in STATES:
                raise HTTPException(status_code=400,
                                    detail=f"state must be one of {list(STATES)}")
            target["state"] = payload["state"]
        if "status_note" in payload:
            # Shown to the raiser on the status page, so it is their update, not an internal
            # note. Capped because that page has no scrollable body.
            target["status_note"] = str(payload["status_note"])[:400]
        target["updated_at"] = _now()
        target["updated_by"] = user.get("email")
        _save(data)
        return {"ok": True, "ticket": _public(target)}


#: Clearing the register is destructive and irreversible — there is no undo and no archive —
#: so it is approver-only and needs an explicit confirmation, not just the right role.
intake_admin = require_role("approver")


@router.delete("/api/intake/tickets/{ref}")
def delete_ticket(ref: str, user: dict = Depends(intake_admin)) -> dict:
    """Remove one ticket. Useful for the probe rows a readiness check leaves behind."""
    with _WRITE_LOCK:
        data = _load()
        before = len(data["tickets"])
        data["tickets"] = [t for t in data["tickets"] if t["ref"] != ref]
        if len(data["tickets"]) == before:
            raise HTTPException(status_code=404, detail=f"no ticket {ref}")
        _save(data)
    return {"ok": True, "deleted": ref}


@router.post("/api/intake/reset")
def reset_register(payload: dict = Body(...), user: dict = Depends(intake_admin)) -> dict:
    """Empty the register. For testing, before a demo, or after a bad import.

    Requires {"confirm": "delete all tickets"} in the body. A destructive action reachable by
    one click, or by a stray request, is one that eventually happens by accident — and the
    partner-facing status links die with the tickets they point at.
    """
    if (payload or {}).get("confirm") != "delete all tickets":
        raise HTTPException(
            status_code=400,
            detail='refusing: send {"confirm": "delete all tickets"} to mean it')
    with _WRITE_LOCK:
        data = _load()
        n = len(data["tickets"])
        _save({"tickets": []})
    durable_path(CHANNELS).write_text(json.dumps({"channels": [], "updated_at": _now()}))
    return {"ok": True, "deleted": n}


@router.get("/t/{token}", response_class=HTMLResponse)
def status_page(token: str) -> HTMLResponse:
    """One ticket, for the person who raised it. No login — see the module docstring."""
    for t in _load()["tickets"]:
        if secrets.compare_digest(t.get("status_token", ""), token):
            return HTMLResponse(_render(t))
    return HTMLResponse(
        '<body style="font:15px -apple-system,sans-serif;padding:36px;color:#1f2328">'
        "<p>No ticket matches this link.</p><p style='color:#59636e;font-size:13.5px'>"
        "Raising the issue again in the channel will create a fresh one.</p></body>",
        status_code=404)


def _render(t: dict) -> str:
    e = html.escape
    label = {"open": "Received", "in_progress": "Being worked on",
             "resolved": "Resolved"}.get(t["state"], "Received")
    colour = {"open": "#0969da", "in_progress": "#9a6700",
              "resolved": "#1a7f37"}.get(t["state"], "#0969da")
    occ = int(t.get("occurrence_count") or 1)
    rows = [("Raised by", t.get("raiser") or "—"),
            ("First raised", (t.get("first_raised_at") or "")[:16] or "—")]
    if occ > 1:
        rows.append(("Times raised", f"<b>{occ}</b> — counted as one ticket, not several"))
    if t.get("dc_code"):
        rows.append(("DC", e(t["dc_code"])))
    rows.append(("Category", e(t.get("disposition") or "being categorised")))
    rows.append(("Last updated", (t.get("updated_at") or "")[:16]))
    body = "".join(f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in rows)
    note = e(t["status_note"]) if t.get("status_note") else (
        "Your message was picked up automatically and a ticket was created. "
        "This page updates as the ticket moves.")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ticket {e(t['ref'])}</title><style>
body{{font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:0;
background:#f6f8fa;color:#1f2328}}
.w{{max-width:620px;margin:0 auto;padding:28px 18px}}
.c{{background:#fff;border:1px solid #d1d9e0;border-radius:12px;padding:22px 24px}}
.ref{{font:600 13px ui-monospace,Menlo,monospace;color:#59636e}}
h1{{font-size:19px;margin:6px 0 14px;line-height:1.35}}
.pill{{display:inline-block;padding:4px 12px;border-radius:999px;color:#fff;font-size:12.5px;
font-weight:600;background:{colour}}}
table{{border-collapse:collapse;width:100%;margin-top:18px;font-size:13.5px}}
td{{padding:7px 0;border-top:1px solid #eaeef2;vertical-align:top}}
td:first-child{{color:#59636e;width:40%}}
.note{{margin-top:16px;padding:12px 14px;background:#f6f8fa;border-radius:8px;font-size:13.5px}}
.f{{margin-top:16px;font-size:12px;color:#59636e}}
</style></head><body><div class="w"><div class="c">
<div class="ref">{e(t['ref'])}</div><h1>{e(t.get('title') or '')}</h1>
<span class="pill">{label}</span><table>{body}</table>
<div class="note">{note}</div>
<div class="f">Keep this link to check back. It shows only this ticket.</div>
</div></div></body></html>"""
