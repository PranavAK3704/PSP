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
    # What the PROCESS actually sees. Without this, "I set the variables" and "the poller did
    # not start" are both true-sounding and there is no way to tell which is wrong — the value
    # is never shown, only whether it arrived.
    env = {
        "SLACK_BOT_TOKEN": "set" if os.environ.get(ENV_TOKEN, "").strip() else "MISSING",
        "INTAKE_CHANNELS": os.environ.get(ENV_CHANNELS, "") or "MISSING",
        "INTAKE_POLL_SECONDS": os.environ.get(ENV_EVERY, "(default 60)"),
        "INTAKE_POLL_ENABLED": os.environ.get(ENV_ENABLED, "(default 1)"),
        "INTAKE_NOTIFY": os.environ.get(ENV_NOTIFY, "(default off)"),
        "INTAKE_NOTIFY_TO": os.environ.get(ENV_NOTIFY_TO, "") or "(none — sends to raisers)",
        "PSP_PUBLIC_URL": os.environ.get(ENV_PUBLIC_URL, "") or "MISSING",
        "SMTP_USERNAME": os.environ.get("SMTP_USERNAME", "") or "MISSING",
        "SMTP_PASSWORD": "set" if os.environ.get("SMTP_PASSWORD", "").strip() else "MISSING",
    }
    # Whether the classifier can run at all. Without this the page cannot tell "the matcher
    # refused" from "there is no matcher", and those look identical on a ticket.
    try:
        from . import intake_api
        n = len(intake_api.exemplar_index().get("exemplars") or [])
    except Exception:                                                     # noqa: BLE001
        n = 0
    env["EXEMPLAR_INDEX"] = f"{n} exemplars" if n else "MISSING — classification is OFF"

    # Whether the durable mirror can be READ. Its failure mode is asymmetric and vicious: reads
    # return blocked while writes still succeed, so a store saves happily, reports success, and
    # comes back empty on the next container. That is not a theory here — the exemplar index was
    # uploaded three times, verified each time, and was absent after every redeploy.
    try:
        from .durable_state import _init as _d_init, durable_path as _dp
        if not _d_init():
            env["DURABLE_MIRROR"] = "not configured — local file only, WIPED on every deploy"
        else:
            if not _dp("intake_tickets.json").read_failed():
                env["DURABLE_MIRROR"] = "readable"
            else:
                # The reason, not just the fact. An exhausted quota, a revoked token and a bad
                # URL all read as "unreadable" and have completely different fixes.
                from . import durable_state as _ds
                from .substrate import turso_http as _th
                try:
                    _th.execute(_ds._url, _ds._tok, "SELECT 1")
                    why = "a probe query succeeded — the failure is key-specific"
                except Exception as _e:                                   # noqa: BLE001
                    why = f"{type(_e).__name__}: {str(_e)[:160]}"
                env["DURABLE_MIRROR"] = f"UNREADABLE — writes vanish. {why}"
    except Exception as e:                                                # noqa: BLE001
        env["DURABLE_MIRROR"] = f"check failed: {type(e).__name__}"
    return {**_state, "running": bool(_thread is not None and _thread.is_alive()),
            "env": env, "start_reason": _state.get("start_reason")}


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

    # The exemplar index is partner text, so it is neither in git nor in the image — it is
    # uploaded once and lives in the durable store. Materialised to a file here because
    # classify.run already takes a path, which keeps app/intake decoupled from PSP: the caller
    # supplies the index, the package never reaches into PSP's storage to find one.
    ex_path = _materialise_exemplars(raw_dir.parent)

    con = store.connect()
    try:
        loadstage.load(str(raw_dir), run_id=run_id, con=con, skip_validate=True)
        for fn in (noise.run, extract.run, evidence.run, qualify.run, group.run,
                   register.run):
            fn(run_id, con=con)
        cls = (classify.run(run_id, con=con, exemplars=ex_path) if ex_path
               else {"skipped": True, "_warning": "no exemplar index materialised"})
        dedupe.run(run_id, con=con)
        sink = InProcessTicketSink()
        r = emit.run(run_id, con=con, sink=sink)
        channels_out = rollup.channel_rollup(con, run_id)
    finally:
        con.close()

    from . import intake_api
    intake_api.store_channels(channels_out, by="intake-poller")
    acked = _acknowledge(run_id)
    return {"messages": got, "issues": r.get("issues", 0), "created": sink.created,
            "channels": len(channels_out), "acknowledged": acked,
            "classified": not cls.get("skipped"),
            # The REASON, not just the fact. "classification is off" has several causes — no
            # index uploaded, an index that failed to materialise, a path classify could not
            # read — and they are indistinguishable from a boolean.
            "classify_note": cls.get("_warning") or
                             f"{cls.get('exemplars', 0)} exemplars, "
                             f"{cls.get('novel', 0)} novel of {cls.get('issues', 0)}",
            "exemplar_path": str(ex_path) if ex_path else None,
            "novel": cls.get("novel", 0)}


