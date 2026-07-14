"""Build backend/data/valmo.db (SQLite) from a Metabase loss-query CSV export.

This is the production data path in miniature: the Metabase question
(gold.valmo_lost_awb_2k24_v1 …) is exported to CSV, and this job loads a
PII-stripped, stratified subset into a local SQLite indexed on AWB. The resolution
engine then does real per-AWB lookups against it (no live DB dependency) — exactly
what the team already does (Metabase → SQLite → query locally). In production this
runs on a schedule (or via the Metabase API) to refresh the file.

Usage:  python build_valmo_db.py [path/to/export.csv] [--seed-demo]

Production builds inject NO synthetic data. Pass --seed-demo (or set SEED_DEMO=1)
ONLY for the scripted showcase — it injects a curated hero AWB + matching
enrichment rows so the demo reverses via the real data path.
"""
import argparse, csv, glob, os, sqlite3, collections
from pathlib import Path

csv.field_size_limit(10 ** 7)


def _newest(pattern):
    hits = glob.glob(os.path.expanduser(pattern))
    return max(hits, key=os.path.getmtime) if hits else None


def _parse_args():
    ap = argparse.ArgumentParser(description="Build valmo.db from a Metabase loss-query CSV export.")
    ap.add_argument("csv", nargs="?", default=None, help="path to the export CSV (default: newest in ~/Downloads)")
    ap.add_argument("--seed-demo", action="store_true",
                    help="inject curated showcase (hero) rows for the scripted demo (default: off). "
                         "Also enabled by env SEED_DEMO=1. Production must NOT set this.")
    args = ap.parse_args()
    args.seed_demo = args.seed_demo or os.environ.get("SEED_DEMO", "") == "1"
    return args


DB = Path(__file__).resolve().parents[1] / "data" / "valmo.db"

# Columns we keep — the 4 name/remark columns are PII and are DROPPED (the engine
# never needs them to decide; it needs the AWB + amounts + reason + the two reversal
# signals facility_inscan / attribution_changed).
KEEP = ["awb", "consolidation_awb", "created_date", "lost_date", "actual_lost_date",
        "current_movement_type", "shipment_value", "loss_percentage", "loss_value",
        "location", "leg", "loc2", "leg2", "reason", "reason_l1",
        "attribution_changed", "facility_inscan", "DC_Tenurity"]
DROP_PII = {"transporter_or_FE_name", "accepted_by", "requested_by", "remarks"}

# Rows kept per reason_l1. 0 = unlimited (load the FULL export so ANY real AWB resolves).
# Set VALMO_CAP=2500 (etc.) to build a smaller subset for a lighter deploy image.
CAP_PER_REASON = int(os.environ.get("VALMO_CAP", "0"))

# A curated (SYNTHETIC) hero row so the scripted demo AWB reverses via the REAL data
# path. Injected ONLY under --seed-demo / SEED_DEMO=1 — never in a production build.
HERO = {"awb": "VL0093310077", "consolidation_awb": "", "created_date": "2026-06-20",
        "lost_date": "2026-06-25", "actual_lost_date": "2026-06-25", "current_movement_type": "forward",
        "shipment_value": "1860", "loss_percentage": "100%", "loss_value": "1860",
        "location": "PUN-DC", "leg": "LM", "loc2": "", "leg2": "",
        "reason": "hardstop_not_connected_within_sla", "reason_l1": "hardstop",
        "attribution_changed": "yes", "facility_inscan": "2026-06-25", "DC_Tenurity": ">56 days"}


def main():
    args = _parse_args()
    # losses = the given CSV, else the NEWEST valmo_lost export in Downloads.
    csv_path = args.csv or _newest("~/Downloads/valmo_lost_data_2k24_*.csv")
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE losses (%s)" % ",".join(f'"{c}" TEXT' for c in KEEP))

    buckets = collections.Counter()
    showcase = {"reversible": None, "escalate": None}
    rows, kept = 0, 0
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.DictReader(f)
        batch = []
        for row in r:
            rows += 1
            rl1 = row.get("reason_l1", "")
            if CAP_PER_REASON and buckets[rl1] >= CAP_PER_REASON:
                continue
            buckets[rl1] += 1
            kept += 1
            row["awb"] = (row.get("awb") or "").strip().upper()
            batch.append(tuple(row.get(c, "") for c in KEEP))
            # capture a couple of real showcase AWBs for the demo
            if rl1 == "hardstop" and row.get("loss_percentage") == "100%":
                if row.get("facility_inscan") and not showcase["reversible"]:
                    showcase["reversible"] = row["awb"]
                if not row.get("facility_inscan") and row.get("attribution_changed") == "no" and not showcase["escalate"]:
                    showcase["escalate"] = row["awb"]
            if len(batch) >= 5000:
                con.executemany("INSERT INTO losses VALUES (%s)" % ",".join("?" * len(KEEP)), batch)
                batch = []
        if batch:
            con.executemany("INSERT INTO losses VALUES (%s)" % ",".join("?" * len(KEEP)), batch)

    # Curated hero row — synthetic showcase data, ONLY when --seed-demo is passed.
    # Production builds inject zero synthetic rows.
    if args.seed_demo:
        print("[seed-demo] injecting showcase rows (hero AWB %s) — NOT for production" % HERO["awb"])
        # inject the hero (replace if the AWB happened to appear in the sample)
        con.execute("DELETE FROM losses WHERE awb = ?", (HERO["awb"],))
        con.execute("INSERT INTO losses VALUES (%s)" % ",".join("?" * len(KEEP)),
                    tuple(HERO.get(c, "") for c in KEEP))
    con.execute("CREATE INDEX idx_awb ON losses(awb)")
    con.commit()

    total = con.execute("SELECT COUNT(*) FROM losses").fetchone()[0]
    print(f"read {rows} rows → kept {kept}{' (+hero)' if args.seed_demo else ''} → {total} in {DB}")
    print("by reason_l1:", dict(buckets))
    print("SHOWCASE reversible AWB:", showcase["reversible"], "| escalate AWB:", showcase["escalate"])

    load_enrichment(con)
    if args.seed_demo:
        _inject_hero_enrichment(con)
    con.close()


