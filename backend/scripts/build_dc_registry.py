"""Build the DC-code registry and its denylist from data already in this repo.

    python scripts/build_dc_registry.py [--out config/dc_codes.txt]

── WHY THIS EXISTS RATHER THAN AN EMPTY PLACEHOLDER ──────────────────────────────────────────
The intake brief planned `config/dc_codes.txt` as an empty file to wait on from ops. The codes
were already here, as four SQLite columns nobody had unioned:

    valmo.db   losses.location          11,370 distinct 3-char   (LOWERCASE)
    tickets.db tickets.hub_code          3,959                   (UPPERCASE)
    valmo.db   attribution.entity_id     3,188                   (UPPERCASE)
    valmo.db   qc_fail.hub_location        416                   (LOWERCASE)
                                        ------
                          union         11,723

Two of those sources are lowercase and two uppercase, so a naive `GLOB '[A-Z0-9][A-Z0-9][A-Z0-9]'`
returns ZERO rows against `losses.location` despite 11,370 codes being present. Everything is
UPPER()ed before the union for that reason.

`losses.location` is also a MIXED column — it holds 11-digit partner IDs, and the literals
'PUN-DC' and 'meesho'. The `LENGTH = 3` filter is what keeps partner IDs (which ARE PII under
`app/substrate/dataplane.py`'s own rules) out of a file labelled "DC registry".

── THIS IS A SEED, NOT THE OPS MASTER ────────────────────────────────────────────────────────
Every code here is OBSERVED in transactional data, so the set contains only hubs that appear in
losses, tickets or QC records. A new DC with no history is absent, and 5 of the 25 DC codes
observed in the Slack channels (L9D, T5X, RX3, R2F, AML) are already missing — which suggests
the ops list is genuinely newer or broader. Reconcile, do not replace: `evaluate` reports
registry coverage as its own number so partial coverage never reads as poor regex recall.

There is NO code -> name/city mapping anywhere in the repo. `attribution.metadata_party_name`
looks like one and is populated, but it is exactly `lower(entity_id)` in 100% of rows. Ops
still owes names and cities; they do not owe the codes.

── THE DENYLIST IS NOT OPTIONAL ──────────────────────────────────────────────────────────────
The brief's Tier B premise was "only the registry rejects it". That is half right. The registry
correctly rejects `342` (the canonical case: "342 Tids are coming in Hardstop loss"), but it
CONTAINS ordinary words and ops acronyms as genuine hub codes:

    ALL AND APP DAY FAD NEW OLD PFB PLS RVP SIR TID YES

`FAD`, `PFB` and `RVP` are on the brief's own ops-acronym list, and `TID` is in there while the
canonical rejection case is literally about "Tids". So registry membership alone is not
sufficient evidence of a DC code, and `app/engine/algo/entities.py` already learned the same
lesson the hard way — an earlier version matched "OLD" and "SIR" and produced 16.7% pure noise.
The denylist is seeded from that module's measured `_NOT_AN_ID` residue, plus the ops acronyms.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.engine.algo.entities import _NOT_AN_ID  # noqa: E402  measured false-positive residue

VALMO_DB = ROOT / "data" / "valmo.db"
TICKETS_DB = ROOT / "data" / "tickets.db"
OUT_CODES = ROOT / "config" / "dc_codes.txt"
OUT_DENY = ROOT / "config" / "dc_denylist.txt"

#: (attached-db alias, table, column). UPPER() and LENGTH=3 are applied to every one.
SOURCES = [
    ("main", "losses", "location"),
    ("main", "qc_fail", "hub_location"),
    ("main", "captain_summary", "hub"),
    ("main", "attribution", "entity_id"),
    ("t", "tickets", "hub_code"),
]

#: Ops acronyms that collide with the 3-char code shape. From the intake brief's own list;
#: several of these are ALSO in the registry as real hub codes, which is exactly the problem.
OPS_ACRONYMS = {
    "TAT", "RTO", "RVP", "OBC", "FYI", "PFB", "DRS", "ETA", "FAD", "TID", "AWB", "COD",
    "SLA", "POD", "QC", "FE", "DC", "LM", "FM", "RVP", "NDR", "OFD", "EDD", "CRM",
}


def _distinct(con: sqlite3.Connection, alias: str, table: str, col: str) -> set[str]:
    prefix = "" if alias == "main" else f"{alias}."
    try:
        rows = con.execute(
            f"SELECT DISTINCT UPPER({col}) FROM {prefix}{table} "
            f"WHERE {col} IS NOT NULL AND LENGTH({col}) = 3").fetchall()
    except sqlite3.OperationalError as e:
        print(f"  ! {prefix}{table}.{col}: {e}", file=sys.stderr)
        return set()
    return {r[0] for r in rows if r[0]}


def build() -> tuple[set[str], set[str], dict[str, int]]:
    if not VALMO_DB.exists():
        raise SystemExit(f"{VALMO_DB} not found — the registry is derived from it.")
    con = sqlite3.connect(f"file:{VALMO_DB}?mode=ro", uri=True)
    if TICKETS_DB.exists():
        con.execute("ATTACH ? AS t", (f"file:{TICKETS_DB}?mode=ro",))

    codes: set[str] = set()
    per_source: dict[str, int] = {}
    for alias, table, col in SOURCES:
        got = _distinct(con, alias, table, col)
        per_source[f"{table}.{col}"] = len(got)
        codes |= got
    con.close()

    deny = {c for c in (_NOT_AN_ID | OPS_ACRONYMS) if len(c) == 3}
    # Purely numeric 3-char codes are real in this data (239, 897) but indistinguishable from
    # the counts partners type ("342 Tids"). They stay OUT of the bare-token tier.
    numeric = {c for c in codes if c.isdigit()}
    return codes, deny | numeric, per_source


def write(codes: set[str], deny: set[str], per_source: dict[str, int]) -> None:
    OUT_CODES.parent.mkdir(parents=True, exist_ok=True)
    collisions = sorted(codes & deny)

    OUT_CODES.write_text(
        "# DC / hub code registry — one code per line, UPPERCASE.\n"
        "# Lines starting with '#' are comments. Blank lines are ignored. Anything after the\n"
        "# code on a line is ignored too, so a pasted 'CODE,City' or 'CODE  Name' column works\n"
        "# without reformatting.\n"
        "#\n"
        "# TO REPLACE WITH THE OPS MASTER: delete everything below this header and paste the\n"
        "# code column in. Nothing else needs to change — Tier B reads this file directly.\n"
        "#\n"
        f"# SEEDED {date.today().isoformat()} by scripts/build_dc_registry.py from data already\n"
        "# in this repo. These codes are OBSERVED in transactional data, not authored by ops:\n"
        + "".join(f"#   {k:<28} {v:>6}\n" for k, v in sorted(per_source.items()))
        + f"#   {'UNION (this file)':<28} {len(codes):>6}\n"
        "#\n"
        "# NOT THE OPS MASTER. A hub with no loss/ticket/QC history is absent, and 5 of the 25\n"
        "# codes observed in the Slack channels (L9D, T5X, RX3, R2F, AML) are missing from it.\n"
        "# `intake evaluate` reports registry coverage separately so that stays visible.\n"
        "#\n"
        f"# {len(collisions)} of these are ALSO in config/dc_denylist.txt and are therefore NOT\n"
        "# usable as bare Tier B tokens: " + " ".join(collisions) + "\n"
        "#\n"
        + "\n".join(sorted(codes)) + "\n",
        encoding="utf-8")

    OUT_DENY.write_text(
        "# Tokens that must NEVER be extracted as a DC code, even when they appear in\n"
        "# config/dc_codes.txt as genuine hub codes. Applies to BOTH tiers.\n"
        "#\n"
        "# WHY THIS FILE IS REQUIRED, MEASURED:\n"
        "#   The brief's Tier B rule was 'only the registry rejects it'. The registry does\n"
        "#   reject 342 — but it CONTAINS ALL, AND, APP, DAY, FAD, NEW, OLD, PFB, PLS, RVP,\n"
        "#   SIR, TID and YES as real hub codes. FAD/PFB/RVP are on the brief's own ops-acronym\n"
        "#   list, and TID is in there while the canonical rejection case is about 'Tids'.\n"
        "#   app/engine/algo/entities.py learned this already: an earlier version matched 'OLD'\n"
        "#   and 'SIR' and produced 16.7% pure noise.\n"
        "#\n"
        "# Seeded from entities._NOT_AN_ID (the measured residue — every entry appeared in a\n"
        "# real false positive), the ops acronyms, and every purely-numeric code.\n"
        "#\n"
        "# A code removed from here becomes extractable again. Do that only with evidence.\n"
        + "\n".join(sorted(deny)) + "\n",
        encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--print-only", action="store_true", help="report counts, write nothing")
    args = ap.parse_args()

    codes, deny, per_source = build()
    print("sources:")
    for k, v in sorted(per_source.items()):
        print(f"  {k:<30} {v:>6}")
    print(f"  {'UNION':<30} {len(codes):>6}")
    print(f"denylist: {len(deny)} tokens ({len(codes & deny)} of them also in the registry)")

    # The canonical acceptance case from the brief.
    print(f"\n'342' in registry: {'342' in codes}  (must be False — "
          f"'342 Tids are coming in Hardstop loss')")

    if args.print_only:
        return 0
    write(codes, deny, per_source)
    print(f"\nwrote {OUT_CODES.relative_to(ROOT)}  ({len(codes)} codes)")
    print(f"wrote {OUT_DENY.relative_to(ROOT)}  ({len(deny)} tokens)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
