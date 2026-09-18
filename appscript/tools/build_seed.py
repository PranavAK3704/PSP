#!/usr/bin/env python3
"""Rebuild the three seed CSVs in appscript/seed/ from local source data.

    python appscript/tools/build_seed.py

── YOU PROBABLY DO NOT NEED THIS ───────────────────────────────────────────────────────────────
The seed CSVs are a BOOTSTRAP. Once they are imported into the Sheet, the Sheet is the source of
truth: naming a NOVEL ticket in the UI appends a gold row to the `_exemplars` tab, and the DC
tabs are edited in place. Re-running this script and re-importing would OVERWRITE those tabs and
throw away every human label accumulated since.

So use it when you are setting up a second Sheet, or recovering from a deleted tab — not as
routine maintenance.

── WHY THIS EXISTS AT ALL ──────────────────────────────────────────────────────────────────────
It replaces a three-script chain in the retired Python intake (build_dc_registry →
build_corpus_from_audits → build_exemplars, plus app/intake/labels.py). Deleting those without
leaving a way to regenerate would have quietly removed the ability to stand this system up
again. This script imports nothing from the PSP backend — the Apps Script build depends on no
Python whatsoever, which was the point of the clean break.

── SOURCES ─────────────────────────────────────────────────────────────────────────────────────
  _exemplars.csv     backend/data/intake/corpus/exemplars.json   (gitignored — partner text)
  _dc_codes.csv      already committed; rebuilt only if the JSON registry below is present
  _dc_denylist.csv   already committed; hand-maintained from here on

Nothing here reaches the network and nothing is overwritten unless its source exists.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = HERE.parent / "seed"
BACKEND = HERE.parents[1] / "backend"

EXEMPLAR_SRC = BACKEND / "data" / "intake" / "corpus" / "exemplars.json"


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def build_exemplars() -> int:
    """`id, disposition, label_provenance, text` — one row per labelled message.

    Newlines inside a message are normalised to \\n so a row cannot straddle two CSV records in
    a way Google Sheets' importer and this file's reader disagree about.
    """
    if not EXEMPLAR_SRC.exists():
        print(f"  skip  _exemplars.csv  ({EXEMPLAR_SRC} not found)")
        return 0
    ex = json.loads(EXEMPLAR_SRC.read_text(encoding="utf-8"))["exemplars"]
    rows = []
    for i, e in enumerate(ex, 1):
        text = (e.get("text") or "").replace("\r\n", "\n").replace("\r", "\n")
        rows.append([e.get("id") or f"EX{i:05d}",
                     e["disposition"],
                     e.get("label_provenance", "silver"),
                     text])
    write_csv(SEED / "_exemplars.csv", ["id", "disposition", "label_provenance", "text"], rows)
    print(f"  wrote _exemplars.csv     {len(rows):>6} rows")
    return len(rows)


def check_committed(name: str, header: str) -> int:
    """The DC tabs are committed and hand-maintained. Report rather than regenerate — a code
    removed from the denylist becomes extractable again, and that should be a deliberate edit
    with a commit behind it, never a side effect of running a build script."""
    p = SEED / name
    if not p.exists():
        print(f"  MISSING {name} — restore it from git: git checkout appscript/seed/{name}")
        return 0
    n = sum(1 for _ in p.open(encoding="utf-8")) - 1
    print(f"  ok    {name:<20} {n:>6} rows (committed, hand-maintained)")
    return n


def main() -> int:
    print(f"seed dir: {SEED}")
    build_exemplars()
    check_committed("_dc_codes.csv", "code")
    check_committed("_dc_denylist.csv", "token")
    print("\nImport into the Sheet with File -> Import -> Upload -> Insert new sheet(s),")
    print("then rename each tab to match the filename without the .csv.")
    print("WARNING: re-importing _exemplars replaces any gold rows the UI has added since.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