def _inject_hero_enrichment(con):
    """So the join is visibly demoable on the hero AWB (the 3 real samples are disjoint slices
    and share no AWBs). Adds a matching pendency + attribution-change row for VL0093310077."""
    hero = "VL0093310077"
    try:
        con.execute("DELETE FROM pendency WHERE awb=?", (hero,))
        con.execute("INSERT INTO pendency (awb,current_status,current_movement_type,current_location,"
                    "current_location_zone,latest_scan_date,misroute_type,promise_date) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (hero, "delivered", "forward", "PUN-DC", "West", "2026-06-25", "", "2026-06-26"))
        con.execute("DELETE FROM attrib_change WHERE awb=?", (hero,))
        con.execute("INSERT INTO attrib_change (awb,attribution_changed,previous_leg1,previous_loss_percentage,"
                    "previous_transporter_or_FE_name,latest_leg1,latest_loss_percentage,latest_transporter_or_FE_name) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (hero, "yes", "LM", "100%", "FE-10075148771", "meesho", "0%", "meesho"))
        con.commit()
        print("  [seed-demo] injected hero enrichment (pendency + attrib_change) for", hero)
    except Exception as e:  # noqa: BLE001 — enrichment tables may be absent
        print("  (skipped hero enrichment:", e, ")")


def _load_csv_table(con, table: str, path: str, awb_col: str = "awb"):
    """Load a full CSV into its own table (all columns TEXT), indexed on awb (uppercased).
    If the source keys on a different column (e.g. awb_num), a normalised `awb` column is added."""
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.DictReader(f)
        cols = list(r.fieldnames)
        out = cols + (["awb"] if awb_col != "awb" else [])
        con.execute(f"DROP TABLE IF EXISTS {table}")
        con.execute(f"CREATE TABLE {table} (%s)" % ",".join(f'"{c}" TEXT' for c in out))
        batch, n = [], 0
        for row in r:
            key = (row.get(awb_col) or "").strip().upper()
            if awb_col == "awb":
                row["awb"] = key
            vals = tuple(row.get(c, "") for c in cols) + ((key,) if awb_col != "awb" else ())
            batch.append(vals); n += 1
            if len(batch) >= 5000:
                con.executemany(f"INSERT INTO {table} VALUES (%s)" % ",".join("?" * len(out)), batch); batch = []
        if batch:
            con.executemany(f"INSERT INTO {table} VALUES (%s)" % ",".join("?" * len(out)), batch)
    con.execute(f"CREATE INDEX idx_{table}_awb ON {table}(awb)")
    con.commit()
    print(f"  loaded {table}: {n} rows")


def load_enrichment(con):
    """Load the 3 per-AWB enrichment exports (identified by a signature column) into their own
    tables so a disputed AWB can be joined to its current pendency + loss + attribution history."""
    print("enrichment tables (join on awb):")
    paths = glob.glob(os.path.expanduser("~/Downloads/20260708_*.csv")) + \
            glob.glob(os.path.expanduser("~/Downloads/query_result_*.csv")) + \
            glob.glob(os.path.expanduser("~/Downloads/qc_fail_*.csv"))
    for path in sorted(paths):
        try:
            hdr = open(path, encoding="utf-8", errors="replace").readline()
        except OSError:
            continue
        cols = set(c.strip() for c in hdr.split(","))
        if "attribution_state" in cols:                 # the loss-attribution ledger
            _load_csv_table(con, "attribution", path)
        elif "sec_qc_lm_status" in cols or "awb_num" in cols:   # secondary-QC-fail evidence (keys on awb_num)
            _load_csv_table(con, "qc_fail", path, awb_col="awb_num")
        elif "cn_flag" in cols:
            _load_csv_table(con, "loss_attrib", path)
        elif "previous_leg1" in cols:
            _load_csv_table(con, "attrib_change", path)
        elif "misroute_type" in cols:
            _load_csv_table(con, "pendency", path)


if __name__ == "__main__":
    main()
