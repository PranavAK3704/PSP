"""Auditing Studio — the LLM judge that scores resolved concerns against the rubric.

For a given concern we assemble the FULL picture the way a human QA reviewer would
see it — the captain's issue, the engine's resolution TRACE (from the Trace Log),
and the final reply — and ask the deep-tier LLM to score each rubric dimension
0.0–1.0 with a one-line rationale. We compute a weighted composite (0–100) and
persist the result (data/audits.json), stamped with the rubric version.

Sampling by design: audit_batch audits the most recent N UN-audited concerns —
we do NOT audit everything by default.

Reuses the SAME LLM mechanism the blueprint / SOP compilers use:
  provider, model = registry.for_node("sop_compile"); provider.generate(..., json_mode=True)
"""
from __future__ import annotations

import json
import threading
from collections import defaultdict
from datetime import datetime, timezone

from ..llm import registry as llm_registry
from ..llm.gemini_provider import _parse_json
from ..ledger import concern_log, trace_log
from ..durable_state import durable_path
from . import rubric as rubric_mod

# MUTABLE store → durable state dir (survives redeploys); default backend/data.
_STORE = durable_path("audits.json")
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict]:
    if _STORE.exists():
        try:
            data = json.loads(_STORE.read_text())
            return data if isinstance(data, list) else []
        except Exception:  # noqa: BLE001
            return []
    return []


def _write(items: list[dict]) -> None:
    _STORE.write_text(json.dumps(items, indent=1))


# ── the judge prompt ─────────────────────────────────────────────────────────
_SYSTEM = """You are the QUALITY AUDITOR for Valmo's partner-support resolution engine — an
independent, demanding reviewer of how well the AI agent handled a delivery-partner ("captain")
concern. You are fair but sceptical: you reward genuinely partner-first, grounded, in-policy
resolutions and you penalise lazy escalation, guessing, re-asking for things it could derive, and
any dishonesty about what actually happened. You judge ONLY against the rubric you are given.
You never resolve the case yourself; you SCORE it. Return STRICT JSON only."""


def _trace_lines(events: list[dict]) -> str:
    """Render the stored trace events into a compact, readable transcript for the judge."""
    lines = []
    for ev in events or []:
        node = ev.get("node", "")
        label = ev.get("label", "")
        status = ev.get("status", "")
        detail = ev.get("detail", "")
        head = f"- [{node}] {label}" + (f" ({status})" if status and status != "done" else "")
        if detail:
            head += f" — {detail}"
        lines.append(head)
        data = ev.get("data") or {}
        # surface the load-bearing fields a reviewer cares about
        for k in ("entities", "checks_run", "evidence_trail", "decision_action",
                  "sop_refs", "sources", "reply"):
            if k in data and data[k] not in (None, "", [], {}):
                val = data[k]
                sval = json.dumps(val, ensure_ascii=False) if not isinstance(val, str) else val
                if len(sval) > 400:
                    sval = sval[:400] + "…"
                lines.append(f"    · {k}: {sval}")
    return "\n".join(lines) if lines else "(no trace recorded)"


def _build_prompt(concern: dict, trace: dict | None, rubric: dict) -> str:
    dims = rubric.get("dimensions", [])
    dim_block = "\n".join(
        f'  - "{d["key"]}" ({d.get("label", d["key"])}, weight {d.get("weight", 0)}): {d.get("description", "")}'
        for d in dims
    )
    events = (trace or {}).get("events", [])
    trace_txt = _trace_lines(events)
    keys_json = ", ".join(f'"{d["key"]}"' for d in dims)
    return f"""Audit this resolution against the rubric. Score EACH dimension from 0.0 (poor) to
1.0 (excellent) with ONE short rationale line each, then give an overall_rationale.

RUBRIC DIMENSIONS (score every one of these):
{dim_block}

THE CAPTAIN'S CONCERN
  captain_id:   {concern.get("captain_id", "?")}
  channel:      {concern.get("channel", "?")}
  what they said / intent: {concern.get("intent", "(none)")}
  disposition:  {concern.get("disposition", "?")}
  action_taken: {concern.get("action_taken", "?")}  |  outcome: {concern.get("outcome", "?")}
  amount_inr:   {concern.get("amount_inr", "—")}
  escalation_team: {concern.get("escalation_team", "—")}

THE ENGINE'S RESOLUTION TRACE (how it actually worked the case):
{trace_txt}

THE FINAL REPLY TO THE CAPTAIN:
{concern.get("reply") or "(no reply captured)"}

Return ONLY JSON with EXACTLY this shape:
{{
  "per_dimension": {{ {keys_json} : {{"score": <0.0-1.0>, "rationale": "<one line>"}} , ... }},
  "overall_rationale": "<2-3 sentence verdict — what was strong, what to fix>"
}}
Every rubric key MUST appear in per_dimension with a numeric score in [0,1]."""


def _composite(per_dimension: dict, rubric: dict) -> int:
    """Weighted composite 0–100, normalised by the sum of weights (weights need
    not sum to 1). Missing/invalid dimension scores are treated as 0."""
    num = den = 0.0
    for d in rubric.get("dimensions", []):
        w = float(d.get("weight", 0) or 0)
        if w <= 0:
            continue
        entry = per_dimension.get(d["key"]) or {}
        try:
            s = float(entry.get("score", 0) or 0)
        except (TypeError, ValueError):
            s = 0.0
        s = min(1.0, max(0.0, s))
        num += s * w
        den += w
    return round(100 * num / den) if den else 0


