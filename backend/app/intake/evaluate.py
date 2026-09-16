"""Scoring against golden labels. NO LLM CALLS, NO NETWORK — it reads what other stages wrote.

── THE NUMBER THAT COMES FIRST ───────────────────────────────────────────────────────────────
Grouping accuracy with ENTITY JOIN ONLY and stage 6 DISABLED. If deterministic rules alone
group well enough, stage 6 — every LLM call, the cache, the budget guard, the adjudication
prompts — becomes optional and this project gets substantially smaller. It is printed first
because it is a decision, not a statistic, and it costs nothing to compute.

── PER CHANNEL, NEVER BLENDED ────────────────────────────────────────────────────────────────
A blended number hides exactly the gap that reshaped this project: #valmo-firefighters carries
an identifier on ~56% of top-level messages and pilot_support_ams on ~11%, so one average over
both says nothing true about either. Every grouping figure here is reported per channel, and
the blended figure is shown only after them, labelled as such.

── HOW ONE-TO-ONE ACCURACY IS COMPUTED ───────────────────────────────────────────────────────
Predicted clusters are matched to gold clusters greedily by descending overlap, each cluster
used at most once; accuracy is the matched message count over the labelled total. Greedy rather
than optimal (Hungarian) assignment keeps it dependency-free and deterministic; on clusters
this size the two agree, and the metric is reported alongside the raw cluster counts so a
degenerate result — everything in one cluster, or every message its own — is visible rather
than hidden inside a single percentage.
"""
from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from . import extract as istage
from . import group, labels, store

GOLDEN = Path(__file__).resolve().parents[2] / "data" / "intake" / "golden" / "labels.csv"


def load_golden(path: Path = GOLDEN) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r.get("message_id")]


def one_to_one(pred: dict[str, str], gold: dict[str, str]) -> dict:
    """Greedy one-to-one cluster matching. `pred`/`gold` map message key -> cluster id."""
    keys = [k for k in gold if k in pred]
    if not keys:
        return {"n": 0, "matched": 0, "accuracy": None,
                "pred_clusters": 0, "gold_clusters": 0}

    overlap: dict[tuple[str, str], int] = {}
    for k in keys:
        overlap[(pred[k], gold[k])] = overlap.get((pred[k], gold[k]), 0) + 1

    used_p, used_g, matched = set(), set(), 0
    # sort by overlap desc, then by ids, so the result never depends on dict ordering
    for (p, g), n in sorted(overlap.items(), key=lambda kv: (-kv[1], kv[0])):
        if p in used_p or g in used_g:
            continue
        used_p.add(p)
        used_g.add(g)
        matched += n

    return {"n": len(keys), "matched": matched, "accuracy": round(matched / len(keys), 4),
            "pred_clusters": len({pred[k] for k in keys}),
            "gold_clusters": len({gold[k] for k in keys})}


def _grouping(con: sqlite3.Connection, run_id: str, golden: list[dict]) -> dict:
    gold = {(r["channel_id"], r["message_id"]): r["issue_id"] for r in golden}
    pred = {(r["channel_id"], r["message_id"]): r["issue_id"]
            for r in con.execute(
                "SELECT channel_id, message_id, issue_id FROM assignments "
                "WHERE run_id = ? AND issue_id IS NOT NULL", (run_id,))}

    channels = sorted({k[0] for k in gold})
    names = {r[0]: r[1] for r in con.execute(
        "SELECT DISTINCT channel_id, channel_name FROM messages")}
    per = {}
    for ch in channels:
        p = {k: v for k, v in pred.items() if k[0] == ch}
        g = {k: v for k, v in gold.items() if k[0] == ch}
        per[names.get(ch, ch)] = one_to_one(p, g)
    return {"per_channel": per, "blended_do_not_quote_alone": one_to_one(pred, gold)}


def _dc_extraction(con: sqlite3.Connection, run_id: str) -> dict:
    """Precision and recall for DC codes, Tier A and Tier B SEPARATELY, with the false
    positives listed. A blended DC number would hide that Tier A is the precise half."""
    fixtures = Path(__file__).resolve().parents[2] / "data" / "intake" / "fixtures"
    import json
    exp_path = fixtures / "expected_tier_a.json"
    if not exp_path.exists():
        return {"available": False}
    exp = json.loads(exp_path.read_text())["by_message"]

    out = {}
    for tier in ("A", "B"):
        tp = fp = fn = 0
        fps, fns = [], []
        for mid, want in exp.items():
            want = set(want)
            got = {r[0] for r in con.execute(
                "SELECT value FROM entities WHERE run_id=? AND message_id=? AND kind='dc_code' "
                "AND tier=?", (run_id, mid, tier))}
            # Tier A owns the expectation; Tier B must add nothing on this corpus.
            expected = want if tier == "A" else set()
            tp += len(got & expected)
            for v in sorted(got - expected):
                fp += 1
                fps.append({"message_id": mid, "value": v})
            for v in sorted(expected - got):
                fn += 1
                fns.append({"message_id": mid, "value": v})
        out[f"tier_{tier}"] = {
            "true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
            "recall": round(tp / (tp + fn), 4) if (tp + fn) else None,
            "false_positive_list": fps, "false_negative_list": fns,
        }

    registry = istage.load_code_list(istage.DC_CODES)
    observed = {v for v, in con.execute(
        "SELECT DISTINCT value FROM entities WHERE run_id=? AND kind='dc_code'", (run_id,))}
    out["registry"] = {
        "size": len(registry),
        "codes_seen_in_channels": len(observed),
        "seen_and_in_registry": len(observed & registry),
        "seen_but_MISSING_from_registry": sorted(observed - registry),
        "coverage": round(len(observed & registry) / len(observed), 4) if observed else None,
        "_why": "Reported separately so partial registry coverage is never mistaken for poor "
                "regex recall. Tier B can only find what the registry contains.",
    }
    return out


