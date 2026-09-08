"""Stage 8 — cross-channel duplicate detection. NO LLM CALLS, NO NETWORK.

── CROSS-CHANNEL, NOT PER-CHANNEL ────────────────────────────────────────────────────────────
Confirmed real: the MX1 captain-panel issue was posted by the same author in BOTH in-scope
channels 47 seconds apart, near-identical text, and the threads then forked — 4 replies on one
side, 2 on the other. Per-channel dedupe creates two tickets out of that on day one, and
closure detection that only looks at one channel leaves the other open forever.

── LINK, NEVER MERGE ─────────────────────────────────────────────────────────────────────────
Both issues stay in the register with `duplicate_of` pointing one at the other. That is how
stage 5's over-splitting bias and this stage coexist: nothing is destroyed, a human sees both
threads, and closure on either is visible from the other. Merging would discard one of the two
forked threads.

── THE KAPTURE SEAM ──────────────────────────────────────────────────────────────────────────
There is no Kapture API token, and this repo has no Kapture client at all — `tickets.db` is an
offline xlsx dump opened read-only. So the demo source scrapes ticket ids out of message text.
`KaptureDedupeSource` is the seam the real API drops into without stage 7 changing.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Protocol

from . import store

WINDOW_S = 3600.0          # cross-posts are minutes apart, not days
TEXT_SIMILARITY = 0.72


class KaptureDedupeSource(Protocol):
    """The seam. `known_ticket_ids(issue)` returns Kapture ids already covering this issue.

    The demo implementation reads ids out of the message text. The real one calls the Kapture
    API with a token — and nothing in stage 7 changes when it does.
    """

    def known_ticket_ids(self, issue: dict) -> set[str]: ...


class TextScrapedKaptureSource:
    """Demo source: the ticket ids people typed into the message themselves.

    Around half of Slack posts already carry a ticket number, which is why this is useful at
    all rather than a placeholder that returns nothing.
    """

    name = "text_scraped"

    def known_ticket_ids(self, issue: dict) -> set[str]:
        return set(json.loads(issue.get("kapture_ticket_ids_json") or "[]"))


_WS = re.compile(r"\s+")


def _norm(t: str) -> str:
    return _WS.sub(" ", re.sub(r"<[^>]+>", " ", (t or "").lower())).strip()


def run(run_id: str, *, con: sqlite3.Connection | None = None,
        source: KaptureDedupeSource | None = None) -> dict:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    own = con is None
    con = con or store.connect()
    try:
        src = source or TextScrapedKaptureSource()
        store.reset_stage(con, "dedupe", run_id)

        issues = [dict(r) for r in con.execute(
            "SELECT i.*, m.ts_epoch, m.text FROM issues i "
            "JOIN messages m ON m.channel_id = i.anchor_channel_id "
            "                AND m.message_id = i.anchor_message_id "
            "WHERE i.run_id = ? ORDER BY m.ts_epoch", (run_id,))]

        pairs = []
        for i, a in enumerate(issues):
            for b in issues[i + 1:]:
                # 1. a shared Kapture ticket id — the strongest signal, and cross-channel
                shared = src.known_ticket_ids(a) & src.known_ticket_ids(b)
                if shared:
                    pairs.append((b["issue_id"], a["issue_id"], "kapture_id", 0.95,
                                  f"shares Kapture ticket {sorted(shared)[0]}"))
                    continue

                # 2. the heuristic: same author, same DC, near-identical text, inside a window,
                #    ACROSS all in-scope channels. Same-channel pairs are left to stage 5.
                if a["anchor_channel_id"] == b["anchor_channel_id"]:
                    continue
                if a["raiser_id"] != b["raiser_id"]:
                    continue
                dt = abs(b["ts_epoch"] - a["ts_epoch"])
                if dt > WINDOW_S:
                    continue
                if a["dc_code"] and b["dc_code"] and a["dc_code"] != b["dc_code"]:
                    continue
                sim = SequenceMatcher(None, _norm(a["text"]), _norm(b["text"])).ratio()
                if sim >= TEXT_SIMILARITY:
                    pairs.append((
                        b["issue_id"], a["issue_id"], "dc_intent_author_window",
                        round(min(0.9, sim), 3),
                        f"same author, dc={a['dc_code']}, {dt:.0f}s apart, "
                        f"text similarity {sim:.2f}, across "
                        f"{a['anchor_channel_id']}/{b['anchor_channel_id']}"))

        con.executemany(
            "INSERT OR REPLACE INTO duplicates (run_id, issue_id, duplicate_of, method, "
            "confidence, evidence) VALUES (?,?,?,?,?,?)",
            [(run_id, p[0], p[1], p[2], p[3], p[4]) for p in pairs])
        for p in pairs:
            con.execute("UPDATE issues SET duplicate_of = ? WHERE run_id = ? AND issue_id = ?",
                        (p[1], run_id, p[0]))
        con.commit()

        cross = sum(1 for p in pairs if p[2] == "dc_intent_author_window")
        stats = {
            "duplicate_pairs": len(pairs),
            "by_method": {m: sum(1 for p in pairs if p[2] == m)
                          for m in {p[2] for p in pairs}},
            "cross_channel_pairs": cross,
            "source": getattr(src, "name", type(src).__name__),
            "issues_marked_duplicate": len({p[0] for p in pairs}),
            "_note": "Linked, never merged — both issues stay in the register with their own "
                     "threads and their own closure state.",
        }
        con.execute(
            "INSERT OR REPLACE INTO stage_runs (run_id, stage, started_at, finished_at, "
            "rows_in, rows_out, llm_calls, cost_usd, config_hash, status) "
            "VALUES (?,?,?,?,?,?,0,0.0,?,'ok')",
            (run_id, "dedupe", started,
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             len(issues), len(pairs), stats["source"]))
        con.commit()
        return stats
    finally:
        if own:
            con.close()
