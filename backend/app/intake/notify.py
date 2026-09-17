"""Telling the raiser their issue was captured. The first thing in this pipeline that reaches
a real person, which is why most of this file is refusals rather than sending.

    python -m app.intake.cli notify --run-id live --notify email --notify-to you@meesho.com

── WHY THIS EXISTS ───────────────────────────────────────────────────────────────────────────
Until a raiser hears something, a captured ticket is indistinguishable from being ignored — and
being ignored is the failure this project exists to fix. A reaction confirms capture and carries
no information; a channel reply is noise. So: a short message with a link to a page showing THAT
ticket's status, and nothing else.

── EMAIL FIRST BECAUSE IT NEEDS NO NEW SLACK SCOPE ───────────────────────────────────────────
`author_email` already arrives from `users:read.email`, which is granted. So acknowledgement can
ship without a reinstall, without an approval, and without the app gaining the ability to post
anywhere. A Slack DM is the better experience and is a config swap behind this same interface
once `chat:write` + `im:write` land.

── THE FIVE GUARDS, AND WHY EACH ONE IS NOT OPTIONAL ─────────────────────────────────────────
Sending is off by default, and every guard below assumes the operator has made a mistake:

1. SEND ONCE, EVER. The `notifications` table is keyed on (idempotency_key, channel) and is NOT
   scoped to a run. The live dashboard re-runs the pipeline every few seconds; without this the
   same partner is emailed every few seconds. That is the seven-canned-replies failure rebuilt
   by accident and pointed at real people.
2. A REDIRECT THAT CANNOT BE FORGOTTEN. `--notify-to` sends everything to one address instead of
   the real raisers, and the real recipient is recorded so a demo run is auditable afterwards.
   Demoing against a live channel without it would mail actual partners.
3. AN EXPLICIT ALLOWLIST FOR REAL SENDING. With no redirect, only domains in `allow_domains`
   are written to. An empty allowlist sends to nobody — fail closed, never open.
4. A HARD CAP PER RUN. A grouping bug that turns 1 issue into 400 must cost 20 emails, not 400.
5. NOTHING WITHOUT A STATUS LINK. An acknowledgement with no destination is the thing that was
   already rejected; a draft with no `status_url` is skipped rather than sent bare.

── PII ───────────────────────────────────────────────────────────────────────────────────────
The message contains the partner's own words back to them, which is the one recipient for whom
that is not a disclosure. Nothing about any other ticket appears, and the status page shows one
ticket only.
"""
from __future__ import annotations

import os
import re
import smtplib
import sqlite3
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from . import store

_BACKEND = Path(__file__).resolve().parents[2]
#: App Password for Gmail / Workspace. Mode 600, gitignored by `backend/data/*.txt`.
SMTP_PASS_FILE = _BACKEND / "data" / "smtp_password.txt"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


class NotifyError(RuntimeError):
    pass


@dataclass
class SmtpConfig:
    host: str = "smtp.gmail.com"
    port: int = 587
    username: str = ""
    password: str = ""
    from_name: str = "Valmo Partner Support"
    from_addr: str = ""          # defaults to username

    @classmethod
    def from_env(cls) -> "SmtpConfig":
        pw = os.environ.get("SMTP_PASSWORD") or (
            SMTP_PASS_FILE.read_text(encoding="utf-8").strip()
            if SMTP_PASS_FILE.exists() else "")
        user = os.environ.get("SMTP_USERNAME", "")
        return cls(
            host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            port=int(os.environ.get("SMTP_PORT", "587")),
            username=user, password=pw,
            from_name=os.environ.get("SMTP_FROM_NAME", "Valmo Partner Support"),
            from_addr=os.environ.get("SMTP_FROM", user))


