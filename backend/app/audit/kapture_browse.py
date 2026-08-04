"""Kapture evidence puller — READ-ONLY browse access to the Kapture CRM for audit evidence.

WHY THIS EXISTS
The audit judge can only see the email text it is given, so it cannot verify the *correctness /
process* fatals (correct tagging, correct reversal, correct queue, whether a quoted TAT matched the
SOP). Those live in the CRM, not in the email. This module fetches exactly that evidence, per
ticket, so those checks can become real.

HOW (architecture reused from the sibling valmo-l1-agent, which proved it against this tenant)
Kapture exposes no partner API to us, but its own UI is driven by internal XHR endpoints. So we use
Playwright purely as an AUTHENTICATED SESSION CARRIER and call those endpoints from inside the page
(`page.evaluate(fetch(...))`), which yields structured JSON instead of brittle scraped DOM:

    POST /api/version3/ticket/get-ticket-list      -> resolve ticket_number -> internal task_id
    POST /api/version3/ticket/get-ticket-detail    -> data_type = ticket | email | conversations
                                                                 | notes | history | additional_info

Two hard-won details from the sibling repo, both handled here:
  • EVERY ticket has TWO ids — the display `ticket_number` and an internal `task_id`. The detail
    endpoints need BOTH, so resolution is a mandatory first step.
  • `networkidle` NEVER fires on Kapture (long-poll XHRs), so navigation must use `domcontentloaded`.

SAFETY POSTURE — this module is deliberately READ-ONLY.
  • Only get-ticket-list / get-ticket-detail are ever called. The write endpoints that exist in the
    sibling repo (send-email, update-status) are NOT imported, NOT referenced, and are explicitly
    blocked by `_assert_read_only()`. An audit run can never email a partner or close a ticket.
  • Credentials come from env or a gitignored file; they are never logged. `status()` reports only
    whether a credential is PRESENT.
  • Every payload is PII-redacted at extraction (reusing the audit engine's `_redact`), and raw
    responses are never persisted.
  • Login needs a ONE-TIME headed OTP (Kapture sends an OTP after the password step). That seeds a
    storage-state file; every later run is headless. Fully-unattended cold start is impossible by
    design — documented, not worked around.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .kapture import _redact

# ── config (values never logged) ──────────────────────────────────────────────
_URL_ENV = "KAPTURE_URL"
_EMAIL_ENV = "KAPTURE_EMAIL"
_PASSWORD_ENV = "KAPTURE_PASSWORD"
_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_CREDS_FILE = _DATA_DIR / "kapture_creds.txt"
# Deliberately a LOCAL file, not durable_state: a browser cookie jar is a machine-local credential
# cache, so it must not be synced into the shared durable store. Gitignored.
_SESSION_FILE = _DATA_DIR / "kapture_session.json"

_NAV_TIMEOUT = 45_000
_XHR_TIMEOUT = 30_000
_OTP_WAIT_MS = 300_000        # 5 min for a human to type the OTP in the headed browser
_SETTLE_MS = 2_500            # fixed settle instead of networkidle (which never fires here)

# The ONLY endpoints this module may call. Anything not on this list is refused.
_ALLOWED_ENDPOINTS = (
    "/api/version3/ticket/get-ticket-list",
    "/api/version3/ticket/get-ticket-detail",
    "/api/version3/ticket/get-ticket-conversations",
)
# Write endpoints that must NEVER be reachable from an audit run.
_FORBIDDEN = ("send-email", "update-status", "get-send-email-screen", "dispose", "delete", "create")

# Evidence surfaces we pull per ticket → which audit gate each one serves.
EVIDENCE_TYPES = {
    "ticket":          "core fields (subject, status, queue) — context",
    "email":           "the SENT email thread incl. signature — opening/closing + email_flow",
    "conversations":   "full conversation thread — completeness of the reply",
    "notes":           "CRM notes + <dispose> drafts — fatal_crm_utilization",
    "history":         "action/status timeline — fatal_incorrect_reversal, fatal_assignment",
    "additional_info": "custom fields incl. sub-type/sub-sub-type — fatal_tagging",
}


class KaptureBrowseError(RuntimeError):
    """A browse/auth/transport failure. Never contains a credential."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code or ""


