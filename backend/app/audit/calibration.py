"""Calibration — is the gate's confidence a probability, or a label?

WHAT THIS IS FOR
`trust/gate.py` blocks any decision below `CONFIDENCE_THRESHOLD = 0.80`. The comment above that
constant says it is "recalibrated continuously so a 0.9-confident decision is right ~90% of the
time". That is the intent. This module measures whether it is true.

It is not. And the honest thing is to build the instrument, run it, and show what it says —
because a threshold that reads like a probability but is not one is the kind of claim that
survives right up until someone asks how it was validated.

THE FINDING, MEASURED
Every confidence value in the system is a HARDCODED CONSTANT in `policy_exec.py`. There are
four of them in the log: 0.2, 0.4, 0.9, 0.92. They do not move with the evidence — they are
labels for which branch was taken, wearing the notation of a probability. Against a threshold
of 0.80 the gate is therefore a two-way switch on four constants: 0.2/0.4 block, 0.9/0.92 pass.

And there is almost nothing to calibrate AGAINST. A reliability curve needs an outcome label
per decision, and the platform collects three kinds, all sparse:

    captain satisfaction (cpd_log)   the captain's own verdict — the best signal there is
    L3 closure (concern_log)         a human closed the escalation, so escalating was warranted
    audit composite (audits.json)    an LLM judge's QUALITY score — NOT a correctness label,
                                     reported separately and never folded into accuracy

So this returns bins with `n` and `labelled` side by side, and renders empty bins as empty. The
gap is the result.

WHAT THE KAPTURE BLOCK IS, AND IS NOT
`kapture_calibration.json` holds n=1,089 real engine-verdict vs human-verdict pairs — the only
place in this repo where a machine score and a human ground truth sit on the same row. It is
included because it is real, and it is labelled precisely: it calibrates AUDIT AGREEMENT on
ticket quality, not the confidence this gate reads. Presenting one as the other would be the
overclaim this module exists to avoid. Its Cohen's kappa of 0.052 is worth saying out loud —
90.6% agreement driven almost entirely by both sides passing nearly everything.
"""
from __future__ import annotations

import json

from ..durable_state import durable_path
from ..ledger import concern_log
from ..trust.gate import CONFIDENCE_THRESHOLD

# 0.1-wide bins over [0, 1]. Fixed rather than derived from the data, so an empty bin is
# VISIBLE — deriving bins from observed values would silently hide the fact that only four
# values exist by drawing exactly four bars.
BIN_WIDTH = 0.1
N_BINS = 10

_CPD = durable_path("cpd_log.json")
_AUDITS = durable_path("audits.json")
_KAPTURE = durable_path("kapture_calibration.json")


def _load(path, default):
    """Read a durable store, or return `default`.

    `default` also declares the EXPECTED TYPE, and a parsed value of a different type is
    discarded. That is not defensiveness for its own sake: a store that is a list where a dict
    was expected makes every downstream `.get()` an AttributeError, and a store that parses to
    an int makes every `for` loop a TypeError. One guard here is worth an isinstance check at
    every use site — and it is the difference between a panel that reports "no data" and a
    panel that takes the request down.
    """
    try:
        if path.exists():
            parsed = json.loads(path.read_text())
            if isinstance(parsed, type(default)):
                return parsed
    except Exception:  # noqa: BLE001 — a panel must never take the app down
        pass
    return default


