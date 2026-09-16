"""Head-to-head: BM25 exemplars vs frozen embeddings, on the same held-out rows.

    python scripts/compare_classifiers.py        # needs embeddings.npz (see build_embedding_index.py)

── WHY THIS EXISTS ───────────────────────────────────────────────────────────────────────────
The plan made Tier 3 (embeddings) a DECISION POINT, not a foregone conclusion: "if it doesn't
beat Tier 2, stop — that's a real outcome." This is the script that decides it, and it is kept
so the decision can be re-run when the labels improve rather than remembered as a conclusion.

── THE COMPARISON MUST BE COVERAGE-MATCHED ───────────────────────────────────────────────────
BM25 is allowed to refuse (NOVEL) and so reports precision over the rows it CHOSE to answer.
An embedding classifier answers everything. Comparing 65.2%-over-82% against 57.7%-over-100%
flatters BM25 and is meaningless. This forces BM25 to 100% coverage so both answer the same
rows, and also prints the refusing configuration separately so the trade is visible.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "data" / "intake" / "corpus"


def main() -> int:
    try:
        import numpy as np
    except ImportError:
        raise SystemExit("needs numpy — run this in the venv that has the ML deps")
    if not (CORPUS / "embeddings.npz").exists():
        raise SystemExit(f"no embeddings.npz in {CORPUS} — run build_embedding_index.py first")

    from app.intake import classify

    meta = json.loads((CORPUS / "embedding_index.json").read_text())
    z = np.load(CORPUS / "embeddings.npz")
    tr, te = z["train"], z["test"]
    tr_lab = [r["disposition"] for r in meta["train"]]
    te_lab = [r["disposition"] for r in meta["holdout"]]
    te_ids = [r["id"] for r in meta["holdout"]]
    txt = {r["id"]: r["text"] for r in
           json.loads((CORPUS / "dispositions_holdout.json").read_text())["messages"]}

    real = [i for i, l in enumerate(te_lab) if l != "NOVEL"]
    sims = te[real] @ tr.T            # vectors are normalised, so dot == cosine
    m = classify.load_matcher()

    def emb_predict(row, k=5):
        idx = np.argpartition(-row, k)[:k]
        votes: dict[str, float] = {}
        for j in idx:
            votes[tr_lab[j]] = votes.get(tr_lab[j], 0.0) + float(row[j])
        return max(votes, key=votes.get)

    m.min_score, m.margin = 0.0, 0.0        # force BM25 to answer everything
    per = defaultdict(lambda: [0, 0, 0])
    for r_i, row in zip(real, sims):
        true = te_lab[r_i]
        per[true][0] += 1
        per[true][1] += emb_predict(row) == true
        per[true][2] += m.match(txt[te_ids[r_i]])["disposition"] == true

    n = sum(v[0] for v in per.values())
    e = sum(v[1] for v in per.values())
    b = sum(v[2] for v in per.values())
    print(f"\n{'=' * 74}\nBM25 vs EMBEDDINGS — {n} held-out rows, coverage-matched at 100%")
    print(f"{'=' * 74}\n")
    print(f"{'true disposition':<24} {'n':>4} {'embed':>7} {'BM25':>7}  verdict")
    for k, (cnt, ec, bc) in sorted(per.items(), key=lambda kv: -kv[1][0]):
        ep, bp = ec / cnt, bc / cnt
        v = ("embeddings WIN" if ep - bp > 0.12 else
             "BM25 win" if bp - ep > 0.12 else "tie")
        print(f"  {k:<22} {cnt:>4} {ep:>6.0%} {bp:>6.0%}  {v}")
    print(f"\n  {'OVERALL':<22} {n:>4} {e / n:>6.1%} {b / n:>6.1%}")

    both_fail = [k for k, (c, ec, bc) in per.items() if c >= 5 and ec / c < 0.5 and bc / c < 0.5]
    print(f"\n  classes BOTH methods fail (n>=5): {', '.join(sorted(both_fail)) or 'none'}")
    print("\n  If the same classes fail under both a lexical and a semantic method, the limit is")
    print("  the LABELS, not the method — and relabelling buys more than any model change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
