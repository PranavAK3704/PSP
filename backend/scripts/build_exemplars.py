"""Build the disposition exemplar index from the labelled corpus.

    python scripts/build_exemplars.py            # train split only
    python scripts/build_exemplars.py --all      # every row (for production, after scoring)

── TRAIN SPLIT ONLY, BY DEFAULT ──────────────────────────────────────────────────────────────
If held-out rows become exemplars, the classifier is scored against messages it has memorised
and the number is meaningless. The default builds from the 1,458-row train split so
scripts/score_classifier.py measures something real. Use --all only after you have a score you
believe, for the index that actually ships.

── NOVEL IS NOT A DISPOSITION AND MAKES NO EXEMPLAR ──────────────────────────────────────────
NOVEL is the ABSENCE of a match, not a class. Indexing NOVEL rows would teach the matcher to
recognise "novel-ness", which is not a thing — and worse, it would let a genuinely new issue
match an old unclassified one and be marked as understood. They are excluded.

── GOLD OUTRANKS SILVER ──────────────────────────────────────────────────────────────────────
Corpus labels are silver: assigned by the engine's own classifier, not a human. Once gold rows
exist for a disposition (a human confirmed them), silver rows for THAT disposition are dropped —
otherwise a large, cheap, possibly-wrong silver set drowns the small, expensive, correct one.
Dispositions with no gold yet keep their silver exemplars, so coverage never regresses.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "data" / "intake" / "corpus" / "dispositions.json"
HOLDOUT = ROOT / "data" / "intake" / "corpus" / "dispositions_holdout.json"
OUT = ROOT / "data" / "intake" / "corpus" / "exemplars.json"


def build(use_all: bool = False) -> tuple[list[dict], dict]:
    rows = json.loads(CORPUS.read_text(encoding="utf-8"))["messages"]
    held = set()
    if not use_all and HOLDOUT.exists():
        held = {m["id"] for m in json.loads(HOLDOUT.read_text(encoding="utf-8"))["messages"]}

    pool = [r for r in rows if r["id"] not in held and r["disposition"] != "NOVEL"]

    gold_dispositions = {r["disposition"] for r in pool if r.get("label_provenance") == "gold"}
    kept = [r for r in pool
            if r.get("label_provenance") == "gold"
            or r["disposition"] not in gold_dispositions]

    exemplars = [{"text": r["text"], "disposition": r["disposition"],
                  "label_provenance": r.get("label_provenance", "silver"), "id": r["id"]}
                 for r in kept]
    meta = {
        "total_corpus": len(rows),
        "held_out_excluded": len(held),
        "novel_excluded": sum(1 for r in rows if r["disposition"] == "NOVEL"
                              and r["id"] not in held),
        "silver_dropped_for_gold": len(pool) - len(kept),
        "exemplars": len(exemplars),
        "by_disposition": dict(Counter(e["disposition"] for e in exemplars).most_common()),
        "gold": sum(1 for e in exemplars if e["label_provenance"] == "gold"),
    }
    return exemplars, meta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--all", action="store_true",
                    help="include held-out rows — ONLY after you trust the score")
    a = ap.parse_args()

    exemplars, meta = build(a.all)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(
        {"_what": "Disposition exemplars for app/intake/classify.py.",
         "_split": "ALL ROWS — scoring against this is meaningless" if a.all
                   else "TRAIN SPLIT ONLY — held-out rows excluded so scoring means something",
         "_labels": "silver unless marked gold; gold outranks silver per disposition",
         **meta, "exemplars": exemplars}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")

    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"  {meta['exemplars']} exemplars across {len(meta['by_disposition'])} dispositions")
    print(f"  excluded: {meta['held_out_excluded']} held-out, {meta['novel_excluded']} NOVEL")
    print(f"  gold: {meta['gold']}   silver dropped in favour of gold: "
          f"{meta['silver_dropped_for_gold']}")
    for d, n in meta["by_disposition"].items():
        print(f"    {d:<26} {n:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