def _body(title: str, ref: str, status_url: str, occurrences: int) -> tuple[str, str]:
    """Plain text and HTML. Short on purpose — it exists to carry the link."""
    again = (f"\n\nThis has now been raised {occurrences} times. It is counted as one ticket, "
             f"not several, so the repeat count is visible to whoever picks it up."
             if occurrences > 1 else "")
    text = (
        f"We picked this up from your message and created a ticket.\n\n"
        f"  {ref}  {title}\n\n"
        f"Track it here — the page shows this ticket only:\n{status_url}\n"
        f"{again}\n\n"
        f"You do not need to reply to this email. Raising it again in the channel will add to "
        f"the same ticket rather than starting a new one.\n")
    html = (
        f'<div style="font:15px/1.55 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;'
        f'color:#1f2328;max-width:560px">'
        f'<p>We picked this up from your message and created a ticket.</p>'
        f'<p style="margin:18px 0;padding:14px 16px;background:#f6f8fa;border-radius:8px">'
        f'<span style="font:600 13px ui-monospace,Menlo,monospace;color:#59636e">{ref}</span>'
        f'<br>{_esc(title)}</p>'
        f'<p><a href="{_esc(status_url)}" style="display:inline-block;padding:9px 16px;'
        f'background:#0969da;color:#fff;border-radius:7px;text-decoration:none;font-weight:600">'
        f'Track this ticket</a></p>'
        + (f'<p style="color:#59636e;font-size:13.5px">Raised <b>{occurrences}</b> times — '
           f'counted as one ticket, not several.</p>' if occurrences > 1 else "")
        + f'<p style="color:#59636e;font-size:13px">No reply needed. Raising it again in the '
          f'channel adds to the same ticket rather than starting a new one.</p></div>')
    return text, html


