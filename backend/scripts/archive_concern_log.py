#!/usr/bin/env python3
"""Move test residue out of the concern ledger, keeping a restorable copy.

── WHAT IT ARCHIVES, AND WHAT IT DELIBERATELY DOES NOT ───────────────────────────────────────
Archives rows whose provenance is unambiguously not partner traffic:

    harness   written by check_*.py runs
    monitor   proactive risk scans, non-inbound by definition
    operator  the Advocate Test Bench, i.e. us driving the engine as a captain

KEEPS the `unclassified` rows, and that is a decision rather than an oversight. There are ~421 of
them, they predate the provenance field, and they sit on REAL partner ids — the Command Deck says
so on screen: "they are counted, because hiding a genuine concern is worse than counting an
uncertain one." Archiving them here would quietly reverse an editorial call the product makes out
loud. Use --since to remove a specific recent batch instead (e.g. rows an exploration run wrote
today), which is a narrower and more honest instrument.

Aggregates already exclude harness+monitor (`concern_log.NON_INBOUND_SOURCES`), so this changes no
KPI. What it changes is the RAW Concern Log view, which shows everything with a provenance chip —
and on a recording, 588 harness rows are the first thing on screen.

  python scripts/archive_concern_log.py --dry-run
  python scripts/archive_concern_log.py --apply
  python scripts/archive_concern_log.py --apply --since 2026-09-01
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app.durable_state import durable_path   # noqa: E402

ARCHIVE_SOURCES = ("harness", "monitor", "operator")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write the change (default is a dry run)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", default="", metavar="YYYY-MM-DD",
                    help="also archive rows logged on/after this date, whatever their provenance")
    a = ap.parse_args()

    store = durable_path("concern_log.json")
    rows = store.read_json([])
    if not rows:
        print("ledger is empty or unreadable — refusing to touch it")
        return 1

    def drop(c: dict) -> str | None:
        if str(c.get("source") or "") in ARCHIVE_SOURCES:
            return str(c.get("source"))
        if a.since and str(c.get("logged_at") or "") >= a.since:
            return f"since {a.since}"
        return None

    keep, moved = [], []
    for c in rows:
        why = drop(c)
        (moved if why else keep).append(c)

    by: dict[str, int] = {}
    for c in moved:
        k = drop(c) or "?"
        by[k] = by.get(k, 0) + 1
    print(f"  {len(rows)} rows  ->  keep {len(keep)}, archive {len(moved)}")
    for k, n in sorted(by.items(), key=lambda kv: -kv[1]):
        print(f"      {k:<18} {n}")

    if not a.apply:
        print("\n  dry run — nothing written. Re-run with --apply.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = Path("data") / f"concern_log.archived-{stamp}.json"
    out.write_text(json.dumps(moved, indent=1))
    store.write_text(json.dumps(keep, indent=1))
    print(f"\n  archived -> {out}")
    print(f"  ledger now {len(keep)} rows. Restore by concatenating that file back in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
