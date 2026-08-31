"""Write data/tickets_summary.json — the committed AGGREGATE of the ticket export.

    python scripts/build_tickets_summary.py

WHY: `tickets.db` is 39 MB and excluded by .dockerignore, so on Render every ticket endpoint
reported `available: false` and the Data Access page silently lost its strongest evidence —
140,875 real tickets, 82% arriving on WhatsApp, and only ~1.2% carrying a sub-type at all. That
last number is the one that reframes the whole access ask, and it was invisible in the one place
people would actually look at it.

WHAT MAKES THIS SAFE TO COMMIT while the database is not: it calls `tickets_db.summary()` and
`top_hubs()` and writes exactly what they return. Those are GROUP BY counts. No ticket text, no
subject lines, no phone numbers, no AWBs, no captain names — nothing that is not already in a
response the API serves. If summary() ever starts returning row-level detail, this script starts
leaking it, so that is the invariant to protect rather than a list of columns to remember.

Re-run it whenever the export is refreshed. It is a snapshot by design: `source` comes back as
"snapshot" rather than "local" so a reader can tell these numbers were frozen at a point in
time, the same rule the growth fixtures follow.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.substrate import tickets_db          # noqa: E402

OUT = ROOT / "data" / "tickets_summary.json"


def main() -> int:
    if not tickets_db.available():
        print(f"tickets.db not readable at {tickets_db._DB} — nothing to snapshot.")
        return 1
    s = tickets_db.summary()
    if not s.get("available"):
        print("summary() reports unavailable; aborting.")
        return 1
    s["top_hubs"] = tickets_db.top_hubs(12)
    s["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    s["generated_from"] = tickets_db._DB.name
    # Drop the live-source marker; the reader sets `source: "snapshot"` on load.
    s.pop("source", None)
    s.pop("available", None)

    # Guard the invariant rather than trusting it: refuse to write anything that looks like a
    # row rather than a count. A long string in an aggregate is a leak.
    def scan(o, path="") -> list[str]:
        bad = []
        if isinstance(o, dict):
            for k, v in o.items():
                bad += scan(v, f"{path}.{k}")
        elif isinstance(o, list):
            for i, v in enumerate(o):
                bad += scan(v, f"{path}[{i}]")
        elif isinstance(o, str) and len(o) > 80:
            bad.append(f"{path}: {len(o)}-char string")
        return bad
    long_strings = scan(s)
    if long_strings:
        print("REFUSING TO WRITE — these look like row data, not aggregates:")
        for b in long_strings[:10]:
            print("   ", b)
        return 1

    OUT.write_text(json.dumps(s, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT}  ({OUT.stat().st_size / 1024:.1f} KB)")
    print(f"  total {s['total']:,} tickets · {len(s.get('top_hubs') or [])} hubs · "
          f"{len(s.get('by_folder') or {})} folders")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