def _intent(con: sqlite3.Connection, run_id: str, golden: list[dict]) -> dict:
    gold = {(r["channel_id"], r["message_id"]): r["intent"]
            for r in golden if r.get("intent")}
    pred = {}
    for r in con.execute(
            "SELECT channel_id, message_id, result_json FROM adjudications "
            "WHERE run_id=? AND task='intent'", (run_id,)):
        import json
        try:
            pred[(r["channel_id"], r["message_id"])] = json.loads(r["result_json"]).get("intent")
        except Exception:                                                    # noqa: BLE001
            pass
    if not pred:
        return {"available": False,
                "_why": "stage 6 has not run — intent is an adjudicated field, so there is "
                        "nothing to score. This is expected on a zero-cost run.",
                "labelled_available": len(gold)}
    keys = [k for k in gold if k in pred]
    correct = sum(1 for k in keys if pred[k] == gold[k])
    matrix: dict[str, dict[str, int]] = {}
    for k in keys:
        matrix.setdefault(gold[k], {}).setdefault(pred[k], 0)
        matrix[gold[k]][pred[k]] += 1
    return {"available": True, "n": len(keys), "correct": correct,
            "accuracy": round(correct / len(keys), 4) if keys else None,
            "unmapped_predicted": sum(1 for k in keys if pred[k] == "UNMAPPED"),
            "confusion": matrix}


def _cost(con: sqlite3.Connection, run_id: str) -> dict:
    n = con.execute("SELECT COUNT(*) FROM adjudications WHERE run_id=?", (run_id,)).fetchone()[0]
    spend = con.execute(
        "SELECT COALESCE(SUM(cost_usd),0), COALESCE(SUM(llm_calls),0) FROM stage_runs "
        "WHERE run_id=?", (run_id,)).fetchone()
    cached = con.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    return {"messages_that_reached_claude": n, "llm_calls": spend[1],
            "estimated_cost_usd": round(spend[0], 4), "cache_entries": cached}