def _assert_read_only(path: str) -> None:
    """Hard guard: refuse any endpoint that is not an explicitly allowed READ endpoint."""
    low = (path or "").lower()
    if any(w in low for w in _FORBIDDEN):
        raise KaptureBrowseError(f"refused: '{path}' is a write endpoint; this module is read-only",
                                 code="read_only_violation")
    if not any(low.startswith(a) for a in _ALLOWED_ENDPOINTS):
        raise KaptureBrowseError(f"refused: '{path}' is not an allowed read endpoint",
                                 code="endpoint_not_allowed")


def _creds() -> tuple[str, str, str]:
    """(base_url, email, password) from env, else the gitignored creds file (KEY=VALUE lines)."""
    base = os.environ.get(_URL_ENV, "").strip().rstrip("/")
    email = os.environ.get(_EMAIL_ENV, "").strip()
    pwd = os.environ.get(_PASSWORD_ENV, "")
    if (not base or not email or not pwd) and _CREDS_FILE.exists():
        for line in _CREDS_FILE.read_text().splitlines():
            if "=" not in line or line.strip().startswith("#"):
                continue
            k, _, v = line.partition("=")
            k, v = k.strip().upper(), v.strip()
            if k == _URL_ENV and not base:
                base = v.rstrip("/")
            elif k == _EMAIL_ENV and not email:
                email = v
            elif k == _PASSWORD_ENV and not pwd:
                pwd = v
    return base, email, pwd


def status() -> dict:
    """Non-throwing config probe for /api/health. Reports PRESENCE only, never values."""
    base, email, pwd = _creds()
    try:
        import playwright  # noqa: F401
        pw = True
    except ImportError:
        pw = False
    return {
        "configured": bool(base and email and pwd),
        "base_url_host": (re.sub(r"^https?://", "", base).split("/")[0] if base else ""),
        "credentials_present": bool(email and pwd),
        "session_cached": _SESSION_FILE.exists(),
        "playwright_installed": pw,
        "read_only": True,
        "evidence_types": sorted(EVIDENCE_TYPES),
    }


# ── session ───────────────────────────────────────────────────────────────────
def _needs_login(page) -> bool:
    """Form-based check (NOT url-based): if the username field renders, we are not authenticated."""
    try:
        page.wait_for_selector('input[name="userName"]', timeout=6_000, state="visible")
        return True
    except Exception:  # noqa: BLE001 — absence of the form is the success signal
        return False


def _save_session(context) -> None:
    context.storage_state(path=str(_SESSION_FILE))


