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
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import HTMLResponse

from .auth.deps import current_user, require_role
from .durable_state import durable_path, read_json_from

router = APIRouter()

STORE = "intake_tickets.json"
CHANNELS = "intake_channels.json"

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

    data = _load()
    for t in data["tickets"]:
        if t["idempotency_key"] == key:
            # ALREADY EXISTS. Return the original reference and its original token, so a
            # retried acknowledgement points at the same page rather than a second one.
            return {"ok": True, "ref": t["ref"], "created": False,
                    "status_token": t["status_token"]}

    ref = f"VAL-{len(data['tickets']) + 1}"
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
        "created_at": _now(),
        "updated_at": _now(),
        "created_by": user.get("email"),
    }
    data["tickets"].append(ticket)
    _save(data)
    return {"ok": True, "ref": ref, "created": True, "status_token": ticket["status_token"]}


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
    durable_path(CHANNELS).write_text(json.dumps(
        {"channels": rows, "updated_at": _now(), "updated_by": user.get("email")},
        indent=1, ensure_ascii=False))
    return {"ok": True, "channels": len(rows)}


@router.get("/api/intake/channels")
def list_channels(user: dict = Depends(intake_access)) -> dict:
    return read_json_from(durable_path(CHANNELS), {"channels": [], "updated_at": None})


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
    data = _load()
    for t in data["tickets"]:
        if t["ref"] != ref:
            continue
        if "state" in payload:
            if payload["state"] not in STATES:
                raise HTTPException(status_code=400,
                                    detail=f"state must be one of {list(STATES)}")
            t["state"] = payload["state"]
        if "status_note" in payload:
            # Shown to the raiser on the status page, so it is their update, not an internal
            # note. Capped because that page has no scrollable body.
            t["status_note"] = str(payload["status_note"])[:400]
        t["updated_at"] = _now()
        t["updated_by"] = user.get("email")
        _save(data)
        return {"ok": True, "ticket": _public(t)}
    raise HTTPException(status_code=404, detail=f"no ticket {ref}")


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
