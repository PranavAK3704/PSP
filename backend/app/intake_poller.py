"""The Slack poller, running inside PSP itself.

Without this, tickets only appear when somebody runs the CLI on a laptop — so the deployed
register looks broken every time a message is posted, when in fact nothing was listening. This
makes the deployment self-sufficient: it reads Slack, runs the pipeline, and writes tickets into
its own store, with nothing on anyone's machine.

── OFF UNLESS CONFIGURED ─────────────────────────────────────────────────────────────────────
Requires SLACK_BOT_TOKEN and INTAKE_CHANNELS. With either missing the thread is never started
and a line is logged saying which. A deployment that silently begins reading a Slack workspace
because a default was left on would be a bad surprise.

── WHAT IT WRITES ────────────────────────────────────────────────────────────────────────────
It calls `intake_api` in-process rather than POSTing to itself over HTTP: same process, same
store, no second credential to manage, and no loopback request that Render's router has to
serve. Tickets therefore land in `durable_state` and survive a restart like everything else.

── WHAT IT LOSES ON A RESTART, AND WHY THAT IS SAFE ──────────────────────────────────────────
The intake SQLite and the raw NDJSON live on the container filesystem, which Render's free tier
wipes. After a restart the poller re-reads Slack from scratch and re-runs the pipeline over the
same messages — and creates NO duplicate tickets, because the idempotency key is derived from
the source message (sha256 of source_system, channel_id, message_id) and `intake_api` refuses a
key it already holds. The rebuild costs a few seconds of Slack calls, not correctness.

── THE FREE TIER SLEEPS, AND THAT IS A REAL LIMIT ────────────────────────────────────────────
Render's free plan spins a web service down after ~15 minutes without traffic, and a spun-down
instance runs no threads — so polling stops until the next HTTP request wakes it. For a demo
this is usually invisible, because an open Intake page polls every 8 seconds and keeps the
instance awake. For anything real it is not good enough, and the fix is a paid instance or a
worker; `INTAKE_POLL_SECONDS` does not change it. Stated here rather than discovered later.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("psp.intake_poller")

#: Every knob, so the deployment is configured in one place.
ENV_TOKEN = "SLACK_BOT_TOKEN"
ENV_CHANNELS = "INTAKE_CHANNELS"          # comma-separated channel ids
ENV_EVERY = "INTAKE_POLL_SECONDS"         # default 60
ENV_ENABLED = "INTAKE_POLL_ENABLED"       # set to 0 to hold it off with the rest configured

#: Acknowledgement. OFF unless INTAKE_NOTIFY is set to `email`, because this is a server that
#: can mail real partners without anyone watching — the one thing here with consequences
#: outside the building.
ENV_NOTIFY = "INTAKE_NOTIFY"              # off (default) | email-dry | email
ENV_NOTIFY_TO = "INTAKE_NOTIFY_TO"        # redirect EVERY acknowledgement to this address
ENV_NOTIFY_ALLOW = "INTAKE_NOTIFY_ALLOW"  # comma-separated domains that may receive real mail
ENV_NOTIFY_MAX = "INTAKE_NOTIFY_MAX"      # hard cap per poll, default 10
#: Where the status link points. Without it the link is relative and useless in an email.
ENV_PUBLIC_URL = "PSP_PUBLIC_URL"

_thread: threading.Thread | None = None
_state: dict = {"polls": 0, "last_at": None, "last_error": None, "messages": 0, "issues": 0,
                "tickets": 0, "acknowledged": 0, "running": False, "channels": [],
                "notify": "off"}


def status() -> dict:
    """What the poller has been doing — surfaced on the Intake page so 'no new tickets' can be
    told apart from 'nothing is listening', which look identical otherwise.

    `running` is derived from the thread rather than from a flag set once at start: a flag says
    "running" forever, including after the thread has died, and a dead poller reporting health
    is worse than one reporting nothing.
    """
    return {**_state, "running": bool(_thread is not None and _thread.is_alive())}


def _channels() -> list[str]:
    raw = os.environ.get(ENV_CHANNELS, "")
    return [c.strip() for c in raw.split(",") if c.strip()]


#: How far back a cold start reads. The container filesystem is wiped on every restart, so
#: without a bound the first poll after a deploy re-reads the channel's entire history — and
#: Render's free plan explicitly reserves the right to suspend a service that "initiates an
#: uncommonly high volume of traffic", naming external API calls. Seven days comfortably covers
#: the longest grouping join window, so an issue is never anchored on a message that fell
#: outside the window while its siblings stayed in.
COLD_START_DAYS = 7


def _watermarks() -> dict:
    """Per-channel high-water mark, kept in the DURABLE store.

    The point of persisting it: after a restart the poller resumes from where it stopped instead
    of re-reading everything. Ephemeral storage would give a watermark that is forgotten exactly
    when it is needed.
    """
    from .durable_state import durable_path, read_json_from
    return read_json_from(durable_path("intake_watermarks.json"), {})


def _save_watermarks(w: dict) -> None:
    import json as _json
    from .durable_state import durable_path
    durable_path("intake_watermarks.json").write_text(_json.dumps(w, indent=1))


def _poll_once(channels: list[str], raw_dir: Path, run_id: str) -> dict:
    """One pull-and-process cycle. Imported lazily so the intake package is not a hard
    dependency of booting PSP — a broken intake import must not take the whole app down."""
    from .intake import (classify, dedupe, emit, evidence, extract, group, loadstage, noise,
                         qualify, register, rollup, slack_source, store)
    from .intake_sink import InProcessTicketSink

    reader = slack_source.SlackReader(pause=0.2)
    marks = _watermarks()
    cold = time.time() - COLD_START_DAYS * 86400
    got = 0
    for ch in channels:
        # Resume from the last message seen, or from the cold-start bound on a fresh container.
        # A backfill and a poll are the same call with a different cursor — which is why there
        # is no separate streaming path to keep in step with this one.
        oldest = f"{max(float(marks.get(ch, 0) or 0), cold):.6f}"
        recs = reader.fetch(ch, oldest=oldest)
        slack_source.write_ndjson(recs, raw_dir)
        got += len(recs)
        if recs:
            marks[ch] = max([float(r["ts_epoch"]) for r in recs] + [float(marks.get(ch, 0) or 0)])
    _save_watermarks(marks)

    con = store.connect()
    try:
        loadstage.load(str(raw_dir), run_id=run_id, con=con, skip_validate=True)
        for fn in (noise.run, extract.run, evidence.run, qualify.run, group.run,
                   register.run, classify.run, dedupe.run):
            fn(run_id, con=con)
        sink = InProcessTicketSink()
        r = emit.run(run_id, con=con, sink=sink)
        channels_out = rollup.channel_rollup(con, run_id)
    finally:
        con.close()

    from . import intake_api
    intake_api.store_channels(channels_out, by="intake-poller")
    acked = _acknowledge(run_id)
    return {"messages": got, "issues": r.get("issues", 0), "created": sink.created,
            "channels": len(channels_out), "acknowledged": acked}


def _acknowledge(run_id: str) -> int:
    """Tell raisers their ticket exists. Returns how many were sent.

    Deliberately reads the raiser's email from the intake SQLite rather than storing it on the
    durable ticket: the address is needed for one send and nowhere else, and keeping partner
    contact details out of the shared register is worth a lookup.
    """
    mode = os.environ.get(ENV_NOTIFY, "off").strip().lower()
    if mode not in ("email", "email-dry"):
        return 0

    from .intake import notify, store
    from . import intake_api

    base = os.environ.get(ENV_PUBLIC_URL, "").strip().rstrip("/")
    if not base:
        log.warning("acknowledgement skipped: $%s is unset, so the status link would be "
                    "relative and useless in an email", ENV_PUBLIC_URL)
        return 0

    pending = intake_api.unacknowledged("email")
    if not pending:
        return 0

    try:
        cap = int(os.environ.get(ENV_NOTIFY_MAX, "10"))
    except ValueError:
        cap = 10
    n = notify.EmailNotifier(
        dry_run=(mode == "email-dry"),
        redirect_to=os.environ.get(ENV_NOTIFY_TO, "").strip() or None,
        allow_domains=tuple(d.strip() for d in
                            os.environ.get(ENV_NOTIFY_ALLOW, "").split(",") if d.strip()))

    con = store.connect()
    try:
        sent = 0
        for t in pending:
            if sent >= cap:
                # A grouping bug that turns one issue into four hundred must cost ten emails,
                # not four hundred. The rest are picked up on the next poll.
                break
            if t.get("state") == "resolved":
                continue
            row = con.execute(
                "SELECT m.author_email FROM messages m WHERE m.channel_id||'/'||m.message_id=?",
                (t.get("source_id") or "",)).fetchone()
            email = (row["author_email"] if row else None) or ""
            ok, why = n._permitted(email)
            if not ok:
                intake_api.mark_acknowledged(t["idempotency_key"], channel="email",
                                             recipient=None, error=why)
                continue
            text, html = notify._body(t.get("title") or "your issue", t["ref"],
                                      f"{base}/t/{t['status_token']}",
                                      t.get("occurrence_count") or 1)
            try:
                to = n.send(to=email, subject=f"{t['ref']} — we have your issue",
                            text=text, html=html)
                if not n.dry_run:
                    intake_api.mark_acknowledged(t["idempotency_key"], channel="email",
                                                 recipient=to)
                sent += 1
            except Exception as e:                                        # noqa: BLE001
                # Recorded WITHOUT marking it acknowledged, so the next poll retries. Dropping
                # it silently would leave a partner believing nobody read their message.
                intake_api.mark_acknowledged(t["idempotency_key"], channel="email",
                                             recipient=None,
                                             error=f"{type(e).__name__}: {e}")
        return sent
    finally:
        con.close()


def _loop(channels: list[str], every: float) -> None:
    raw_dir = Path(os.environ.get("PSP_STATE_DIR", "/tmp")) / "intake-raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    run_id = "render"
    _state.update(channels=channels)
    log.info("intake poller started: %d channel(s) every %.0fs", len(channels), every)

    while True:
        try:
            r = _poll_once(channels, raw_dir, run_id)
            _state.update(polls=_state["polls"] + 1, last_error=None,
                          last_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          messages=r["messages"], issues=r["issues"],
                          tickets=_state["tickets"] + r["created"],
                          acknowledged=_state["acknowledged"] + r.get("acknowledged", 0),
                          notify=os.environ.get(ENV_NOTIFY, "off"))
            log.info("intake poll %d: %d messages, %d issues, %d new ticket(s), "
                     "%d acknowledged", _state["polls"], r["messages"], r["issues"],
                     r["created"], r.get("acknowledged", 0))
        except Exception as e:                                            # noqa: BLE001
            # A transient Slack or store failure must never end the loop — staying up is the
            # only thing it does. Record it so the page can show it rather than showing silence.
            _state.update(last_error=f"{type(e).__name__}: {e}",
                          last_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            log.warning("intake poll failed: %s: %s", type(e).__name__, e)
        time.sleep(every)


def start() -> str:
    """Start the poller if it is configured. Returns a one-line reason, for the boot log."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return "already running"
    if os.environ.get(ENV_ENABLED, "1").strip() in ("0", "false", "no"):
        return f"disabled by ${ENV_ENABLED}"
    if not os.environ.get(ENV_TOKEN, "").strip():
        return f"not started — ${ENV_TOKEN} is unset"
    channels = _channels()
    if not channels:
        return f"not started — ${ENV_CHANNELS} is unset (comma-separated channel ids)"
    try:
        every = float(os.environ.get(ENV_EVERY, "60"))
    except ValueError:
        every = 60.0
    # A poll faster than Slack's tier-3 limit gets us rate-limited, which looks like an outage.
    every = max(15.0, every)

    _thread = threading.Thread(target=_loop, args=(channels, every), daemon=True,
                              name="intake-poller")
    _thread.start()
    return f"started on {len(channels)} channel(s) every {every:.0f}s"
