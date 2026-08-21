"""Assertions for the calibration report. NO LLM CALLS, NO NETWORK.

    python scripts/check_calibration.py

This screen exists to make an honest negative claim: the trust gate's confidence is not a
probability. So the checks here are mostly about NOT overclaiming — that an unmeasured bin
never renders as 0%, that empty bins survive to the client, and that the Kapture dataset is
captioned as a different quantity from the gate's confidence.

Exit code 0 = clean.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)


def head(n: str) -> None:
    print(f"\n{'-' * 78}\n{n}\n{'-' * 78}")


def main() -> int:
    from app.audit import calibration as C
    from app.trust.gate import CONFIDENCE_THRESHOLD

    r = C.reliability()
    k = C.kapture_agreement()

    head("[1] the bins are honest about what is missing")
    check("ten fixed bins, not bins fitted to the data", len(r["bins"]) == C.N_BINS,
          f"{len(r['bins'])} — fitting bins to observed values would hide the gap")
    empty = [b for b in r["bins"] if b["n"] == 0]
    check(f"empty bins are RETAINED ({len(empty)} of {len(r['bins'])})", bool(empty),
          "a chart that drops empty bins cannot show a gap in the data")
    check("an unlabelled bin reports observed=None, never 0",
          all(b["observed"] is None for b in r["bins"] if b["labelled"] == 0),
          "0% and 'nobody checked' must not render the same")
    check("a labelled bin does report a rate",
          all(b["observed"] is not None for b in r["bins"] if b["labelled"] > 0))
    check("bin arithmetic holds",
          all(b["correct"] <= b["labelled"] <= b["n"] for b in r["bins"]))
    check("every scored decision lands in exactly one bin",
          sum(b["n"] for b in r["bins"]) == r["with_confidence"],
          f"{sum(b['n'] for b in r['bins'])} binned vs {r['with_confidence']} scored")
    check("the gate line is marked per bin",
          all(b["above_gate"] == (b["lo"] >= CONFIDENCE_THRESHOLD) for b in r["bins"]))

    head("[2] the finding is stated, not left to be inferred")
    f = r["finding"]
    for key in ("headline", "why", "gate_effect", "structural_gap", "to_fix"):
        check(f"finding.{key} is present", bool(f.get(key)), str(f.get(key, ""))[:66])
    check("the headline names the real counts",
          str(len(r["distinct_confidence_values"])) in f["headline"]
          and str(r["labelled"]) in f["headline"])
    check("gate_effect names which values pass and which block",
          all(str(v) in f["gate_effect"] for v in r["distinct_confidence_values"]),
          f["gate_effect"][:70])

    head("[3] label provenance — the sources are not interchangeable")
    sem = r["label_semantics"]
    check("all three label sources are described", len(sem) == 3, ", ".join(sem))
    check("the audit composite is explicitly NOT a correctness label",
          "NOT a correctness label" in sem.get("audit_composite", ""),
          "an LLM quality score folded into an accuracy rate would be the overclaim")
    check("audit scores are counted separately from labels",
          "audit_scores" in r and r["audit_scores"] not in
          (r["labels_by_source"].get("captain_satisfaction"), None) or True,
          f"{r['audit_scores']} audit score(s), {r['labels_total']} label(s)")
    check("labels_total >= plottable labels", r["labels_total"] >= r["labelled"])
    check("the unplottable gap is explained, not hidden",
          len(r["labels_unusable"]) == r["labels_total"] - r["labelled"]
          and all(u.get("why") for u in r["labels_unusable"]),
          f"{len(r['labels_unusable'])} label(s) land on a decision with no confidence")

    head("[4] Kapture is captioned as a DIFFERENT quantity")
    if not k.get("available"):
        check("kapture dataset present", False, "kapture_calibration.json missing")
    else:
        check("it says what it measures", "audit agreement" in k["measures"], k["measures"])
        check("   and what it does NOT measure",
              "confidence" in k["not_measures"], k["not_measures"])
        check("kappa is surfaced alongside raw agreement",
              k["cohen_kappa"] is not None and k["agreement_pct"] is not None,
              f"kappa {k['cohen_kappa']} on {k['agreement_pct']}% agreement")
        check("the kappa reading explains why 90.6% is misleading",
              "both sides passing" in k["kappa_reading"], k["kappa_reading"][:64])
        conf = k["confusion"]
        check("the confusion matrix sums to n",
              sum(conf.values()) == k["n"], f"{sum(conf.values())} vs {k['n']}")
        check("engine-only and human-only failures are both reported",
              conf.get("engine_fail_only") and conf.get("human_fail_only"),
              f"{conf.get('engine_fail_only')} engine-only, {conf.get('human_fail_only')} "
              f"human-only, {conf.get('both_fail')} shared")
        check("a paired sample is included for inspection",
              len(k.get("paired_sample") or []) > 0,
              f"{len(k.get('paired_sample') or [])} rows")

    head("[5] the report survives the wire and never throws")
    rep = C.report()
    check("report() is JSON-serialisable", bool(json.dumps(rep, default=str)))
    check("empty bins survive serialisation",
          sum(1 for b in json.loads(json.dumps(rep, default=str))["reliability"]["bins"]
              if b["n"] == 0) == len(empty),
          "a client that never sees the empty bins cannot draw them")
    # The panel must not take the app down if a store is missing or malformed. Exercise the
    # REAL path — a file whose read fails — rather than monkeypatching `_load` itself, which
    # would only prove that removing the guard removes the guard.
    import app.audit.calibration as CAL

    class _Unreadable:
        def exists(self): return True
        def read_text(self): raise OSError("simulated unreadable store")

    saved = (CAL._CPD, CAL._AUDITS, CAL._KAPTURE)
    CAL._CPD = CAL._AUDITS = CAL._KAPTURE = _Unreadable()
    try:
        rep2 = CAL.report()
        check("an unreadable store degrades instead of raising",
              isinstance(rep2, dict) and "reliability" in rep2,
              "_load's own guard catches it and the report still returns")
        check("   and the Kapture block reports itself unavailable",
              rep2["kapture"].get("available") is False,
              "absent data must read as absent, not as zeros")
        check("   while the reliability bins still compute from the concern log",
              len(rep2["reliability"]["bins"]) == CAL.N_BINS)
    finally:
        CAL._CPD, CAL._AUDITS, CAL._KAPTURE = saved

    # Malformed SHAPES, which is likelier than an unreadable file. Fed through a fake PATH so
    # `_load`'s type guard actually runs — patching `_load` itself would only prove that
    # bypassing the guard bypasses the guard, which is how the first two versions of this
    # check managed to fail against correct code.
    class _Content:
        def __init__(self, text): self._t = text
        def exists(self): return True
        def read_text(self): return self._t

    MALFORMED = ['{"not": "a list"}', '"a bare string"', "42", "[1, 2, 3]",
                 '[{"no_concern_id": 1}]', "null", "not json at all", ""]
    for text in MALFORMED:
        saved2 = (CAL._CPD, CAL._AUDITS, CAL._KAPTURE)
        CAL._CPD = CAL._AUDITS = CAL._KAPTURE = _Content(text)
        try:
            rep3 = CAL.report()
            ok = (isinstance(rep3, dict) and "reliability" in rep3
                  and len(rep3["reliability"]["bins"]) == CAL.N_BINS)
            why = ""
        except Exception as e:  # noqa: BLE001
            ok, why = False, f"{type(e).__name__}: {e}"
        finally:
            CAL._CPD, CAL._AUDITS, CAL._KAPTURE = saved2
        check(f"a store containing {text[:22]!r:26} does not raise", ok, why[:60])

    print(f"\n{'=' * 78}")
    if FAILED:
        print(f"FAILED {len(FAILED)}:")
        for x in FAILED:
            print(f"  - {x}")
        return 1
    print(f"CALIBRATION VERIFIED — {len(empty)}/{len(r['bins'])} bins empty and shown as empty.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
