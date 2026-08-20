"""Materialise the `captain_summary` table — the aggregate the engine sends to the model.

WHY A TABLE AND NOT A QUERY PER TURN
The query set is fixed. `loss_db.captain_summary()` is two aggregate queries over
`attribution`; on the local file that is free, but on the remote (Turso) path it is two
HTTPS round-trips on every turn that touches captain context. Computing once and reading
with a single indexed lookup is the same answer for a fraction of the latency.

It is an OPTIMISATION, never a dependency: `loss_db.read_captain_summary()` falls back to
computing live when the table is absent, so a deploy that forgot to run this is slower, not
broken. That property is what makes it safe to add.

NO LLM ANYWHERE IN THIS PATH. It is SQL in, SQL out.

Usage
  python scripts/build_captain_summary.py                 # the demo partner set (default)
  python scripts/build_captain_summary.py --all           # every partner in the ledger
  python scripts/build_captain_summary.py --limit 500
  python scripts/build_captain_summary.py --db data/valmo.db

Then push it to Turso (only this table, not the 1M-row losses export):
  python scripts/push_to_turso.py --tables captain_summary
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "valmo.db"

DDL = """
CREATE TABLE IF NOT EXISTS captain_summary (
  partner_id        TEXT PRIMARY KEY,
  hub               TEXT,
  debits_on_record  INTEGER,
  open_debits       INTEGER,
  total_debited_inr REAL,
  recovered_inr     REAL,
  pending_inr       REAL,
  failed_inr        REAL,
  reversals         INTEGER,
  reversed_inr      REAL,
  loss_type_mix     TEXT,      -- JSON: {"hardstop": 39}
  first_debit       TEXT,
  last_debit        TEXT,
  computed_at       TEXT
)
"""

COLS = ["partner_id", "hub", "debits_on_record", "open_debits", "total_debited_inr",
        "recovered_inr", "pending_inr", "failed_inr", "reversals", "reversed_inr",
        "loss_type_mix", "first_debit", "last_debit", "computed_at"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--all", action="store_true",
                    help="every partner in the ledger, not just the demo set")
    ap.add_argument("--limit", type=int, default=0, help="cap the partner count (0 = no cap)")
    a = ap.parse_args()

    sys.path.insert(0, str(ROOT))
    from app.substrate import loss_db

    if not loss_db.available():
        print("no loss DB available (set TURSO_* or provide backend/data/valmo.db)")
        return 1

    # WHICH partners. The default is the demo set known_partners() serves, because that is
    # what a conversation can actually be opened about. --all is the production shape.
    if a.all:
        rows = loss_db._query(
            "SELECT partner_id, COUNT(*) AS n FROM attribution"
            " WHERE partner_id IS NOT NULL AND partner_id != ''"
            " GROUP BY partner_id ORDER BY n DESC" + (f" LIMIT {int(a.limit)}" if a.limit else ""), ())
        partners = [str(r["partner_id"]) for r in rows]
    else:
        partners = loss_db.known_partners(limit=a.limit or 12)
    if not partners:
        print("no partners found in `attribution`")
        return 1

    # The table is written to the LOCAL file even when the engine is reading Turso: the
    # local file is the build artefact, and push_to_turso.py is the one thing that writes
    # remotely. One writer, one direction — so a half-finished build can never be what the
    # engine is serving.
    target = Path(a.db)
    if not target.exists():
        print(f"{target} does not exist — build it first (scripts/build_valmo_db.py)")
        return 1
    con = sqlite3.connect(str(target))
    con.execute(DDL)

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = skipped = 0
    for pid in partners:
        s = loss_db.captain_summary(pid)
        if not s:
            skipped += 1
            continue
        con.execute(
            f"INSERT OR REPLACE INTO captain_summary ({','.join(COLS)})"
            f" VALUES ({','.join('?' * len(COLS))})",
            (pid, s.get("hub", ""), s["debits_on_record"], s["open_debits"],
             s["total_debited_inr"], s["recovered_inr"], s["pending_inr"], s["failed_inr"],
             s["reversals"], s["reversed_inr"], json.dumps(s.get("loss_type_mix") or {}),
             s.get("first_debit", ""), s.get("last_debit", ""), now))
        written += 1
    con.commit()

    # VERIFY, in the same run. A materialised aggregate that silently disagrees with the live
    # computation is worse than no table at all — the engine would serve stale numbers with
    # full confidence. So every written row is read back and compared field by field.
    con.row_factory = sqlite3.Row
    mismatches = []
    for pid in partners:
        live = loss_db.captain_summary(pid)
        if not live:
            continue
        got = con.execute("SELECT * FROM captain_summary WHERE partner_id = ?", (pid,)).fetchone()
        if got is None:
            mismatches.append((pid, "missing from table")); continue
        for k in ("debits_on_record", "open_debits", "total_debited_inr", "recovered_inr",
                  "pending_inr", "reversals"):
            if round(float(got[k] or 0)) != round(float(live[k] or 0)):
                mismatches.append((pid, f"{k}: table {got[k]} vs live {live[k]}"))
    con.close()

    print(f"captain_summary: {written} row(s) written, {skipped} partner(s) had no debits")
    if mismatches:
        print("MISMATCH between the materialised table and the live computation:")
        for pid, why in mismatches[:20]:
            print(f"   {pid}: {why}")
        return 1
    print(f"verified: all {written} row(s) match the live computation")
    print("push it with:  python scripts/push_to_turso.py --tables captain_summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