def _labels() -> dict[str, dict]:
    """concern_id → {correct: bool|None, source: str, detail: str}.

    Each source is explicit about WHAT it can attest, because they are not interchangeable:
    a satisfied captain is evidence the answer was right; an L3 closure is evidence the
    ESCALATION was right, which is a different claim; an audit composite is a quality opinion
    and attests neither.
    """
    out: dict[str, dict] = {}

    # 1) The captain's own verdict. The strongest label available.
    for row in _load(_CPD, []) or []:
        if not isinstance(row, dict) or not row.get("concern_id"):
            continue
        out[row["concern_id"]] = {
            "correct": bool(row.get("satisfied")),
            "source": "captain_satisfaction",
            "detail": (row.get("note") or "").strip()[:120]
                      or ("satisfied" if row.get("satisfied") else "not satisfied"),
        }

    # 2) An L3 closure means a human worked the escalation to a resolution — so escalating was
    #    the right call. It says nothing about whether a RESOLUTION would have been right, so it
    #    is only a label for concerns that actually escalated.
    all_concerns = concern_log.all_concerns()
    escalated = {c["id"] for c in all_concerns
                 if c.get("id") and c.get("action_taken") == "escalate"}
    for c in all_concerns:
        target = c.get("resolves_concern_id")
        if target and target in escalated and target not in out:
            out[target] = {"correct": True, "source": "l3_closure",
                           "detail": (c.get("resolution_note") or "closed by L3")[:120]}
    return out


def _audit_scores() -> dict[str, int]:
    """concern_id → composite (0-100). A QUALITY opinion from an LLM judge, kept separate
    from the correctness labels above and never mixed into an accuracy rate."""
    out = {}
    for a in _load(_AUDITS, []) or []:
        if isinstance(a, dict) and a.get("concern_id") and a.get("composite") is not None:
            out[a["concern_id"]] = int(a["composite"])
    return out


def reliability() -> dict:
    """Confidence bins with n, labelled-n, and observed accuracy where labels exist."""
    labels = _labels()
    audits = _audit_scores()

    # L3 follow-ups shadow an original concern; counting them would double-count.
    concerns = [c for c in concern_log.all_concerns() if not c.get("resolves_concern_id")]
    scored = [c for c in concerns if isinstance(c.get("confidence"), (int, float))]

    bins = []
    for i in range(N_BINS):
        lo, hi = i * BIN_WIDTH, (i + 1) * BIN_WIDTH
        # The top bin is inclusive of 1.0; every other is [lo, hi).
        members = [c for c in scored
                   if lo <= float(c["confidence"]) < hi
                   or (i == N_BINS - 1 and float(c["confidence"]) == 1.0)]
        lab = [labels[c["id"]] for c in members if c.get("id") in labels]
        correct = sum(1 for l in lab if l["correct"])
        bins.append({
            "lo": round(lo, 2), "hi": round(hi, 2),
            "label": f"{lo:.1f}–{hi:.1f}",
            "n": len(members),
            "labelled": len(lab),
            "correct": correct,
            # None, not 0 — an unmeasured bin and a bin that got everything wrong must not
            # render as the same bar.
            "observed": round(correct / len(lab), 3) if lab else None,
            "mean_confidence": round(sum(float(c["confidence"]) for c in members) / len(members), 3)
                               if members else None,
            "above_gate": lo >= CONFIDENCE_THRESHOLD,
        })

    distinct = sorted({float(c["confidence"]) for c in scored})
    by_source: dict[str, int] = {}
    for l in labels.values():
        by_source[l["source"]] = by_source.get(l["source"], 0) + 1

    # A label can exist and still be UNPLOTTABLE, and the gap between these two counts would
    # otherwise read as an arithmetic error. It is a finding in its own right: `_escalate_case`
    # hardcodes `confidence: None`, so an escalated concern carries no confidence — and escalated
    # concerns are exactly the ones most likely to receive a human label, since an L3 closure only
    # happens after an escalation. The decisions we can label are the decisions we cannot plot.
    scored_ids = {c.get("id") for c in scored}
    unusable = [{"concern_id": cid, "source": l["source"],
                 "why": "the concern carries no numeric confidence, so it lands in no bin"}
                for cid, l in labels.items() if cid not in scored_ids]

    return {
        "threshold": CONFIDENCE_THRESHOLD,
        "concerns": len(concerns),
        "with_confidence": len(scored),
        "labelled": sum(b["labelled"] for b in bins),
        "distinct_confidence_values": distinct,
        "bins": bins,
        "labels_by_source": by_source,
        "labels_total": len(labels),
        "labels_unusable": unusable,
        "audit_scores": len(audits),
        # The headline. Stated as data rather than left for a reader to notice.
        "finding": _finding(distinct, len(scored), sum(b["labelled"] for b in bins)),
        "label_semantics": {
            "captain_satisfaction": "the captain's own verdict on the answer — the strongest label",
            "l3_closure": "a human closed the escalation, so escalating was warranted; says "
                          "nothing about whether resolving would have been right",
            "audit_composite": "an LLM judge's quality score, NOT a correctness label — counted "
                               "separately and never folded into accuracy",
        },
    }


