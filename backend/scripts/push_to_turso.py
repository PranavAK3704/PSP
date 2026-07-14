"""Push the local valmo.db tables to an external Turso (libSQL) database over HTTPS.

No Turso CLI / admin needed — pure Python client. Once the DB is queryable in Turso, the
deployed image ships WITHOUT the heavy data file; the engine (app/substrate/loss_db.py)
queries Turso when TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are set.

Usage:
  python scripts/push_to_turso.py --url libsql://<db>.turso.io --token <token> [--limit N]

--limit caps rows per table (handy for a fast first push; omit for everything). HTTP bulk
insert of the full 1M-row losses table is slow — for the full set, a subset db
(VALMO_CAP=3000 python scripts/build_valmo_db.py) or the Turso CLI `db import` is faster.
"""
import argparse, os, sqlite3, sys
from pathlib import Path

DB = Path(__file__).resolve().parents[1] / "data" / "valmo.db"
BATCH = 1000     # rows per HTTPS round-trip (bigger = fewer trips = faster full push)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=os.environ.get("TURSO_DATABASE_URL"))
    ap.add_argument("--token", default=os.environ.get("TURSO_AUTH_TOKEN"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--db", default=str(DB))
    a = ap.parse_args()
    if not a.url or not a.token:
        sys.exit("need --url and --token (or TURSO_DATABASE_URL / TURSO_AUTH_TOKEN)")

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.substrate import turso_http as th
    local = sqlite3.connect(a.db)
    local.row_factory = sqlite3.Row

    tables = [r[0] for r in local.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    print("pushing tables:", tables, flush=True)
    for t in tables:
        cols = [r[1] for r in local.execute(f"PRAGMA table_info({t})")]
        th.execute(a.url, a.token, f"DROP TABLE IF EXISTS {t}")
        th.execute(a.url, a.token, "CREATE TABLE %s (%s)" % (t, ",".join(f'"{c}" TEXT' for c in cols)))
        ins = f"INSERT INTO {t} VALUES (%s)" % ",".join("?" * len(cols))
        q = f"SELECT * FROM {t}" + (f" LIMIT {a.limit}" if a.limit else "")
        batch, n = [], 0
        for row in local.execute(q):
            batch.append((ins, [row[c] for c in cols])); n += 1
            if len(batch) >= BATCH:
                th.batch(a.url, a.token, batch); batch = []
                if n % 25000 == 0:
                    print(f"  {t}: {n} rows…", flush=True)
        if batch:
            th.batch(a.url, a.token, batch)
        try:
            th.execute(a.url, a.token, f'CREATE INDEX IF NOT EXISTS idx_{t}_awb ON {t}("awb")')
        except Exception as e:  # noqa: BLE001 — table may not have awb
            print(f"  ({t}: index skipped: {e})")
        print(f"  ✓ {t}: {n} rows", flush=True)
    local.close()
    print("done — the engine queries Turso when TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are set.", flush=True)


if __name__ == "__main__":
    main()
