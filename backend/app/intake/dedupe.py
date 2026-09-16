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

        # ── BUCKETING, because the pairwise scan is O(n^2) ──────────────────────────────────
        # Measured: 5.21s at 3,500 issues, and it grows with the square — a 90-day backfill
        # would make it the slowest thing in the pipeline by an order of magnitude.
        #
        # Every heuristic pair must share an author AND fall inside WINDOW_S, so bucketing by
        # (author, time-bucket) before comparing skips the overwhelming majority of pairs
        # without changing a single result. Two adjacent buckets are checked so a pair
        # straddling a boundary is not missed.
        #
        # The kapture_id path is exempt: a shared ticket id links issues at ANY distance, so it
        # runs over a separate index keyed on the id itself rather than on time.
        bucket_s = max(WINDOW_S, 1.0)
        by_author: dict[tuple, list[dict]] = {}
        by_ticket: dict[str, list[dict]] = {}
        for it in issues:
            b = int((it["ts_epoch"] or 0) // bucket_s)
            by_author.setdefault((it["raiser_id"], b), []).append(it)
            for tid in src.known_ticket_ids(it):
                by_ticket.setdefault(tid, []).append(it)

        pairs = []
        seen_pairs: set[tuple[str, str]] = set()

        # 1. shared Kapture ticket id — strongest, and not time-bounded
        for tid, group in by_ticket.items():
            for i, a in enumerate(group):
                for b in group[i + 1:]:
                    key = tuple(sorted((a["issue_id"], b["issue_id"])))
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    pairs.append((b["issue_id"], a["issue_id"], "kapture_id", 0.95,
                                  f"shares Kapture ticket {tid}"))

        # 2. the heuristic, over same-author neighbouring time buckets only
        candidates = []
        for (author, b), group in by_author.items():
            nxt = by_author.get((author, b + 1), [])
            for i, a in enumerate(group):
                for x in group[i + 1:] + nxt:
                    candidates.append((a, x))

        comparisons = len(candidates)
        for a, b in candidates:
            key = tuple(sorted((a["issue_id"], b["issue_id"])))
            if key in seen_pairs:
                continue
            # Same author is guaranteed by the bucket key. Same-channel pairs belong to
            # stage 5, not here.
            if a["anchor_channel_id"] == b["anchor_channel_id"]:
                continue
            dt = abs(b["ts_epoch"] - a["ts_epoch"])
            if dt > WINDOW_S:
                continue
            if a["dc_code"] and b["dc_code"] and a["dc_code"] != b["dc_code"]:
                continue
            sim = SequenceMatcher(None, _norm(a["text"]), _norm(b["text"])).ratio()
            if sim >= TEXT_SIMILARITY:
                seen_pairs.add(key)
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
        naive = len(issues) * (len(issues) - 1) // 2
        stats = {
            "duplicate_pairs": len(pairs),
            "by_method": {m: sum(1 for p in pairs if p[2] == m)
                          for m in {p[2] for p in pairs}},
            "cross_channel_pairs": cross,
            "source": getattr(src, "name", type(src).__name__),
            "issues_marked_duplicate": len({p[0] for p in pairs}),
            "comparisons": comparisons,
            "comparisons_naive": naive,
            "skipped_by_bucketing": naive - comparisons,
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
