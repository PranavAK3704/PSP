"""Per-channel rollup — what each listening channel produced.

One function, used by both the local dashboard and the PSP register, so the two cannot drift
into disagreeing about the same numbers.

── WHY THIS PANEL EXISTS AT ALL ──────────────────────────────────────────────────────────────
A channel that is connected but silent looks exactly like one that is broken. Without counts on
screen there is no way to tell "nobody wrote anything today" from "the bot was never invited"
or "the qualification gate excluded it" — and all three are indistinguishable from the ticket
list alone, because all three produce no tickets.

So every row carries what arrived, what came of it, and — when a channel is excluded — the
reason in the gate's own words rather than a silent zero.
"""
from __future__ import annotations

import sqlite3


def channel_rollup(con: sqlite3.Connection, run_id: str) -> list[dict]:
    """One row per channel seen in the store, newest-first by volume."""
    rows: dict[str, dict] = {}
    for r in con.execute(
            "SELECT channel_id, channel_name, COUNT(*) n, MAX(ts_iso) last_at "
            "FROM messages GROUP BY channel_id, channel_name"):
        rows[r["channel_id"]] = {
            "channel_id": r["channel_id"],
            "name": r["channel_name"] or r["channel_id"],
            "messages": r["n"],
            "last_at": r["last_at"] or "",
            "issues": 0,
            "tickets": 0,
            "gated": 0,
            "not_an_issue": 0,
            "qualified": None,
            "reason": None,
        }

    for r in con.execute(
            "SELECT anchor_channel_id c, COUNT(*) n FROM issues WHERE run_id=? "
            "GROUP BY anchor_channel_id", (run_id,)):
        if r["c"] in rows:
            rows[r["c"]]["issues"] = r["n"]

    # Tickets that would actually be raised — suppressed drafts are not tickets.
    for r in con.execute(
            "SELECT i.anchor_channel_id c, COUNT(*) n FROM ticket_drafts d "
            "JOIN issues i ON i.run_id=d.run_id AND i.issue_id=d.issue_id "
            "WHERE d.run_id=? AND d.suppressed=0 GROUP BY i.anchor_channel_id", (run_id,)):
        if r["c"] in rows:
            rows[r["c"]]["tickets"] = r["n"]

    for r in con.execute(
            "SELECT channel_id c, COUNT(*) n FROM message_flags WHERE run_id=? AND gated=1 "
            "GROUP BY channel_id", (run_id,)):
        if r["c"] in rows:
            rows[r["c"]]["gated"] = r["n"]

    for r in con.execute(
            "SELECT channel_id c, COUNT(*) n FROM evidence WHERE run_id=? "
            "AND decision='not_an_issue' GROUP BY channel_id", (run_id,)):
        if r["c"] in rows:
            rows[r["c"]]["not_an_issue"] = r["n"]

    # The gate's verdict, so an excluded channel says why instead of showing a silent zero.
    try:
        for r in con.execute(
                "SELECT channel_id, in_scope, reason FROM channel_qualification WHERE run_id=?",
                (run_id,)):
            if r["channel_id"] in rows:
                rows[r["channel_id"]]["qualified"] = bool(r["in_scope"])
                rows[r["channel_id"]]["reason"] = r["reason"]
    except sqlite3.Error:
        pass                      # qualification has not run yet on a cold start

    return sorted(rows.values(), key=lambda c: -c["messages"])