def _esc(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


class EmailNotifier:
    """SMTP acknowledgement. `dry_run=True` renders and records nothing sent."""

    channel = "email"

    def __init__(self, cfg: SmtpConfig | None = None, *, dry_run: bool = True,
                 allow_domains: tuple[str, ...] = (), redirect_to: str | None = None):
        self.cfg = cfg or SmtpConfig.from_env()
        self.dry_run = dry_run
        self.allow_domains = tuple(d.lower().lstrip("@") for d in allow_domains)
        self.redirect_to = redirect_to
        self.sent = 0
        self.skipped: list[str] = []

    def _permitted(self, addr: str) -> tuple[bool, str]:
        if not addr or not _EMAIL_RE.match(addr):
            return False, "no usable email address"
        if self.redirect_to:
            return True, ""                      # redirect is its own containment
        if not self.allow_domains:
            # Fail CLOSED. An empty allowlist with no redirect means somebody wired real sending
            # without saying who may receive it, and the safe reading of that is "nobody".
            return False, ("no allow_domains configured and no --notify-to redirect — refusing "
                           "to write to a real address")
        dom = addr.rsplit("@", 1)[-1].lower()
        if dom not in self.allow_domains:
            return False, f"{dom} is not in the allowlist"
        return True, ""

    def send(self, *, to: str, subject: str, text: str, html: str) -> str:
        target = self.redirect_to or to
        if self.dry_run:
            return target
        if not self.cfg.username or not self.cfg.password:
            raise NotifyError(
                "SMTP is not configured. Set SMTP_USERNAME and put the App Password in "
                f"{SMTP_PASS_FILE} (chmod 600), or export SMTP_PASSWORD.")
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = f"{self.cfg.from_name} <{self.cfg.from_addr or self.cfg.username}>"
        msg["To"] = target
        msg["Auto-Submitted"] = "auto-generated"     # keeps it out of vacation-responder loops
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        # certifi's bundle when it is installed. A stock python.org build on macOS ships no
        # OS trust store link, so ssl.create_default_context() finds no issuer and EVERY send
        # fails with CERTIFICATE_VERIFY_FAILED — which is what happened here, ten times, and
        # looked like "no mail arrived" rather than like a TLS problem.
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()
        with smtplib.SMTP(self.cfg.host, self.cfg.port, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(self.cfg.username, self.cfg.password)
            s.send_message(msg)
        return target


def run(run_id: str, notifier, *, con: sqlite3.Connection | None = None,
        status_urls: dict[str, str] | None = None, max_sends: int = 20) -> dict:
    """Acknowledge every ticket in this run that has not already been acknowledged.

    Idempotent by construction: a ticket already present in `notifications` for this channel is
    skipped, so re-running — which the live dashboard does every few seconds — sends nothing.
    """
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    own = con is None
    con = con or store.connect()
    status_urls = status_urls or {}
    try:
        rows = con.execute(
            "SELECT d.idempotency_key, d.title, d.external_ref, d.occurrence_count, "
            "       d.suppressed, m.author_email, m.author_name "
            "FROM ticket_drafts d "
            "JOIN issues i ON i.run_id=d.run_id AND i.issue_id=d.issue_id "
            "JOIN messages m ON m.channel_id=i.anchor_channel_id "
            "                AND m.message_id=i.anchor_message_id "
            "WHERE d.run_id=?", (run_id,)).fetchall()

        already = {r[0] for r in con.execute(
            "SELECT idempotency_key FROM notifications WHERE channel=?", (notifier.channel,))}

        stats = {"considered": len(rows), "sent": 0, "already_acknowledged": 0,
                 "skipped": 0, "failed": 0, "capped": False, "reasons": {}}

        def skip(why: str) -> None:
            stats["skipped"] += 1
            stats["reasons"][why] = stats["reasons"].get(why, 0) + 1

        for r in rows:
            key = r["idempotency_key"]
            if r["suppressed"]:
                skip("ticket was held back, so there is nothing to acknowledge")
                continue
            if key in already:
                # The guarantee. Not an error — it is why a five-second poll is safe.
                stats["already_acknowledged"] += 1
                continue
            url = status_urls.get(key)
            if not url:
                skip("no status link — an acknowledgement with nowhere to look is what we "
                     "already rejected")
                continue
            ok, why = notifier._permitted(r["author_email"] or "")
            if not ok:
                skip(why)
                continue
            if stats["sent"] >= max_sends:
                # A grouping bug that turns 1 issue into 400 must cost 20 emails, not 400.
                stats["capped"] = True
                break

            text, html = _body(r["title"] or "your issue", r["external_ref"] or "",
                               url, r["occurrence_count"] or 1)
            subject = f"{r['external_ref'] or 'Ticket'} — we have your issue"
            err = None
            try:
                target = notifier.send(to=r["author_email"], subject=subject,
                                       text=text, html=html)
                stats["sent"] += 1
            except Exception as e:                                        # noqa: BLE001
                target, err = r["author_email"], f"{type(e).__name__}: {e}"
                stats["failed"] += 1

            if not notifier.dry_run:
                con.execute(
                    "INSERT OR REPLACE INTO notifications (idempotency_key, channel, recipient, "
                    "status_url, sent_at, ok, error, redirected_from) VALUES (?,?,?,?,?,?,?,?)",
                    (key, notifier.channel, target, url,
                     datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     0 if err else 1, err,
                     r["author_email"] if notifier.redirect_to else None))
        con.commit()

        stats["_mode"] = ("DRY RUN — nothing was sent and nothing was recorded"
                          if notifier.dry_run else
                          f"LIVE — redirected to {notifier.redirect_to}"
                          if notifier.redirect_to else "LIVE — sent to real raisers")
        con.execute(
            "INSERT OR REPLACE INTO stage_runs (run_id, stage, started_at, finished_at, "
            "rows_in, rows_out, llm_calls, cost_usd, config_hash, status) VALUES "
            "(?,?,?,?,?,?,0,0.0,?,'ok')",
            (run_id, "notify", started,
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             len(rows), stats["sent"], f"channel={notifier.channel},dry={notifier.dry_run}"))
        con.commit()
        return stats
    finally:
        if own:
            con.close()
