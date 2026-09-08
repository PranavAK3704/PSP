"""Stage 7 — one row per issue. NO LLM CALLS, NO NETWORK.

The register is the artefact that does not exist today: who raised what, which DC, when it was
picked up, and whether it is still open.

── FIRST-RESPONSE LATENCY HAS AN EXCLUSION, AND IT MATTERS MORE THAN THE MEDIAN ──────────────
Real first-response times in these channels run from 69 seconds to 6 days, so the distribution
is the whole story and one poisoned value moves it. A reply that changes no state is not a
response: one real record reads "Just a follow up of previous messages", threaded onto a
broadcast six days later. Counted naively that is a 6-day first response, a number that
measures nothing and drags the p90 with it.

So latency is measured to the first reply BY SOMEONE OTHER THAN THE RAISER that is not itself
a gated ack, and when no such reply exists the field is NULL with
`latency_excluded_reason` saying which of those two rules bit. A NULL that explains itself is
worth more than a number that cannot be defended.

── THE STATE MACHINE ─────────────────────────────────────────────────────────────────────────
NEW -> IDENTIFIED -> OPEN -> RESOLVED -> CLOSED, plus FLAGGED. Without stage 6 nothing reaches
RESOLVED or CLOSED, because closure is an adjudicated judgement and the brief is explicit that
closure is the biggest gap in the data. An issue sits at IDENTIFIED (has an identifier) or
OPEN (has a reply) and says so, rather than being guessed into RESOLVED.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from . import store

#: Tokens that identify a specific thing. An issue carrying one is IDENTIFIED — a human can
#: act on it without asking the raiser who they are.
IDENTIFYING = ("kapture_id", "waybill", "mobile", "pilot_id", "email")


def run(run_id: str, *, con: sqlite3.Connection | None = None) -> dict:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    own = con is None
    con = con or store.connect()
    try:
        store.reset_stage(con, "register", run_id)

        anchors = con.execute(
            "SELECT a.issue_id, a.channel_id, a.message_id, m.ts_epoch, m.ts_iso, m.permalink, "
            "       m.author_id, m.author_name, m.reply_count, f.informational "
            "FROM assignments a "
            "JOIN messages m USING(channel_id, message_id) "
            "LEFT JOIN message_flags f ON f.run_id = a.run_id "
            "     AND f.channel_id = a.channel_id AND f.message_id = a.message_id "
            "WHERE a.run_id = ? AND a.rule = 'new_issue' ORDER BY m.ts_epoch", (run_id,)
        ).fetchall()

        rows, states = [], {}
        for a in anchors:
            members = con.execute(
                "SELECT m.channel_id, m.message_id, m.ts_epoch, m.author_id, m.text, "
                "       COALESCE(f.gated, 0) AS gated "
                "FROM assignments s JOIN messages m USING(channel_id, message_id) "
                "LEFT JOIN message_flags f ON f.run_id = s.run_id "
                "     AND f.channel_id = s.channel_id AND f.message_id = s.message_id "
                "WHERE s.run_id = ? AND s.issue_id = ? ORDER BY m.ts_epoch",
                (run_id, a["issue_id"])).fetchall()

            toks = {}
            for m in members:
                for k, v in con.execute(
                        "SELECT kind, value FROM entities WHERE run_id=? AND channel_id=? "
                        "AND message_id=?", (run_id, m["channel_id"], m["message_id"])):
                    toks.setdefault(k, set()).add(v)

            # first response: not the raiser, not a gated ack
            latency = None
            excluded = None
            replies = [m for m in members if m["message_id"] != a["message_id"]]
            genuine = [m for m in replies
                       if m["author_id"] != a["author_id"] and not m["gated"]]
            if genuine:
                latency = round(genuine[0]["ts_epoch"] - a["ts_epoch"], 3)
            elif replies:
                excluded = ("replies exist but none qualify: "
                            + ("all from the raiser" if all(
                                m["author_id"] == a["author_id"] for m in replies)
                               else "all gated acks"))
            else:
                excluded = "no replies"

            dc = sorted(toks.get("dc_code", []))
            has_id = any(toks.get(k) for k in IDENTIFYING)
            state = "OPEN" if len(replies) else ("IDENTIFIED" if has_id or dc else "NEW")

            rows.append((
                run_id, a["issue_id"], a["channel_id"], a["message_id"], a["permalink"],
                a["author_id"], a["author_name"], dc[0] if dc else None,
                json.dumps({k: sorted(v) for k, v in sorted(toks.items())}, sort_keys=True),
                None, "unadjudicated", 1 if a["informational"] else 0,
                json.dumps(sorted(toks.get("kapture_id", []))),
                latency, excluded, len(replies),
                None, None, None, None, state, None, json.dumps([]), json.dumps([]),
            ))
            states[state] = states.get(state, 0) + 1

        con.executemany(
            "INSERT OR REPLACE INTO issues (run_id, issue_id, anchor_channel_id, "
            "anchor_message_id, permalink, raiser_id, raiser_name, dc_code, "
            "entity_tokens_json, intent, intent_source, informational, "
            "kapture_ticket_ids_json, first_response_latency_s, latency_excluded_reason, "
            "reply_count, closure_signal, closure_detected_by, closure_ts, days_open, state, "
            "duplicate_of, related_to_json, flags_json) "
            "VALUES (" + ",".join("?" * 24) + ")", rows)
        con.commit()

        lat = [r[13] for r in rows if r[13] is not None]
        lat.sort()

        def pct(p):
            return round(lat[min(int(len(lat) * p), len(lat) - 1)], 1) if lat else None

        stats = {
            "issues": len(rows),
            "by_state": states,
            "with_latency": len(lat),
            "latency_excluded": len(rows) - len(lat),
            "latency_median_s": pct(0.5),
            "latency_p90_s": pct(0.9),
            "informational_issues": sum(1 for r in rows if r[11]),
            "no_closure_signal_pct": 1.0,
        }
        con.execute(
            "INSERT OR REPLACE INTO stage_runs (run_id, stage, started_at, finished_at, "
            "rows_in, rows_out, llm_calls, cost_usd, config_hash, status) "
            "VALUES (?,?,?,?,?,?,0,0.0,'','ok')",
            (run_id, "register", started,
             datetime.now(timezone.utc).isoformat(timespec="seconds"), len(anchors), len(rows)))
        con.commit()
        return stats
    finally:
        if own:
            con.close()