def login(page, headless: bool) -> None:
    """Authenticate, reusing a cached session when possible.

    A cached storage-state skips the OTP entirely. On a cold start Kapture requires an OTP that only
    a human can supply, so headless MUST fail loudly rather than hang forever."""
    base, email, pwd = _creds()
    if not (base and email and pwd):
        raise KaptureBrowseError(
            f"Kapture not configured — set {_URL_ENV}/{_EMAIL_ENV}/{_PASSWORD_ENV} "
            f"or create {_CREDS_FILE.name} (gitignored).", code="not_configured")

    page.goto(f"{base}/nui/tickets/list/7/-1/0", wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
    if not _needs_login(page):
        _save_session(page.context)          # refresh the cached cookies
        return

    page.goto(f"{base}/nui/login", wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
    page.fill('input[name="userName"]', email)
    page.fill('input[type="password"]', pwd)
    page.click('button:has-text("Log in")')

    try:   # credentials accepted → Kapture then asks for an OTP
        page.wait_for_function("() => !window.location.href.includes('/nui/login')", timeout=15_000)
    except Exception:  # noqa: BLE001
        if headless:
            raise KaptureBrowseError(
                "Kapture requires an OTP and no cached session exists. Run the one-time headed "
                "bootstrap first:  python scripts/kapture_login.py", code="otp_required") from None
        page.wait_for_function("() => !window.location.href.includes('/nui/login')",
                               timeout=_OTP_WAIT_MS)   # human types the OTP

    if _needs_login(page):
        raise KaptureBrowseError("login failed (still on the login form)", code="login_failed")
    _save_session(page.context)


# ── the in-page XHR primitive ─────────────────────────────────────────────────
_FETCH_JS = """
async ([path, body, isForm]) => {
  const opts = { method: 'POST', credentials: 'include' };
  if (isForm) {
    opts.headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
    opts.body = new URLSearchParams(body).toString();
  } else {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(path, opts);
  const text = await r.text();
  try { return { status: r.status, json: JSON.parse(text) }; }
  catch (e) { return { status: r.status, text: text.slice(0, 2000) }; }
}
"""


def _xhr(page, path: str, body: dict) -> dict:
    """POST an ALLOWED Kapture endpoint from inside the authenticated page.

    Arguments are passed through page.evaluate's argument channel (never string-interpolated into
    the JS), so a ticket number can't inject script."""
    _assert_read_only(path)
    page.set_default_timeout(_XHR_TIMEOUT)
    res = page.evaluate(_FETCH_JS, [path, body, True])
    if not isinstance(res, dict):
        raise KaptureBrowseError(f"unexpected XHR result for {path}", code="bad_response")
    if res.get("status") != 200:
        raise KaptureBrowseError(f"{path} returned HTTP {res.get('status')}", code=str(res.get("status")))
    return res.get("json") or {}


def resolve_task_id(page, ticket_number: str) -> dict | None:
    """ticket_number → {task_id, ticket_id, subject, status, queue}.

    Uses the BROAD elastic search (folder_id=-1, isElasticSearch=true, no type/status filter): the
    narrow agent-view search does NOT return closed tickets, which is exactly what an audit needs.
    """
    tn = str(ticket_number).strip()
    if not tn or not re.fullmatch(r"[A-Za-z0-9_\-/]{3,40}", tn):
        raise KaptureBrowseError(f"implausible ticket number {tn!r}", code="bad_ticket_number")
    body = {
        "sort_by_column": "last_conversation_time", "folder_id": "-1", "query": tn,
        "page_no": "0", "sort_type": "desc", "page_size": "10",
        "response_type": "json", "key_beautify": "yes", "isElasticSearch": "true",
    }
    data = _xhr(page, "/api/version3/ticket/get-ticket-list", body)
    tickets = (data.get("response") or data).get("tickets") or []
    for t in tickets:
        if str(t.get("ticketId", "")).strip() == tn:
            return {"task_id": str(t.get("id", "")), "ticket_id": tn,
                    "subject": _clean_pii(str(t.get("title") or t.get("subject") or "")[:300]),
                    "status": str(t.get("status", "")), "queue": str(t.get("queueKey") or "")}
    return None


def fetch_evidence(page, task_id: str, ticket_id: str, types: tuple[str, ...] = ()) -> dict:
    """Pull the audit-evidence surfaces for one ticket. Each is best-effort: one failing data_type
    must not lose the others, so failures are recorded per type rather than raised."""
    variants = {
        "ticket":          {"data_type": "ticket", "skip_unread_action": "no"},
        "email":           {"data_type": "email", "last_con_id": "0", "last_con_type": "E"},
        "conversations":   {"data_type": "conversations", "last_con_id": "0"},
        "notes":           {"data_type": "notes"},
        "history":         {"data_type": "history", "fetch_action_name": "yes"},
        "additional_info": {"data_type": "additional_info", "status": "C",
                            "last_con_id": "0", "last_con_type": "O"},
    }
    wanted = types or tuple(variants)
    out: dict = {"task_id": str(task_id), "ticket_id": str(ticket_id), "errors": {}}
    for name in wanted:
        extra = variants.get(name)
        if extra is None:
            continue
        try:
            out[name] = _xhr(page, "/api/version3/ticket/get-ticket-detail",
                             {"id": str(task_id), "ticket_id": str(ticket_id), **extra})
        except KaptureBrowseError as e:
            out["errors"][name] = e.code or "error"
    return out


# ── parsing: raw payloads → the fields the audit gates need ───────────────────
_DISPOSE_RE = re.compile(r"</?dispose>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")

# Personal NAMES are PII and `_redact` (email/phone/long-id) does not cover them. AWBs deliberately
# survive — this codebase treats them as join keys, not PII (build_valmo_db.py: "never drop the join
# keys"), and the audit needs the AWB to verify what an agent told a partner.
_BRAND = r"(?:partner|team|sir|madam|captain|support|valmo|meesho|customer)"
_SALUTE_NAME = re.compile(rf"\b(dear|hi|hello|respected)\s+(?!{_BRAND}\b)([A-Z][a-z]{{2,}})", re.I)
_SIGNOFF_NAME = re.compile(
    rf"\b(regards|thanks|thank you|sincerely)\s*,?\s*\n+\s*(?!{_BRAND}\b)([A-Z][a-z]{{2,}}"
    rf"(?:\s+[A-Z][a-z]+)?)", re.I)


def _scrub_names(s: str) -> str:
    """Mask personal names in salutations / sign-offs while preserving brand sign-offs (so the
    signature check still works)."""
    s = _SALUTE_NAME.sub(lambda m: f"{m.group(1)} [name]", s)
    return _SIGNOFF_NAME.sub(lambda m: f"{m.group(1)},\n[name]", s)


def _clean_pii(s: str) -> str:
    """Full redaction for stored/returned evidence text: emails, phones, long ids, then names."""
    return _scrub_names(_redact(s))


def _clean(html: str, keep_breaks: bool = True) -> str:
    """Strip markup but KEEP the text — crucially including the signature block, which the sibling
    repo strips and which our opening/closing check needs."""
    s = _DISPOSE_RE.sub("", str(html or ""))
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>", "\n", s, flags=re.I)
    s = _TAG_RE.sub("", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
          .replace("​", "").replace("﻿", ""))
    s = re.sub(r"[ \t]{2,}", " ", s)
    return (re.sub(r"\n{3,}", "\n\n", s) if keep_breaks else re.sub(r"\s+", " ", s)).strip()


def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return None


def parse_evidence(ev: dict) -> dict:
    """Normalise the raw payloads into the audit-facing shape. Everything is redacted."""
    out: dict = {"ticket_id": ev.get("ticket_id"), "task_id": ev.get("task_id"),
                 "errors": ev.get("errors") or {}}

    # ── the sent email thread (signature preserved) ──
    msgs = []
    for src in ("email", "conversations"):
        blob = ev.get(src) or {}
        node = blob.get("response") if isinstance(blob.get("response"), (dict, list)) else blob
        items = []
        if isinstance(node, dict):
            for key in ("conversations", "emails", "list", "data", "messages"):
                if isinstance(node.get(key), list):
                    items = node[key]
                    break
        elif isinstance(node, list):
            items = node
        for it in items:
            if not isinstance(it, dict):
                continue
            body = _clean(_first(it, "body", "message", "emailBody", "email_body", "detail",
                                 "description") or "")
            if not body:
                continue
            ctype = str(_first(it, "conversationType", "type", "conType") or "").upper()
            # Kapture: 'S' = sent by agent, 'R' = received from the partner
            role = "agent" if ctype.startswith("S") else "partner" if ctype.startswith("R") else ""
            frm = str(_first(it, "fromAddress", "fromEmail", "senderEmail", "from") or "")
            if not role and frm:
                role = "agent" if ("@valmo" in frm.lower() or "@meesho" in frm.lower()) else "partner"
            msgs.append({"role": role or "unknown",
                         "at": str(_first(it, "date", "createdAt", "created_at", "time") or ""),
                         "body": _clean_pii(body[:6000])})
    out["thread"] = msgs
    agent_msgs = [m for m in msgs if m["role"] == "agent"]

    # ── notes (fatal_crm_utilization) ──
    notes_blob = (ev.get("notes") or {})
    nnode = notes_blob.get("response") if isinstance(notes_blob.get("response"), dict) else notes_blob
    raw_notes = []
    if isinstance(nnode, dict):
        for key in ("notes", "list", "data"):
            if isinstance(nnode.get(key), list):
                raw_notes = nnode[key]
                break
    notes = []
    for n in raw_notes:
        if not isinstance(n, dict):
            continue
        detail = str(_first(n, "detail", "note", "body", "description") or "")
        notes.append({"is_dispose_draft": "<dispose" in detail.lower(),
                      "by": "partner" if str(n.get("creatorName", "")).lower() == "customer" else "agent",
                      "at": str(_first(n, "date", "createdAt") or ""),
                      "text": _clean_pii(_clean(detail)[:1500])})
    out["notes"] = notes
    out["note_count"] = len(notes)

    # ── THE REPLY + ITS CHANNEL (settles the hybrid opening/closing rule from CRM fact) ──
    # VERIFIED on the live tenant: since the team moved off email replies, the agent's actual reply
    # to the partner is the latest <dispose> NOTE (Kapture mails it out without the email template's
    # greeting/signature). Email-format replies still appear in the email/conversations thread. So
    # the channel is determined by WHERE the reply came from — not guessed from the text.
    dispose_notes = [n for n in notes if n["is_dispose_draft"] and n["text"].strip()]
    if agent_msgs:
        out["agent_reply"] = agent_msgs[-1]["body"]
        out["reply_channel"] = "email"
    elif dispose_notes:
        out["agent_reply"] = dispose_notes[-1]["text"]
        out["reply_channel"] = "notes"
    else:
        out["agent_reply"] = ""
        out["reply_channel"] = "none"
    out["agent_reply_count"] = len(agent_msgs) + len(dispose_notes)
    # A real email-format reply carries the brand signature block; a notes reply cannot.
    out["has_signature"] = bool(re.search(
        r"(valmo\s+partner\s+support|team\s+valmo|valmo\s+support)", out["agent_reply"], re.I))
    # What the audit should do with opening/closing + email_flow for THIS ticket.
    out["email_format_applicable"] = out["reply_channel"] == "email" or out["has_signature"]

    # ── tagging (fatal_tagging) ──
    tkt_blob = (ev.get("ticket") or {})
    tnode = tkt_blob.get("response") if isinstance(tkt_blob.get("response"), dict) else tkt_blob
    tobj = tnode.get("ticket") if isinstance(tnode.get("ticket"), dict) else tnode
    folders = tobj.get("folders") if isinstance(tobj.get("folders"), list) else []
    # VERIFIED against the live tenant: the disposition path lives in `folders`
    # (e.g. ['Web Form','Captain','Losses & Debits','<L4 detail>']) and `taskTitle` is the
    # pipe-delimited subject; `detail` holds the partner's own message.
    out["tagging"] = {
        "status": str(_first(tobj, "statusName", "status") or ""),
        "substatus": str(_first(tobj, "substatusName", "substatus") or ""),
        "queue": str(_first(tobj, "queueName", "queueKey", "currentQueueName") or ""),
        "folders": [str(f)[:80] for f in folders if str(f).strip()][:6],
        "sub_type": "", "sub_sub_type": "",
    }
    out["task_title"] = _clean_pii(str(tobj.get("taskTitle") or "")[:300])
    out["partner_message"] = _clean_pii(_clean(str(tobj.get("detail") or ""))[:4000])
    # custom fields carry sub-type / sub-sub-type; field IDs are TENANT-SPECIFIC, so match by
    # displayName and fall back to the sibling repo's observed IDs.
    ai_blob = (ev.get("additional_info") or {})
    anode = ai_blob.get("response") if isinstance(ai_blob.get("response"), dict) else ai_blob
    # VERIFIED shape: response.{existing, fieldConfig} at the TOP level (not nested under
    # "additionalInfo"), and `existing` is a DICT keyed by group id -> {"fields": {fieldId: value}}.
    # Older/other tenants nest it, so both shapes are handled.
    addl = anode.get("additionalInfo") if isinstance(anode.get("additionalInfo"), dict) else anode
    fields: dict = {}
    existing = addl.get("existing")
    groups = existing.values() if isinstance(existing, dict) else (existing or [])
    for grp in groups:
        if isinstance(grp, dict) and isinstance(grp.get("fields"), dict):
            fields.update(grp["fields"])
    cfg = addl.get("fieldConfig") if isinstance(addl.get("fieldConfig"), dict) else {}
    named: dict[str, str] = {}
    for fid, val in fields.items():
        if not str(val).strip():
            continue                      # the form has ~40 fields; keep only the filled ones
        label = ""
        meta = cfg.get(str(fid))
        if isinstance(meta, dict):
            label = str(meta.get("displayName") or meta.get("columnName") or meta.get("name") or "")
        key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or f"field_{fid}"
        named[key] = _clean_pii(_clean(str(val), keep_breaks=False)[:400])
    for k, v in named.items():
        if "sub_sub" in k and v:
            out["tagging"]["sub_sub_type"] = v
        elif k.startswith("sub_type") and v:
            out["tagging"]["sub_type"] = v
    out["custom_fields"] = named

    # ── action timeline (fatal_incorrect_reversal / fatal_assignment) ──
    h_blob = (ev.get("history") or {})
    hnode = h_blob.get("response") if isinstance(h_blob.get("response"), dict) else h_blob
    raw_hist = []
    if isinstance(hnode, dict):
        for key in ("history", "list", "data", "actions"):
            if isinstance(hnode.get(key), list):
                raw_hist = hnode[key]
                break
    elif isinstance(hnode, list):
        raw_hist = hnode
    timeline = []
    for h in raw_hist:
        if not isinstance(h, dict):
            continue
        # VERIFIED live field names: action / createDate / remark (+ queueName on reassignments).
        action = str(_first(h, "action", "actionName", "activity") or "")
        detail = _clean(str(_first(h, "remark", "detail", "description", "remarks") or ""),
                        keep_breaks=False)
        timeline.append({"action": action[:80],
                         "at": str(_first(h, "createDate", "date", "createdAt", "time") or ""),
                         "queue": str(_first(h, "queueName", "queueKey") or "")[:40],
                         "status": str(h.get("status") or "")[:8],
                         "detail": _clean_pii(detail[:400])})
    out["timeline"] = timeline
    blob = " ".join(f"{t['action']} {t['detail']}" for t in timeline).lower()
    # Signals for the correctness gates — presence only; the judge still decides.
    out["signals"] = {
        "reversal_mentioned": bool(re.search(r"revers|refund|credit\s*note|debit\s*revers", blob)),
        "reassigned": bool(re.search(r"assign|transfer|queue\s*chang", blob)),
        "status_changed": bool(re.search(r"status|clos|resolv|dispos", blob)),
        "timeline_events": len(timeline),
    }
    return out


# ── the public one-shot API ───────────────────────────────────────────────────
def pull_evidence(ticket_numbers: list[str], headless: bool = True,
                  types: tuple[str, ...] = ()) -> dict[str, dict]:
    """Resolve + pull + parse evidence for a batch of ticket numbers. One browser, one login.

    Returns {ticket_number: parsed_evidence | {"error": ...}}. Requires a cached session when
    headless (see scripts/kapture_login.py for the one-time OTP bootstrap)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:   # noqa: BLE001
        raise KaptureBrowseError("playwright is not installed (pip install playwright && "
                                 "playwright install chromium)", code="no_playwright") from e
    base, _, _ = _creds()
    if not base:
        raise KaptureBrowseError(f"{_URL_ENV} not set", code="not_configured")

    out: dict[str, dict] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx_kwargs = {"storage_state": str(_SESSION_FILE)} if _SESSION_FILE.exists() else {}
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        try:
            login(page, headless=headless)
            for tn in ticket_numbers:
                try:
                    found = resolve_task_id(page, tn)
                    if not found:
                        out[str(tn)] = {"error": "ticket not found in Kapture", "code": "not_found"}
                        continue
                    page.wait_for_timeout(_SETTLE_MS)     # deliberate pacing: never hammer the CRM
                    raw = fetch_evidence(page, found["task_id"], found["ticket_id"], types)
                    parsed = parse_evidence(raw)
                    parsed["subject"] = found.get("subject", "")
                    parsed["kapture_status"] = found.get("status", "")
                    parsed["kapture_queue"] = found.get("queue", "")
                    out[str(tn)] = parsed
                except KaptureBrowseError as e:
                    out[str(tn)] = {"error": str(e), "code": e.code}
        finally:
            try:
                _save_session(context)
            except Exception:  # noqa: BLE001
                pass
            context.close()
            browser.close()
    return out