def _materialise_exemplars(base: Path) -> Path | None:
    """Write the uploaded index to a file classify.run can read, or None if none is stored.

    Rewritten only when the stored copy is newer, so the common case is a cheap timestamp
    comparison rather than a 366 KB write every minute.
    """
    from . import intake_api
    d = intake_api.exemplar_index()
    rows = d.get("exemplars") or []
    if not rows:
        return None
    path = base / "exemplars.json"
    stamp = d.get("uploaded_at") or ""
    marker = base / ".exemplars-stamp"
    if path.exists() and marker.exists() and marker.read_text() == stamp:
        return path
    import json as _json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps({"exemplars": rows}, ensure_ascii=False))
    marker.write_text(stamp)
    log.info("exemplar index materialised: %d rows uploaded %s", len(rows), stamp)
    return path


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
                          classified=r.get("classified", False),
                          classify_note=r.get("classify_note"),
                          exemplar_path=r.get("exemplar_path"),
                          notify=os.environ.get(ENV_NOTIFY, "off"))
            log.info("intake poll %d: %d messages, %d issues, %d new ticket(s), "
                     "%d acknowledged, classifier=%s", _state["polls"], r["messages"],
                     r["issues"], r["created"], r.get("acknowledged", 0),
                     "on" if r.get("classified") else "OFF (no exemplar index)")
        except Exception as e:                                            # noqa: BLE001
            # A transient Slack or store failure must never end the loop — staying up is the
            # only thing it does. Record it so the page can show it rather than showing silence.
            _state.update(last_error=f"{type(e).__name__}: {e}",
                          last_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            log.warning("intake poll failed: %s: %s", type(e).__name__, e)
        time.sleep(every)


def _remember(msg: str) -> str:
    _state["start_reason"] = msg
    return msg


def start() -> str:
    """Start the poller if it is configured. Returns a one-line reason, for the boot log."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return _remember("already running")
    if os.environ.get(ENV_ENABLED, "1").strip() in ("0", "false", "no"):
        return _remember(f"disabled by ${ENV_ENABLED}")
    if not os.environ.get(ENV_TOKEN, "").strip():
        return _remember(f"not started — ${ENV_TOKEN} is unset")
    channels = _channels()
    if not channels:
        return _remember(
            f"not started — ${ENV_CHANNELS} is unset (comma-separated channel ids)")
    try:
        every = float(os.environ.get(ENV_EVERY, "60"))
    except ValueError:
        every = 60.0
    # A poll faster than Slack's tier-3 limit gets us rate-limited, which looks like an outage.
    every = max(15.0, every)

    _thread = threading.Thread(target=_loop, args=(channels, every), daemon=True,
                              name="intake-poller")
    _thread.start()
    msg = f"started on {len(channels)} channel(s) every {every:.0f}s"
    _state["start_reason"] = msg
    return msg
