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

_thread: threading.Thread | None = None
_state: dict = {"polls": 0, "last_at": None, "last_error": None, "messages": 0, "issues": 0,
                "tickets": 0, "running": False, "channels": []}


def status() -> dict:
    """What the poller has been doing — surfaced on the Intake page so 'no new tickets' can be
    told apart from 'nothing is listening', which look identical otherwise."""
    return dict(_state)


def _channels() -> list[str]:
    raw = os.environ.get(ENV_CHANNELS, "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def _poll_once(channels: list[str], raw_dir: Path, run_id: str) -> dict:
    """One pull-and-process cycle. Imported lazily so the intake package is not a hard
    dependency of booting PSP — a broken intake import must not take the whole app down."""
    from .intake import (classify, dedupe, emit, evidence, extract, group, loadstage, noise,
                         qualify, register, rollup, slack_source, store)
    from .intake_sink import InProcessTicketSink

    reader = slack_source.SlackReader(pause=0.2)
    got = 0
    for ch in channels:
        recs = reader.fetch(ch)
        slack_source.write_ndjson(recs, raw_dir)
        got += len(recs)

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
    return {"messages": got, "issues": r.get("issues", 0), "created": sink.created,
            "channels": len(channels_out)}


def _loop(channels: list[str], every: float) -> None:
    raw_dir = Path(os.environ.get("PSP_STATE_DIR", "/tmp")) / "intake-raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    run_id = "render"
    _state.update(running=True, channels=channels)
    log.info("intake poller started: %d channel(s) every %.0fs", len(channels), every)

    while True:
        try:
            r = _poll_once(channels, raw_dir, run_id)
            _state.update(polls=_state["polls"] + 1, last_error=None,
                          last_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          messages=r["messages"], issues=r["issues"],
                          tickets=_state["tickets"] + r["created"])
            log.info("intake poll %d: %d messages, %d issues, %d new ticket(s)",
                     _state["polls"], r["messages"], r["issues"], r["created"])
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
