"""Which lever actually moves disposition accuracy — measured, on a dev/test split.

    python scripts/score_levers.py

`score_classifier.py` reports where the classifier stands. This answers the next question: given
that it stands there, what is worth doing about it? Re-run it after any taxonomy change.

── WHY THIS SPLITS THE HELD-OUT SET AGAIN ────────────────────────────────────────────────────
Choosing a threshold against a set and then quoting that set's score is how a number becomes
fiction. The held-out rows are split in half by content hash: every choice below is made on DEV,
and only the final configuration is measured on TEST, which nothing tuned against.

── WHAT THE MEASUREMENTS SAID (2026-09-17, 1,259 exemplars, silver labels) ────────────────────
1. The refusal threshold is a BAD lever here. Pushing min_margin 0.15 -> 0.60 cost 51 points of
   coverage to buy 13 points of precision. That is the signature of errors that are confident
   rather than borderline — the classifier is not hesitating between the money classes, it is
   picking one firmly and being wrong, because the partner's text does not contain the thing
   that separates them.

2. Merging the five money classes is a GREAT lever: +17.9 points of precision on the test set at
   ZERO coverage cost. Free, and larger than every other lever combined.

3. Gating the two untrusted classes is MARGINAL: +1.1 precision for -4.1 coverage. Measured, and
   not recommended — it is here so the decision is on record rather than re-litigated.

4. More exemplars help slowly but really: +3.5 points per 10x data, and the curve is still
   rising at 1,259. The Kapture export is worth doing; it is not the big lever.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.intake import classify  # noqa: E402

CORPUS = ROOT / "data" / "intake" / "corpus" / "dispositions.json"
HOLDOUT = ROOT / "data" / "intake" / "corpus" / "dispositions_holdout.json"

#: Five dispositions that describe the same partner sentence — "paisa nahi aaya". Which one is
#: right depends on WHY the money did not arrive, which is usually not in the message.
MONEY = {"payment_not_received", "hardstop_loss", "cod_pendency", "cod_shortfall",
         "shortage_loss"}

#: Chosen on DEV: precision 20% (n=5) and 33% (n=3) after the money merge. Refusing is better
#: than asserting at those rates — but see the header; the coverage cost is not worth it.
GATE = {"consumables_order", "capacity_panel_issue"}


def collapse(d: str) -> str:
    return "MONEY" if d in MONEY else d


def evaluate(matcher, rows, *, merge=False, gate=False) -> tuple[float, float, int]:
    """Returns (precision_on_answered, coverage, n_answered)."""
    real = [r for r in rows if r["disposition"] != "NOVEL"]
    answered = correct = 0
    for r in real:
        p = matcher.match(r["text"])["disposition"]
        if gate and p in GATE:
            p = "NOVEL"                     # a class we do not trust: refuse, do not assert
        if p == "NOVEL":
            continue
        answered += 1
        t, q = (collapse(r["disposition"]), collapse(p)) if merge else (r["disposition"], p)
        correct += (t == q)
    return (correct / answered if answered else 0.0,
            answered / len(real) if real else 0.0, answered)


def main() -> int:
    if not HOLDOUT.exists():
        raise SystemExit("no holdout — run scripts/build_corpus_from_audits.py --holdout")
    m = classify.load_matcher()
    if m is None:
        raise SystemExit("no exemplars — run scripts/build_exemplars.py first")
    cfg = classify.load_config()
    exemplars = json.loads(
        (ROOT / "data" / "intake" / "corpus" / "exemplars.json").read_text())["exemplars"]

    held = json.loads(HOLDOUT.read_text(encoding="utf-8"))["messages"]
    # Deterministic, content-hash split. No seed to forget, same halves every run.
    dev = [r for r in held if int(r["id"][:2], 16) % 2 == 0]
    test = [r for r in held if int(r["id"][:2], 16) % 2 == 1]

    print(f"\n{'=' * 78}\nWHICH LEVER MOVES DISPOSITION ACCURACY")
    print("SILVER LABELS: agreement with the existing classifier, not correctness")
    print("=" * 78)
    print(f"\n  exemplars {len(exemplars)}   dev {len(dev)}   test {len(test)}"
          f"   (split by content hash)")

    print(f"\n  ── tuning, on DEV only ──\n")
    print(f"  {'lever':<44} {'precision':>10} {'coverage':>10}")
    print("  " + "-" * 66)
    for label, margin, merge in [
            ("baseline (min_margin 0.15)", 0.15, False),
            ("refuse more: margin 0.30", 0.30, False),
            ("refuse more: margin 0.45", 0.45, False),
            ("refuse more: margin 0.60", 0.60, False),
            ("merge the 5 money classes", 0.15, True),
            ("merge money + margin 0.30", 0.30, True)]:
        mm = classify.Matcher(exemplars, {**cfg, "min_margin": margin})
        p, c, _ = evaluate(mm, dev, merge=merge)
        print(f"  {label:<44} {p:>9.1%} {c:>10.1%}")
    print("\n  Raising the refusal threshold buys little and costs a lot: the errors are")
    print("  CONFIDENT, not borderline. Merging the money classes is free.")

    # ── learning curve: is more data worth acquiring? ────────────────────────────────────────
    print(f"\n  ── does more data help? (money merged, averaged over 5 subsamples) ──\n")
    print(f"  {'exemplars':>10} {'precision':>11} {'coverage':>11}")
    rng = random.Random(7)
    curve = []
    for frac in (0.1, 0.25, 0.5, 0.75, 1.0):
        ps, cs = [], []
        for _ in range(5):
            sub = rng.sample(exemplars, max(2, int(len(exemplars) * frac)))
            p, c, _ = evaluate(classify.Matcher(sub, cfg), dev, merge=True)
            ps.append(p)
            cs.append(c)
        n = int(len(exemplars) * frac)
        curve.append((n, sum(ps) / len(ps)))
        print(f"  {n:>10} {sum(ps)/len(ps):>10.1%} {sum(cs)/len(cs):>10.1%}")
    gain = (curve[-1][1] - curve[0][1]) * 100
    print(f"\n  +{gain:.1f} points across a 10x increase, still rising at the right edge.")
    print("  Worth acquiring; not the big lever.")

    # ── the final configuration, on TEST ─────────────────────────────────────────────────────
    print(f"\n  ── the chosen configuration, on TEST (untouched by everything above) ──\n")
    print(f"  {'configuration':<44} {'precision':>10} {'coverage':>10}")
    print("  " + "-" * 66)
    base = None
    for label, merge, gate in [("as it ships today", False, False),
                               ("+ money classes merged", True, False),
                               ("+ money merged + gate untrusted classes", True, True)]:
        p, c, _ = evaluate(m, test, merge=merge, gate=gate)
        if base is None:
            base = (p, c)
        print(f"  {label:<44} {p:>9.1%} {c:>10.1%}")
    p1, c1, _ = evaluate(m, test, merge=True)
    print(f"\n  RECOMMENDED: merge the money classes and stop there —")
    print(f"  precision {base[0]:.1%} -> {p1:.1%} ({(p1 - base[0]) * 100:+.1f} points), "
          f"coverage {base[1]:.1%} -> {c1:.1%} ({(c1 - base[1]) * 100:+.1f} points).")
    print("  Gating adds ~1 point of precision for ~4 of coverage: measured, not recommended.")
    print("\n  This is a taxonomy decision, not a model. No training, no GPU, no API calls.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