def _finding(distinct: list[float], scored: int, labelled: int) -> dict:
    above = [v for v in distinct if v >= CONFIDENCE_THRESHOLD]
    below = [v for v in distinct if v < CONFIDENCE_THRESHOLD]
    return {
        "headline": (f"{len(distinct)} distinct confidence values across {scored} decisions, "
                     f"and {labelled} outcome label(s). This is not enough to calibrate."),
        "why": ("Every confidence is a hardcoded constant in engine/policy_exec.py, one per "
                "decision branch. The values do not move with the evidence — they identify "
                "which branch was taken, in the notation of a probability."),
        "gate_effect": (f"Against a threshold of {CONFIDENCE_THRESHOLD}, the gate is a two-way "
                        f"switch: {', '.join(str(v) for v in below)} block and "
                        f"{', '.join(str(v) for v in above)} pass."),
        "structural_gap": ("Escalations are the decisions most likely to get a human label — an "
                           "L3 closure only follows an escalation — but `_escalate_case` records "
                           "`confidence: None`, so those decisions carry no value to calibrate. "
                           "The ones we can label are the ones we cannot plot."),
        "to_fix": ("Collect an outcome label per decision. The captain-satisfaction prompt "
                   "already exists and is the right instrument; it has been answered once. "
                   "Until labels accumulate, treat the threshold as a policy switch — which is "
                   "what it is — and not as a probability."),
    }


def kapture_agreement() -> dict:
    """The one real paired dataset — labelled as what it is, not as what would be convenient.

    n=1,089 engine-vs-human verdicts on ticket QUALITY. A different quantity from the gate's
    decision confidence, on a different population (Kapture email tickets, not concerns), with
    no join between them. Included because it is real; captioned so it cannot be misread.
    """
    k = _load(_KAPTURE, {})
    if not isinstance(k, dict) or not k:
        return {"available": False}
    conf = k.get("confusion") if isinstance(k.get("confusion"), dict) else {}
    sample = k.get("disagreements_sample") if isinstance(
        k.get("disagreements_sample"), list) else []
    return {
        "available": True,
        "measures": "audit agreement on ticket quality",
        "not_measures": "the decision confidence trust/gate.py reads",
        "n": k.get("n"),
        "model": k.get("model"),
        "agreement_pct": k.get("status_agreement"),
        "cohen_kappa": k.get("cohen_kappa"),
        "engine_fail_rate": k.get("engine_fail_rate"),
        "human_fail_rate": k.get("human_fail_rate"),
        "confusion": conf,
        # The number that stops 90.6% being read as "the engine is 90% right".
        "kappa_reading": (
            f"Cohen's kappa {k.get('cohen_kappa')} on {k.get('status_agreement')}% raw "
            f"agreement means the agreement is almost entirely explained by both sides passing "
            f"nearly everything ({conf.get('both_pass')} of {k.get('n')}). The engine and the "
            f"human each fail about 5% of tickets and they largely fail DIFFERENT ones "
            f"({conf.get('engine_fail_only')} engine-only, {conf.get('human_fail_only')} "
            f"human-only, {conf.get('both_fail')} shared). On the cases that matter they do not "
            f"agree."),
        "paired_sample": [
            {"ticket": s.get("ticket_id"), "engine": s.get("engine"), "human": s.get("human"),
             "engine_quality_pct": s.get("engine_quality_pct")}
            for s in sample[:25] if isinstance(s, dict)
        ],
        "per_parameter": (k.get("top_disagreements") or [])[:6],
    }


def report() -> dict:
    """Everything the panel needs, in one call."""
    return {"reliability": reliability(), "kapture": kapture_agreement()}