def _coerce_result(parsed: dict, rubric: dict) -> dict:
    """Coerce the LLM's JSON into the stored per_dimension shape, ensuring every
    rubric key is present with a clamped numeric score."""
    raw = (parsed or {}).get("per_dimension") or {}
    per_dimension: dict = {}
    for d in rubric.get("dimensions", []):
        entry = raw.get(d["key"])
        if not isinstance(entry, dict):   # LLM sometimes emits a bare number/string per dimension
            entry = {}
        try:
            score = float(entry.get("score", 0) or 0)
        except (TypeError, ValueError):
            score = 0.0
        per_dimension[d["key"]] = {
            "score": min(1.0, max(0.0, score)),
            "rationale": str(entry.get("rationale", "")).strip()[:400],
        }
    return per_dimension


def audit_concern(concern_id: str) -> dict:
    """Judge one concern against the current rubric, persist + return the result."""
    concern = _get_concern(concern_id)
    if not concern:
        return {"error": f"concern {concern_id} not found"}
    trace = trace_log.get(concern_id)
    rubric = rubric_mod.get_rubric()
    prompt = _build_prompt(concern, trace, rubric)

    provider, model = llm_registry.for_node("sop_compile")   # deep tier, same as compilers
    parsed, overall = {}, ""
    for attempt in range(2):   # robust to a JSON parse miss — one retry
        try:
            res = provider.generate(prompt, model=model, node="sop_compile",
                                    system=_SYSTEM, json_mode=True)
            parsed = _parse_json(res.text)
            if isinstance(parsed, list) and parsed:
                parsed = parsed[0]
            if isinstance(parsed, dict) and parsed.get("per_dimension"):
                overall = str(parsed.get("overall_rationale", "")).strip()
                break
        except Exception:  # noqa: BLE001 — retry once, then fall back to zeros
            parsed = {}
    per_dimension = _coerce_result(parsed if isinstance(parsed, dict) else {}, rubric)
    composite = _composite(per_dimension, rubric)

    result = {
        "concern_id": concern_id,
        "captain_id": concern.get("captain_id"),
        "disposition": concern.get("disposition"),
        "action_taken": concern.get("action_taken"),
        "rubric_version": rubric.get("version"),
        "per_dimension": per_dimension,
        "composite": composite,
        "overall_rationale": overall,
        "audited_at": _now(),
    }
    with _lock:
        items = _load()
        items.append(result)
        _write(items)
    return result


def audit_batch(limit: int = 10) -> dict:
    """Audit the most recent `limit` UN-audited concerns (sampling — not everything).
    Returns a summary {audited, results, skipped_already_audited}."""
    audited_ids = {a.get("concern_id") for a in _load()}
    todo = []
    for c in concern_log.all_concerns():   # newest first
        cid = c.get("id")
        if cid and cid not in audited_ids:
            todo.append(cid)
        if len(todo) >= max(1, int(limit or 10)):
            break
    results = [audit_concern(cid) for cid in todo]
    ok = [r for r in results if "error" not in r]
    return {
        "audited": len(ok),
        "avg_composite": round(sum(r["composite"] for r in ok) / len(ok)) if ok else None,
        "results": results,
        "already_audited": len(audited_ids),
    }


def scores() -> dict:
    """Audit history + aggregates for the dashboard: overall avg composite, avg
    per-dimension, a by-day time series, and a breakdown by disposition."""
    items = _load()
    rubric = rubric_mod.get_rubric()
    history = list(reversed(items))   # newest first

    if not items:
        return {"count": 0, "avg_composite": None, "per_dimension_avg": {},
                "time_series": [], "by_disposition": {}, "rubric_version": rubric.get("version"),
                "history": []}

    avg_composite = round(sum(i.get("composite", 0) for i in items) / len(items))

    # avg per dimension across all audits (only dimensions present in the results)
    dim_sums: dict[str, float] = defaultdict(float)
    dim_counts: dict[str, int] = defaultdict(int)
    for i in items:
        for k, v in (i.get("per_dimension") or {}).items():
            try:
                dim_sums[k] += float(v.get("score", 0) or 0)
                dim_counts[k] += 1
            except (TypeError, ValueError):
                pass
    per_dimension_avg = {
        k: round(dim_sums[k] / dim_counts[k], 3) for k in dim_sums if dim_counts[k]
    }

    # time series by day (avg composite + count)
    day_sums: dict[str, float] = defaultdict(float)
    day_counts: dict[str, int] = defaultdict(int)
    for i in items:
        day = (i.get("audited_at") or "")[:10] or "unknown"
        day_sums[day] += i.get("composite", 0)
        day_counts[day] += 1
    time_series = [
        {"day": d, "avg_composite": round(day_sums[d] / day_counts[d]), "count": day_counts[d]}
        for d in sorted(day_sums)
    ]

    # breakdown by disposition
    disp_sums: dict[str, float] = defaultdict(float)
    disp_counts: dict[str, int] = defaultdict(int)
    for i in items:
        disp = i.get("disposition") or "unknown"
        disp_sums[disp] += i.get("composite", 0)
        disp_counts[disp] += 1
    by_disposition = {
        d: {"avg_composite": round(disp_sums[d] / disp_counts[d]), "count": disp_counts[d]}
        for d in disp_sums
    }

    return {
        "count": len(items),
        "avg_composite": avg_composite,
        "per_dimension_avg": per_dimension_avg,
        "time_series": time_series,
        "by_disposition": by_disposition,
        "rubric_version": rubric.get("version"),
        "dimensions": rubric.get("dimensions", []),
        "history": history,
    }


def _get_concern(concern_id: str) -> dict | None:
    for c in concern_log.all_concerns():
        if c.get("id") == concern_id:
            return c
    return None
