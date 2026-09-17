"""A local, read-only live view of the intake pipeline. For demos.

    python -m app.intake.live_demo --channel C0C0KPAFVT4

Opens http://127.0.0.1:8099 . A background thread polls the channel, runs the whole
deterministic pipeline over whatever it finds, and the page shows what the pipeline decided
about every message — updating within a few seconds of you posting in Slack.

── WHY A LOCAL SERVER AND NOT THE PSP FRONTEND ───────────────────────────────────────────────
The intake module is deliberately decoupled from the FastAPI app: it has no network calls at
runtime, no PSP database reads, and nothing in main.py imports it. Wiring a demo into the
deployed app would undo that for a view nobody needs in production. stdlib http.server, no new
dependency, binds to LOOPBACK ONLY.

── IT STILL CANNOT WRITE TO SLACK ────────────────────────────────────────────────────────────
The poller uses the same read-only bot token. There is no send path anywhere in this module or
in slack_source.py, and the granted scopes contain no `chat:write`.

── THE POLL IS THE SAME CALL AS THE BACKFILL ─────────────────────────────────────────────────
`conversations.history` with an `oldest` cursor. At 90 days it is a backfill; at 60 seconds it
is real time. That is why there is no separate streaming implementation to maintain — and why
this demo exercises the production path rather than a mock of it.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.intake import (  # noqa: E402
    classify, dedupe, emit, evidence, extract, group, labels, loadstage, noise, qualify,
    register, rollup, slack_source, store,
)

STATIC = Path(__file__).resolve().parent / "static"
RUN_ID = "live"

_state: dict = {"poll": {"status": "starting", "count": 0, "last_at": None, "error": None},
                "stats": {}, "feed": [], "tickets": []}
_lock = threading.Lock()


def _snapshot(con) -> dict:
    """Everything the page needs, in one read. Ordered newest-first — a demo watches the top."""
    rows = con.execute(
        "SELECT m.channel_id, m.message_id, m.ts_iso, m.ts_epoch, m.author_name, m.author_id, "
        "       m.text, m.subtype, m.has_media, m.attachments_json, m.reactions_json, "
        "       m.thread_ref, m.permalink, "
        "       f.gated, f.gate_rule, f.informational, f.informational_rule, "
        "       f.informational_borderline, a.issue_id, a.rule AS grp_rule, a.confidence "
        "FROM messages m "
        "LEFT JOIN message_flags f ON f.run_id=? AND f.channel_id=m.channel_id "
        "     AND f.message_id=m.message_id "
        "LEFT JOIN assignments a ON a.run_id=? AND a.channel_id=m.channel_id "
        "     AND a.message_id=m.message_id "
        "ORDER BY m.ts_epoch DESC", (RUN_ID, RUN_ID)).fetchall()

    ev = {r["message_id"]: dict(r) for r in con.execute(
        "SELECT message_id, decision, score, reasons FROM evidence WHERE run_id=?", (RUN_ID,))}

    ents: dict[str, list] = {}
    for e in con.execute("SELECT channel_id, message_id, kind, value, tier FROM entities "
                         "WHERE run_id=? ORDER BY kind, value", (RUN_ID,)):
        ents.setdefault(e["message_id"], []).append(
            {"kind": e["kind"], "value": e["value"], "tier": e["tier"]})

    drafts = {d["issue_id"]: dict(d) for d in con.execute(
        "SELECT * FROM ticket_drafts WHERE run_id=?", (RUN_ID,))}

    # The NOVEL queue: issues the matcher refused to classify. This is the half of the loop that
    # makes the taxonomy grow — a refusal is a question addressed to a human, and answering it
    # turns the message into an exemplar so the next one like it is classified for free.
    confirmed = labels.load()
    novel_q = []
    for iid, d in drafts.items():
        if d["intent"] != "NOVEL":
            continue
        row = con.execute(
            "SELECT m.text, m.permalink, m.author_name, m.author_id, m.ts_iso FROM issues i "
            "JOIN messages m ON m.channel_id=i.anchor_channel_id "
            "               AND m.message_id=i.anchor_message_id "
            "WHERE i.run_id=? AND i.issue_id=?", (RUN_ID, iid)).fetchone()
        if not row:
            continue
        prev = confirmed.get(labels.text_id(row["text"] or ""))
        novel_q.append({
            "issue_id": iid, "text": row["text"] or "", "permalink": row["permalink"],
            "author": row["author_name"] or row["author_id"], "time": (row["ts_iso"] or "")[11:16],
            "title": d["title"], "dc": d["dc_code"],
            "occurrences": d["occurrence_count"] or 1,
            # Already answered? Shown so a re-run does not ask the same question twice — the
            # label is keyed on the TEXT, so it survives a new issue_id for the same wording.
            "confirmed": None if not prev else {
                "decision": prev["decision"], "disposition": prev["disposition"],
                "by": prev["confirmed_by"], "at": prev["confirmed_at"]},
        })
    novel_q.sort(key=lambda x: (x["confirmed"] is not None, -(x["occurrences"] or 1)))

    feed = []
    for r in rows:
        d = drafts.get(r["issue_id"]) if r["grp_rule"] == "new_issue" else None
        feed.append({
            "message_id": r["message_id"],
            "ts_iso": r["ts_iso"],
            "time": (r["ts_iso"] or "")[11:16],
            "author": r["author_name"] or r["author_id"],
            "text": r["text"] or "",
            "is_reply": bool(r["thread_ref"]),
            "permalink": r["permalink"],
            "has_media": bool(r["has_media"]),
            "files": [f.get("name") for f in json.loads(r["attachments_json"] or "[]")],
            "reactions": [f"{x.get('name')}×{x.get('count')}"
                          for x in json.loads(r["reactions_json"] or "[]")],
            "subtype": r["subtype"],
            "gated": bool(r["gated"]),
            "gate_rule": r["gate_rule"],
            "evidence": ev.get(r["message_id"], {}).get("decision"),
            "evidence_score": ev.get(r["message_id"], {}).get("score"),
            "evidence_why": ev.get(r["message_id"], {}).get("reasons"),
            "informational": bool(r["informational"]),
            "informational_rule": r["informational_rule"],
            "borderline": bool(r["informational_borderline"]),
            "entities": ents.get(r["message_id"], []),
            "issue_id": r["issue_id"],
            "issue_short": (r["issue_id"] or "").rsplit("-", 1)[-1][:14],
            "grp_rule": r["grp_rule"],
            "confidence": r["confidence"],
            "ticket": None if not d else {
                "raise": not d["suppressed"], "title": d["title"],
                "reason": d["suppressed_reason"], "ref": d["external_ref"],
                "key": (d["idempotency_key"] or "")[:12]},
        })

    tickets = [{"title": d["title"], "dc": d["dc_code"], "raise": not d["suppressed"],
                "reason": d["suppressed_reason"], "ref": d["external_ref"],
                "replies": d["reply_count"], "latency": d["first_response_latency_s"],
                "disposition": d["intent"], "occurrences": d["occurrence_count"] or 1,
                "first_raised": d["first_raised_at"], "last_raised": d["last_raised_at"]}
               for d in sorted(drafts.values(), key=lambda x: (x["suppressed"], x["title"] or ""))]

    # ── LISTENING CHANNELS ──────────────────────────────────────────────────────────────
    # Same function the PSP register uses, so the two views cannot drift into disagreeing
    # about the same counts.
    channels = rollup.channel_rollup(con, RUN_ID)

    n = len(feed)
    return {
        "channels": channels,
        "stats": {
            "messages": n,
            "gated": sum(1 for f in feed if f["gated"]),
            "not_an_issue": sum(1 for f in feed if f["evidence"] == "not_an_issue"),
            "orphans": sum(1 for f in feed if f["evidence"] == "orphan"),
            "informational": sum(1 for f in feed if f["informational"]),
            "issues": len(drafts),
            "tickets": sum(1 for t in tickets if t["raise"]),
            "held_back": sum(1 for t in tickets if not t["raise"]),
            "entities": sum(len(f["entities"]) for f in feed),
            "repeat_issues": sum(1 for t in tickets if (t["occurrences"] or 1) > 1),
            "novel": sum(1 for t in tickets if t["disposition"] == "NOVEL"),
            "novel_pending": sum(1 for q in novel_q if not q["confirmed"]),
            "confirmations": len(confirmed),
        },
        "feed": feed,
        "tickets": tickets,
        "novel_queue": novel_q,
        # The classes a human can pick from. Read off the live index rather than a hardcoded
        # list, so a disposition confirmed into existence yesterday is offered today.
        "dispositions": (lambda m: m.dispositions if m else [])(classify.load_matcher()),
    }


def poll_once(channels: list[str], raw_dir: Path, reader, oldest: str | None = None) -> dict:
    """Pull EVERY listening channel, then run the pipeline once over all of them together.

    One pipeline pass over the union, not one pass per channel — because cross-channel dedupe
    only works if both copies are in the same run. The MX1 pair that proved this was posted to
    two channels 47 seconds apart.
    """
    for ch in channels:
        recs = reader.fetch(ch, oldest=oldest)
        slack_source.write_ndjson(recs, raw_dir)
    con = store.connect()
    try:
        loadstage.load(raw_dir, run_id=RUN_ID, con=con, skip_validate=True)
        noise.run(RUN_ID, con=con)
        extract.run(RUN_ID, con=con)
        # AFTER extract (identifiers are its strongest signal) and BEFORE group (it decides
        # what may anchor an issue). Without this the demo shows the pre-fix behaviour.
        evidence.run(RUN_ID, con=con)
        qualify.run(RUN_ID, con=con)
        group.run(RUN_ID, con=con)
        register.run(RUN_ID, con=con)
        classify.run(RUN_ID, con=con)
        dedupe.run(RUN_ID, con=con)
        emit.run(RUN_ID, con=con)
        return _snapshot(con)
    finally:
        con.close()


def poller(channels: list[str], raw_dir: Path, every: float,
           oldest: str | None = None) -> None:
    reader = slack_source.SlackReader(pause=0.15)
    while True:
        try:
            snap = poll_once(channels, raw_dir, reader, oldest)
            with _lock:
                _state.update(snap)
                _state["poll"] = {
                    "status": "live", "count": _state["poll"]["count"] + 1,
                    "last_at": datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S"),
                    "error": None, "channels": channels}
        except Exception as e:                                          # noqa: BLE001
            with _lock:
                _state["poll"] = {**_state.get("poll", {}), "status": "error",
                                  "error": f"{type(e).__name__}: {e}"}
            traceback.print_exc(limit=1)
        time.sleep(every)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):                                          # quiet
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):                                                  # noqa: N802
        """Record a human decision about a NOVEL item.

        Binding to loopback keeps other machines out, but it does NOT keep other WEB PAGES out:
        anything the browser is showing can POST to 127.0.0.1. Two cheap guards, both of which a
        cross-origin page fails:

        - a custom header, which makes the request non-simple so the browser must preflight it
        - an Origin check, since our own page sends same-origin or none

        Nothing here can reach Slack — the only effect is a line appended to a local file.
        """
        if not self.path.startswith("/api/confirm"):
            return self._json({"ok": False, "error": "not_found"}, 404)
        if self.headers.get("X-Intake-Confirm") != "1":
            return self._json({"ok": False, "error": "missing_confirm_header"}, 403)
        origin = self.headers.get("Origin")
        if origin and not origin.startswith(("http://127.0.0.1", "http://localhost")):
            return self._json({"ok": False, "error": "cross_origin_refused"}, 403)

        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._json({"ok": False, "error": "bad_json"}, 400)

        try:
            row = labels.confirm(
                body.get("text") or "",
                body.get("decision") or "label",
                disposition=(body.get("disposition") or None),
                confirmed_by=body.get("confirmed_by") or "dashboard",
                source_id=body.get("issue_id"),
                permalink=body.get("permalink"),
                note=body.get("note"),
            )
        except labels.LabelError as e:
            # The message is written for a person to read and act on, so pass it through.
            return self._json({"ok": False, "error": str(e)}, 400)

        return self._json({"ok": True, "confirmation": row, "stats": labels.stats(),
                           "_next": "scripts/build_exemplars.py folds this into the index"})

    def do_GET(self):                                                   # noqa: N802
        if self.path.startswith("/api/state"):
            with _lock:
                body = json.dumps(_state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        page = (STATIC / "demo.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--channel", required=True, action="append",
                    help="channel id. Repeat the flag, or comma-separate, to listen on several "
                         "— they are pulled together and run through ONE pipeline pass so "
                         "cross-channel dedupe can see both copies.")
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--every", type=float, default=5.0, help="seconds between Slack polls")
    ap.add_argument("--raw", default=str(_BACKEND / "data" / "intake" / "live"))
    ap.add_argument("--since-now", action="store_true",
                    help="only read messages posted from launch onward. The app has no write "
                         "scope so it cannot delete channel history — this is how a demo gets "
                         "a genuinely empty feed without touching Slack.")
    ap.add_argument("--oldest", default=None, help="unix ts to read from")
    a = ap.parse_args()

    channels = [c.strip() for spec in a.channel for c in spec.split(",") if c.strip()]
    if not channels:
        raise SystemExit("--channel needs at least one channel id")
    oldest = a.oldest or (f"{time.time():.6f}" if a.since_now else None)
    raw = Path(a.raw)
    if a.since_now:
        # Stale NDJSON from an earlier run would be re-loaded on the first poll and the feed
        # would not be empty after all.
        for f in raw.glob("*.ndjson"):
            f.unlink()
    threading.Thread(target=poller, args=(channels, raw, a.every, oldest),
                     daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)   # LOOPBACK ONLY
    print(f"\n  intake live view   http://127.0.0.1:{a.port}")
    print(f"  listening on       {len(channels)} channel(s): {', '.join(channels)}")
    print(f"  polling every      {a.every}s   (read-only; this app has no write scope)")
    print(f"  reading from       {'launch time — feed starts empty' if oldest else 'all history'}\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
