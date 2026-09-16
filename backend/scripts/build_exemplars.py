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

── THIS IS WHERE THE CREEP HAPPENS ───────────────────────────────────────────────────────────
Human confirmations from the dashboard's NOVEL queue are merged in here, which is the step that
makes the loop close. Three things can happen to a confirmation:

  promoted   its text matches a corpus row  -> that row becomes gold with the human's label.
             A row the engine called NOVEL becomes a real exemplar, so the same wording is
             classified next run instead of queued again.
  added      its text is new                -> a new gold exemplar.
  noise      the human said "not an issue"  -> DROPPED, and any matching corpus row is dropped
             with it. A message a person judged not to be an issue must not teach the matcher
             what an issue looks like.

Confirmations live in `app/intake/labels.py`, deliberately NOT in the file this script writes —
this script overwrites its output, so a label stored there would vanish on the next rebuild.

One exclusion survives a human: a confirmation on a HELD-OUT row stays out of the default build.
Otherwise the classifier is scored on text it has memorised and the score stops meaning anything.
The count is printed so the exclusion is visible rather than silent.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.intake import labels  # noqa: E402

CORPUS = ROOT / "data" / "intake" / "corpus" / "dispositions.json"
HOLDOUT = ROOT / "data" / "intake" / "corpus" / "dispositions_holdout.json"
OUT = ROOT / "data" / "intake" / "corpus" / "exemplars.json"


def merge_confirmations(rows: list[dict], conf: dict[str, dict]) -> tuple[list[dict], dict]:
    """Fold human confirmations into the corpus rows. Returns (rows, counts).

    Runs BEFORE the NOVEL and gold/silver filters on purpose: a corpus row the engine could not
    classify carries `disposition == "NOVEL"` and would be excluded, but once a human names it,
    it is an ordinary labelled row and belongs in the index. That conversion is the entire point
    of the queue.
    """
    counts = {"promoted": 0, "added": 0, "noise_dropped": 0}
    out, seen = [], set()

    for r in rows:
        c = conf.get(r["id"])
        seen.add(r["id"])
        if not c:
            out.append(r)
            continue
        if c["decision"] == "not_an_issue":
            counts["noise_dropped"] += 1
            continue                       # a human said this is not an issue — it teaches nothing
        out.append({**r, "disposition": c["disposition"], "label_provenance": "gold"})
        counts["promoted"] += 1

    for c in conf.values():
        if c["id"] in seen or c["decision"] != "label":
            continue
        out.append({"id": c["id"], "text": c["text"], "disposition": c["disposition"],
                    "label_provenance": "gold"})
        counts["added"] += 1

    return out, counts


def build(use_all: bool = False, conf: dict[str, dict] | None = None
          ) -> tuple[list[dict], dict]:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))["messages"]
    held = set()
    if not use_all and HOLDOUT.exists():
        held = {m["id"] for m in json.loads(HOLDOUT.read_text(encoding="utf-8"))["messages"]}

    conf = labels.load() if conf is None else conf
    rows, cc = merge_confirmations(corpus, conf)

    # A confirmation on a held-out row is honoured as a LABEL but still excluded from the index,
    # or the classifier is scored on text it has memorised.
    held_conf = sum(1 for cid in conf if cid in held)

    pool = [r for r in rows if r["id"] not in held and r["disposition"] != "NOVEL"]

    gold_dispositions = {r["disposition"] for r in pool if r.get("label_provenance") == "gold"}
    kept = [r for r in pool
            if r.get("label_provenance") == "gold"
            or r["disposition"] not in gold_dispositions]

    exemplars = [{"text": r["text"], "disposition": r["disposition"],
                  "label_provenance": r.get("label_provenance", "silver"), "id": r["id"]}
                 for r in kept]
    meta = {
        "total_corpus": len(corpus),
        "held_out_excluded": len(held),
        "novel_excluded": sum(1 for r in rows if r["disposition"] == "NOVEL"
                              and r["id"] not in held),
        "silver_dropped_for_gold": len(pool) - len(kept),
        "confirmations": len(conf),
        "confirmed_promoted": cc["promoted"],
        "confirmed_added": cc["added"],
        "confirmed_noise_dropped": cc["noise_dropped"],
        "confirmed_held_out_excluded": held_conf,
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
    if meta["confirmations"]:
        print(f"  human confirmations: {meta['confirmations']} "
              f"({meta['confirmed_promoted']} promoted a corpus row, "
              f"{meta['confirmed_added']} new, "
              f"{meta['confirmed_noise_dropped']} dropped as not-an-issue"
              + (f", {meta['confirmed_held_out_excluded']} held out"
                 if meta["confirmed_held_out_excluded"] else "") + ")")
    else:
        print("  human confirmations: none yet — confirm NOVEL items in the dashboard and the "
              "next build picks them up")
    for d, n in meta["by_disposition"].items():
        print(f"    {d:<26} {n:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