def _funnel(con: sqlite3.Connection, run_id: str) -> dict:
    """Where volume actually goes, tier by tier, and what escapes each one.

    ESCAPE RATE here means: of the items that ENTERED a tier, the share that tier did not
    resolve, and which therefore fell through to the next one. It is the number that decides
    whether an expensive tier is worth building — a tier only has to handle what escapes the
    cheap tiers above it, and every tier above stage 6 costs nothing to run.

    Read it top-down. The last row is the only one that costs a person's time.
    """
    q = lambda s, *a: con.execute(s, a).fetchone()[0]  # noqa: E731

    msgs = q("SELECT COUNT(*) FROM messages")
    gated = q("SELECT COUNT(*) FROM message_flags WHERE run_id=? AND gated=1", run_id)
    after_gate = msgs - gated

    # The evidence gate only ever sees what the noise gate let through.
    ev = {r[0]: r[1] for r in con.execute(
        "SELECT decision, COUNT(*) FROM evidence WHERE run_id=? GROUP BY decision", (run_id,))}
    not_issue = ev.get("not_an_issue", 0)
    held = ev.get("weak", 0) + ev.get("orphan", 0)
    after_evidence = after_gate - not_issue - held

    issues = q("SELECT COUNT(*) FROM issues WHERE run_id=?", run_id)
    novel = q("SELECT COUNT(*) FROM issues WHERE run_id=? AND intent='NOVEL'", run_id)
    unclassified = q("SELECT COUNT(*) FROM issues WHERE run_id=? AND intent IS NULL", run_id)
    answered = issues - novel - unclassified

    # How many of the NOVEL ones a person has already answered. The queue is keyed on the text,
    # so this survives a re-run producing new issue ids.
    confirmed_ids = set(labels.load())
    still_asking = 0
    for r in con.execute(
            "SELECT m.text FROM issues i JOIN messages m "
            "ON m.channel_id=i.anchor_channel_id AND m.message_id=i.anchor_message_id "
            "WHERE i.run_id=? AND i.intent='NOVEL'", (run_id,)):
        still_asking += labels.text_id(r[0] or "") not in confirmed_ids

    pct = lambda n, d: None if not d else round(n / d, 4)  # noqa: E731
    return {
        "_what": "Escape rate = share of what ENTERED a tier that the tier did not resolve. "
                 "Every tier below costs $0 to run; only the last row costs a person's time.",
        "tiers": [
            {"tier": "0 noise gate (structural)", "in": msgs, "resolved": gated,
             "escaped": after_gate, "escape_rate": pct(after_gate, msgs), "cost_usd": 0.0},
            {"tier": "0b evidence (is this an issue at all)", "in": after_gate,
             "resolved": not_issue, "held_for_review": held, "escaped": after_evidence,
             "escape_rate": pct(after_evidence, after_gate), "cost_usd": 0.0},
            # Not a filter — a COLLAPSE. Nothing is discarded here; several messages become one
            # issue. Shown because otherwise the count drops between rows with no explanation
            # and it reads like silent loss.
            {"tier": "1 grouping (messages -> issues)", "in": after_evidence,
             "collapsed_into": issues, "escaped": issues,
             "escape_rate": pct(issues, after_evidence), "cost_usd": 0.0,
             "_note": "a collapse, not a filter — nothing is dropped here"},
            {"tier": "2 BM25 disposition", "in": issues, "resolved": answered,
             "escaped": novel + unclassified,
             "escape_rate": pct(novel + unclassified, issues), "cost_usd": 0.0},
            {"tier": "human (the NOVEL queue)", "in": novel, "resolved": novel - still_asking,
             "escaped": still_asking, "escape_rate": pct(still_asking, novel),
             "cost_usd": 0.0, "_note": "costs attention, not money"},
        ],
        "disposition_coverage": pct(answered, issues),
        "novel_rate": pct(novel, issues),
        "classifier_off": unclassified > 0 and answered == 0,
        "awaiting_a_human": still_asking,
        "messages_per_issue": None if not issues else round(after_evidence / issues, 2),
        "share_of_messages_that_became_an_issue": pct(issues, msgs),
    }


def run(run_id: str, *, con: sqlite3.Connection | None = None,
        golden_path: Path = GOLDEN, config_path: Path = group.CONFIG) -> dict:
    """Score a completed run. Re-groups internally to price the entity-join-only setting."""
    own = con is None
    con = con or store.connect()
    try:
        golden = load_golden(golden_path)
        if not golden:
            return {"error": f"no golden labels at {golden_path}"}

        # THE HEADLINE: re-group with stage 6 disabled and only deterministic rules, both with
        # and without the weak (place-shaped) token join, then restore the run's own grouping.
        headline = {}
        for label, weak in (("entity_join_only", False), ("entity_join_plus_dc_code", True)):
            group.run(run_id, con=con, config_path=config_path, join_weak=weak)
            headline[label] = _grouping(con, run_id, golden)
        group.run(run_id, con=con, config_path=config_path, join_weak=False)

        n_real = sum(1 for r in golden if r.get("source") not in (None, "", "synthetic"))
        n_syn = len(golden) - n_real
        caveat = None
        if n_real == 0:
            # The single most important line in this output. A grouping score against labels
            # generated from the fixtures says the pipeline agrees with the fixture author --
            # that is a test of the metric code, not evidence about the channels. Quoting it
            # as an accuracy figure would be worse than quoting nothing.
            caveat = ("SYNTHETIC ONLY — all {} golden labels come from generated fixtures and "
                      "0 from real messages. These scores verify that the metric code and the "
                      "rules agree with the fixture author. They are NOT a measurement of the "
                      "real channels and must not be quoted as accuracy. The comparison "
                      "BETWEEN settings is still meaningful; the absolute number is not. "
                      "Hand-label real messages into the same CSV with source=real."
                      ).format(n_syn)
        elif n_syn:
            caveat = (f"MIXED — {n_real} real and {n_syn} synthetic labels are scored together. "
                      f"Filter to source=real before quoting a figure externally.")

        return {
            "CAVEAT": caveat,
            "THE_NUMBER_grouping_without_stage_6": headline,
            "funnel": _funnel(con, run_id),
            "dc_extraction": _dc_extraction(con, run_id),
            "intent": _intent(con, run_id, golden),
            "cost": _cost(con, run_id),
            "golden": {"labelled_messages": len(golden),
                       "issues": len({r["issue_id"] for r in golden}),
                       "synthetic": sum(1 for r in golden if r.get("source") == "synthetic"),
                       "real": sum(1 for r in golden if r.get("source") not in
                                   (None, "", "synthetic"))},
        }
    finally:
        if own:
            con.close()
