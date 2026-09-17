"""Score the disposition matcher on held-out data. NO LLM, NO NETWORK.

    python scripts/score_classifier.py
    python scripts/score_classifier.py --sweep    # threshold sweep

── WHAT THIS NUMBER IS AND IS NOT ────────────────────────────────────────────────────────────
The corpus labels are SILVER — assigned by the engine's own classifier, not a human. So this
measures AGREEMENT WITH THAT CLASSIFIER, not correctness. It is still the right number to
optimise against for now, because agreement is measurable and truth is not until the human pass
happens. It must not be quoted as accuracy.

── NOVEL IS SCORED SEPARATELY AND ON PURPOSE ─────────────────────────────────────────────────
A matcher that answers NOVEL to everything scores 0% wrong and is useless. A matcher that never
answers NOVEL mis-routes every new issue type silently. So three numbers are reported:

    coverage  — share of messages given a disposition at all
    precision — of those, the share matching the corpus label
    NOVEL     — share refused

Precision is measured ONLY over answered messages, because that is what a downstream consumer
experiences. Coverage is what you trade away to get it.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.intake import classify  # noqa: E402

HOLDOUT = ROOT / "data" / "intake" / "corpus" / "dispositions_holdout.json"


def score(matcher: classify.Matcher, rows: list[dict]) -> dict:
    answered = correct = novel = 0
    confusion: dict[str, Counter] = defaultdict(Counter)
    misses = []
    # Rows whose true label is NOVEL are a different question: did we correctly refuse?
    real, truly_novel = [r for r in rows if r["disposition"] != "NOVEL"], \
                        [r for r in rows if r["disposition"] == "NOVEL"]

    for r in real:
        d = matcher.match(r["text"])
        got = d["disposition"]
        if got == "NOVEL":
            novel += 1
            continue
        answered += 1
        confusion[r["disposition"]][got] += 1
        if got == r["disposition"]:
            correct += 1
        elif len(misses) < 12:
            misses.append((r["disposition"], got, d["score"], d["margin"], r["text"][:70]))

    refused_novel = sum(1 for r in truly_novel
                        if matcher.match(r["text"])["disposition"] == "NOVEL")
    n = len(real) or 1
    return {
        "labelled_rows": len(real),
        "answered": answered,
        "coverage": round(answered / n, 4),
        "precision_on_answered": round(correct / answered, 4) if answered else None,
        "novel_refusals": novel,
        "novel_rate": round(novel / n, 4),
        "truly_novel_rows": len(truly_novel),
        "truly_novel_correctly_refused": refused_novel,
        "novel_recall": round(refused_novel / len(truly_novel), 4) if truly_novel else None,
        "confusion": {k: dict(v.most_common(3)) for k, v in confusion.items()},
        "misses": misses,
        "per_class": per_class(confusion),
        "collapsed": collapsed_precision(confusion),
    }


#: The five dispositions that all describe the same partner sentence — "paisa nahi aaya". Which
#: one is right usually depends on WHY the money did not arrive, and that reason is frequently
#: not in the message at all. Grouped here so the headline number can be split into "the matcher
#: is weak" and "these classes are not separable from the text", which are different problems
#: with different fixes.
MONEY = ("payment_not_received", "hardstop_loss", "cod_pendency", "cod_shortfall",
         "shortage_loss")


def per_class(confusion: dict) -> dict:
    """Precision per true class. The headline average hides that this is BIMODAL — a few classes
    are usable today and a few are near-random, and averaging them suggests a uniformly mediocre
    system that does not exist."""
    out = {}
    for true, got in confusion.items():
        n = sum(got.values())
        out[true] = {"n": n, "correct": got.get(true, 0),
                     "precision": round(got.get(true, 0) / n, 4) if n else None,
                     "mostly_confused_with": next(
                         (k for k, _ in got.most_common() if k != true), None)}
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["n"]))


def collapsed_precision(confusion: dict) -> dict:
    """What precision would be if the money classes were ONE class.

    The gap between this and the headline is the share of error that is a taxonomy problem
    rather than a matching problem — error that no classifier can fix, because the information
    needed to split those classes is not in the partner's message.
    """
    tot = hit = c_hit = inside = errs = 0
    for true, got in confusion.items():
        for pred, k in got.items():
            tot += k
            hit += k if pred == true else 0
            ct, cp = ("MONEY" if true in MONEY else true), ("MONEY" if pred in MONEY else pred)
            c_hit += k if ct == cp else 0
            if pred != true:
                errs += k
                inside += k if (true in MONEY and pred in MONEY) else 0
    return {
        "as_is": round(hit / tot, 4) if tot else None,
        "money_merged": round(c_hit / tot, 4) if tot else None,
        "errors_inside_the_money_cluster": inside,
        "errors_total": errs,
        "share_of_error_that_is_taxonomy": round(inside / errs, 4) if errs else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sweep", action="store_true", help="sweep min_score and min_margin")
    a = ap.parse_args()

    m = classify.load_matcher()
    if m is None:
        raise SystemExit("no exemplars — run scripts/build_exemplars.py first")
    rows = json.loads(HOLDOUT.read_text(encoding="utf-8"))["messages"]

    if a.sweep:
        print(f"{'min_score':>9} {'margin':>7} {'coverage':>9} {'precision':>10} "
              f"{'novel_recall':>13}")
        for ms in (1.0, 2.0, 3.0, 4.0, 6.0, 8.0):
            for mg in (0.0, 0.1, 0.15, 0.25):
                m.min_score, m.margin = ms, mg
                s = score(m, rows)
                print(f"{ms:>9.1f} {mg:>7.2f} {s['coverage']:>9.1%} "
                      f"{(s['precision_on_answered'] or 0):>10.1%} "
                      f"{(s['novel_recall'] or 0):>13.1%}")
        return 0

    s = score(m, rows)
    print(f"\n{'=' * 78}\nDISPOSITION MATCHER — held-out, {s['labelled_rows']} labelled rows")
    print("SILVER LABELS: this is agreement with the existing classifier, NOT accuracy")
    print("=" * 78)
    print(f"\n  exemplars            {len(m.docs)} across {len(m.dispositions)} dispositions")
    print(f"  coverage             {s['coverage']:.1%}  ({s['answered']} answered)")
    print(f"  precision (answered) {(s['precision_on_answered'] or 0):.1%}")
    print(f"  refused as NOVEL     {s['novel_rate']:.1%}")
    if s["truly_novel_rows"]:
        print(f"  truly-NOVEL rows     {s['truly_novel_rows']}, correctly refused "
              f"{s['truly_novel_correctly_refused']} ({(s['novel_recall'] or 0):.1%})")
    c = s["collapsed"]
    print(f"\n  ── is this a matcher problem or a taxonomy problem? ──")
    print(f"  precision, 14 dispositions        {(c['as_is'] or 0):.1%}")
    print(f"  precision, 5 money classes as 1   {(c['money_merged'] or 0):.1%}")
    print(f"  errors that never leave the money cluster: "
          f"{c['errors_inside_the_money_cluster']}/{c['errors_total']} = "
          f"{(c['share_of_error_that_is_taxonomy'] or 0):.0%}")
    print("  A symmetric confusion between two classes means the text does not separate them.")
    print("  That error is not reachable by any classifier — it is a taxonomy decision.")

    print(f"\n  ── per class (the average hides that this is bimodal) ──")
    print(f"  {'disposition':<24} {'prec':>7} {'n':>5}   mostly confused with")
    for k, v in s["per_class"].items():
        mark = "ok " if (v["precision"] or 0) >= 0.75 else "   "
        print(f"  {mark}{k:<21} {(v['precision'] or 0):>7.1%} {v['n']:>5}   "
              f"{v['mostly_confused_with'] or '—'}")

    if s["misses"]:
        print(f"\n  confusions (true -> predicted):")
        for true, got, sc, mg, txt in s["misses"][:8]:
            print(f"    {true:<22} -> {got:<22} score={sc:<6} margin={mg}")
            print(f"       {txt!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
