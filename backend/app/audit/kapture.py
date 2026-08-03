"""Kapture-ticket auditing engine.

Audits the resolution quality of EXTERNAL Kapture CRM tickets (each = a ticket number +
a conversation transcript). For each ticket it:
  1. scores it against the dedicated Kapture rubric (LLM-as-judge → per-dimension + composite),
  2. measures SOP COVERAGE (matched SOP, or NOVEL) and per-check ADHERENCE against our SOP base,
in ONE combined judge call, then persists a derived result row (NO raw transcript — PII).

Reuses the internal judge's scoring maths (audit/runner.py::_composite/_coerce_result), the
retrieval/disposition engine for coverage, and the same LLM mechanism the compilers use. Built
for LARGE batches: a streamed, chunked, resumable generator that dedupes already-audited tickets.
"""
from __future__ import annotations

import csv
import io
import json
import re
import threading
from collections import defaultdict
from datetime import datetime, timezone

from ..durable_state import durable_path
from ..llm import registry as llm_registry
from ..llm.gemini_provider import _parse_json
from ..engine import dispositions
from ..knowledge import policies as pol
from ..knowledge import store
from ..kt import engine as kt_engine
from . import kapture_rubric
from .runner import _composite, _coerce_result   # reuse the internal judge's scoring maths

_STORE = durable_path("kapture_audits.json")
_lock = threading.Lock()

# Coverage floor — tunable, and deliberately SEPARATE from dispositions.NOVEL_THRESHOLD (1.5), so
# QA can loosen/tighten "did an SOP cover this ticket" without touching the live resolution engine.
# A ticket counts as COVERED only if (a) retrieval clears this floor AND (b) we resolve an actual SOP
# (with checks) to audit against — an incidental word-overlap that maps to no real SOP is NOT coverage.
_COVERAGE_THRESHOLD = 2.0
_NON_SOP_THEMES = {"any", "general", "novel", ""}
_TRANSCRIPT_CAP = 12000     # chars fed to the judge
_COVERAGE_QUERY_CAP = 2000  # chars used for SOP retrieval (the issue is usually stated up front)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    if _STORE.exists():
        try:
            d = json.loads(_STORE.read_text())
            if isinstance(d, dict):
                d.setdefault("version", 1)
                d.setdefault("tickets", {})
                d.setdefault("runs", [])
                return d
        except Exception:  # noqa: BLE001
            pass
    return {"version": 1, "tickets": {}, "runs": []}


def _write(d: dict) -> None:
    _STORE.write_text(json.dumps(d, indent=1))


# ── PII redaction (applied to any derived text before it is persisted) ────────
_EMAIL = re.compile(r"\b[\w.\-]+@[\w.\-]+\.\w+\b")
_PHONE = re.compile(r"\b(?:\+?91[-\s]?)?[6-9]\d{9}\b")
_LONGID = re.compile(r"\b\d{12,}\b")


def _redact(s: str) -> str:
    s = str(s or "")
    s = _EMAIL.sub("[email]", s)
    s = _PHONE.sub("[phone]", s)
    s = _LONGID.sub("[id]", s)
    return s


# ── SOP index + coverage join ─────────────────────────────────────────────────
def build_sop_index() -> dict:
    """Map SOP id → its full structured policy (checks/resolution/escalation/title/disposition).
    Sources: compiled SOPs in the KT queue (id → policy) + the taxonomy policies (fallback)."""
    idx: dict[str, dict] = {}
    try:
        for k in kt_engine.all_kt():
            if k.get("compiled_sop") and k.get("policy"):
                sid = k.get("id") or (k["policy"].get("id"))
                if sid:
                    idx[sid] = k["policy"]
    except Exception:  # noqa: BLE001
        pass
    try:
        for p in pol.all_policies():
            if p.get("id"):
                idx.setdefault(p["id"], p)
    except Exception:  # noqa: BLE001
        pass
    return idx


def locate_sop(transcript: str, sop_index: dict) -> dict:
    """Retrieve the SOP that covers this ticket's issue (or flag it uncovered/NOVEL).
    Returns {covered, disposition, coverage_score, matched_sop_id, sop, supporting}."""
    query = (transcript or "").strip()[:_COVERAGE_QUERY_CAP]
    hits = store.retrieve(query, k=5) if query else []
    top = hits[0] if hits else None
    score = float(top.get("score", 0.0)) if top else 0.0
    supporting = [h.get("title") or h.get("id") for h in (hits or [])[:3]]
    if not top or score < _COVERAGE_THRESHOLD:
        return {"covered": False, "disposition": "NOVEL", "coverage_score": round(score, 2),
                "matched_sop_id": None, "sop": None, "supporting": supporting}
    disp = (top.get("disposition") or "").strip() or dispositions._theme_for(top)
    sop = sop_index.get(top.get("id"))            # the retrieved chunk joins straight back to its SOP
    sop_id = top.get("id") if sop else None
    if sop is None and disp.lower() not in _NON_SOP_THEMES:   # else the taxonomy policy for the disposition
        sop = pol.get_policy(disp)
        sop_id = (sop or {}).get("id")
    covered = sop is not None                     # covered ONLY if we found a real SOP to audit against
    return {"covered": covered, "disposition": disp if covered else "NOVEL",
            "coverage_score": round(score, 2), "matched_sop_id": sop_id if covered else None,
            "sop": sop, "supporting": supporting}


# ── the judge (ONE combined call: rubric quality + SOP adherence) ─────────────
_SYSTEM = """You are an independent QUALITY AUDITOR reviewing how a support agent handled an
external CRM ("Kapture") ticket for a Valmo delivery partner ("captain" or "pilot"). You are fair
but demanding: reward correct, partner-first, SOP-compliant resolutions; penalise wrong outcomes,
guessing, unhelpful deflection, and dishonesty. You judge ONLY against the rubric you are given,
and SEPARATELY assess whether the resolution followed the applicable SOP. You never resolve the
ticket yourself; you SCORE it. Return STRICT JSON only."""


def build_kapture_prompt(transcript: str, rubric: dict, sop: dict | None) -> str:
    dims = rubric.get("dimensions", [])
    dim_block = "\n".join(
        f'  - "{d["key"]}" ({d.get("label", d["key"])}, weight {d.get("weight", 0)}): {d.get("description", "")}'
        for d in dims)
    keys_json = ", ".join(f'"{d["key"]}"' for d in dims)
    t = (transcript or "").strip()
    if len(t) > _TRANSCRIPT_CAP:
        t = t[:_TRANSCRIPT_CAP] + "\n…[transcript truncated]"

    if sop:
        checks = sop.get("checks") or []
        clines = "\n".join(
            f'    {i+1}. {c.get("description", "")}' + (f'  [expect: {c.get("expect")}]' if c.get("expect") else "")
            for i, c in enumerate(checks)) or "    (no explicit checks listed)"
        res = sop.get("resolution") or {}
        esc = sop.get("escalation") or {}
        cap = f" (cap ₹{res.get('cap_inr')})" if res.get("cap_inr") is not None else ""
        sop_block = f"""THE SOP THIS RESOLUTION SHOULD HAVE FOLLOWED
  title: {sop.get('title') or sop.get('disposition') or '—'}
  required checks:
{clines}
  correct resolution action: {res.get('action', '—')}{cap}
  escalation if checks fail: {esc.get('team', '—')}"""
    else:
        sop_block = ("SOP COVERAGE: No SOP in our library covers this ticket's issue (uncovered / NOVEL).\n"
                     "Judge the rubric dimensions only; in sop_adherence set matched=false and per_check=[].")

    return f"""Audit this Kapture support ticket. Score EACH rubric dimension from 0.0 (poor) to 1.0
(excellent) with ONE short rationale line, give an overall_rationale, and SEPARATELY assess SOP
adherence against the SOP below.

RUBRIC DIMENSIONS (score every one of these):
{dim_block}

TICKET CONVERSATION TRANSCRIPT (partner ↔ agent):
{t}

{sop_block}

Return ONLY JSON with EXACTLY this shape:
{{
  "per_dimension": {{ {keys_json} : {{"score": <0.0-1.0>, "rationale": "<one line>"}} , ... }},
  "overall_rationale": "<2-3 sentence verdict — what was strong, what to fix>",
  "sop_adherence": {{
    "matched": <true|false>,
    "per_check": [ {{"ref": "<check number or name>", "followed": "yes|partial|no|unknown", "rationale": "<one line>"}} ],
    "resolution_action_followed": "yes|partial|no|unknown"
  }},
  "key_findings": ["<short factual bullet>", "..."]
}}
Every rubric key MUST appear in per_dimension with a numeric score in [0,1]."""


_FOLLOW = {"yes": 1.0, "partial": 0.5, "no": 0.0}


def _coerce_adherence(block, covered: bool) -> dict:
    """Defensively coerce the sop_adherence block; compute adherence % over graded checks
    (unknown excluded) blended with the resolution-action term. None when uncovered."""
    if not covered:
        return {"per_check": [], "resolution_action_followed": "unknown", "adherence": None}
    b = block if isinstance(block, dict) else {}
    per_check = []
    for c in (b.get("per_check") or []):
        if not isinstance(c, dict):
            continue
        f = str(c.get("followed", "unknown")).strip().lower()
        if f not in _FOLLOW:
            f = "unknown"
        per_check.append({"ref": str(c.get("ref", ""))[:60], "followed": f,
                          "rationale": _redact(str(c.get("rationale", ""))[:300])})
    raf = str(b.get("resolution_action_followed", "unknown")).strip().lower()
    if raf not in _FOLLOW:
        raf = "unknown"
    graded = [_FOLLOW[c["followed"]] for c in per_check if c["followed"] in _FOLLOW]
    if raf in _FOLLOW:
        graded.append(_FOLLOW[raf])
    adherence = round(100 * sum(graded) / len(graded)) if graded else None
    return {"per_check": per_check, "resolution_action_followed": raf, "adherence": adherence}


def audit_ticket(ticket_number: str, transcript: str, rubric: dict, sop_index: dict, run_id: str) -> dict:
    """Audit one Kapture ticket → coverage + judge → persist a derived row (no transcript)."""
    cov = locate_sop(transcript, sop_index)
    prompt = build_kapture_prompt(transcript, rubric, cov["sop"])

    provider, model = llm_registry.for_node("audit_judge")   # own node (falls back to fast tier if unset)
    parsed: dict = {}
    for _ in range(2):   # robust to a JSON miss — one retry, then fall back to zeros
        try:
            res = provider.generate(prompt, model=model, node="audit_judge", system=_SYSTEM, json_mode=True)
            parsed = _parse_json(res.text)
            if isinstance(parsed, list) and parsed:
                parsed = parsed[0]
            if isinstance(parsed, dict) and parsed.get("per_dimension"):
                break
        except Exception:  # noqa: BLE001
            parsed = {}
    parsed = parsed if isinstance(parsed, dict) else {}

    per_dimension = _coerce_result(parsed, rubric)
    composite = _composite(per_dimension, rubric)
    adh = _coerce_adherence(parsed.get("sop_adherence"), cov["covered"])
    findings = [_redact(str(x)[:200]) for x in (parsed.get("key_findings") or []) if str(x).strip()][:6]

    row = {
        "ticket_number": str(ticket_number),
        "rubric_version": rubric.get("version"),
        "run_id": run_id,
        "per_dimension": per_dimension,
        "composite": composite,
        "covered": cov["covered"],
        "disposition": cov["disposition"],
        "coverage_score": cov["coverage_score"],
        "matched_sop_id": cov["matched_sop_id"],
        "sop_title": (cov.get("sop") or {}).get("title"),          # what the ticket was audited against
        "coverage_candidates": cov.get("supporting") or [],        # top-3 near-matches — surfaces a wrong/NOVEL match
        "adherence": adh["adherence"],
        "per_check": adh["per_check"],
        "resolution_action_followed": adh["resolution_action_followed"],
        "key_findings": findings,
        "overall_rationale": _redact(str(parsed.get("overall_rationale", ""))[:600]),
        "audited_at": _now(),
    }
    with _lock:   # persist after every ticket → a dropped stream / crash resumes from here
        d = _load()
        d["tickets"][str(ticket_number)] = row
        _write(d)
    return row


# ── CSV parse (request-path) ──────────────────────────────────────────────────
_TICKET_ALIASES = ("ticket_number", "ticket", "ticket_id", "ticket no", "ticketno", "case_id", "case", "id")
_CONVO_ALIASES = ("conversation_history", "conversation", "transcript", "history", "chat",
                  "messages", "conversation history", "chat_history")


def _parse_csv(raw: bytes) -> list[dict]:
    """Parse an uploaded CSV of {ticket_number, conversation_history} → rows. Case-insensitive
    header aliases; quoted multi-line transcript cells parse natively. Dedupes within the file."""
    try:
        csv.field_size_limit(10 ** 7)
    except Exception:  # noqa: BLE001
        pass
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    norm = {(h or "").strip().lower(): h for h in fields}
    tcol = next((norm[a] for a in _TICKET_ALIASES if a in norm), None)
    ccol = next((norm[a] for a in _CONVO_ALIASES if a in norm), None)
    if not tcol or not ccol:
        raise ValueError("CSV needs a ticket-number column and a conversation-history column. "
                         "Found columns: " + (", ".join(fields) or "(none)"))
    rows, seen = [], set()
    for r in reader:
        tn = str(r.get(tcol, "") or "").strip()
        convo = str(r.get(ccol, "") or "").strip()
        if not tn or not convo or tn in seen:
            continue
        seen.add(tn)
        rows.append({"ticket_number": tn, "conversation_history": convo})
    return rows


# ── cost estimate ─────────────────────────────────────────────────────────────
def _price_per_mtok(model: str) -> tuple[float, float]:
    """Approximate 2026 USD per-Mtok (in, out). Illustrative — models.yaml carries no prices."""
    m = (model or "").lower()
    if "opus" in m:
        return (5.0, 25.0)
    if "sonnet" in m:
        return (3.0, 15.0)
    if "haiku" in m:
        return (0.8, 4.0)
    if "gpt-4o" in m or "gpt4o" in m:
        return (2.5, 10.0)
    return (1.25, 10.0)   # GPT-5-class default (current gpt-5.5)


def estimate_cost(rows: list[dict]) -> dict:
    """Pre-run token/₹ estimate over the to-be-audited (post-dedupe) tickets."""
    _, model = llm_registry.for_node("audit_judge")
    audited = set(_load()["tickets"])
    seen, to_audit = set(), []
    for r in rows or []:
        tn = str(r.get("ticket_number", "")).strip()
        if not tn or tn in seen:
            continue
        seen.add(tn)
        if tn not in audited:
            to_audit.append(r)
    in_tok = sum(min(len(str(r.get("conversation_history", ""))), _TRANSCRIPT_CAP) // 4 + 1500 for r in to_audit)
    out_tok = 600 * len(to_audit)
    p_in, p_out = _price_per_mtok(model)
    usd = (in_tok / 1e6) * p_in + (out_tok / 1e6) * p_out
    return {
        "tickets_total": len(seen), "already_audited": len(seen) - len(to_audit), "to_audit": len(to_audit),
        "est_input_tokens": in_tok, "est_output_tokens": out_tok,
        "est_cost_usd": round(usd, 2), "est_cost_inr": round(usd * 86), "model": model,
        "note": "Approximate — per-token rates are illustrative; dedupe of already-audited tickets applied.",
    }


# ── streamed, chunked, resumable batch ────────────────────────────────────────
def audit_batch_streamed(rows: list[dict], run_id: str, resume: bool = True):
    """Generator: audit each ticket, persist per-ticket, yield SSE stages
    (start → ticket → progress → done). Resumes by skipping already-audited ticket_numbers."""
    rubric = kapture_rubric.get_rubric()
    sop_index = build_sop_index()

    uniq, seen = [], set()
    for r in rows or []:
        tn = str(r.get("ticket_number", "")).strip()
        if not tn or tn in seen:
            continue
        seen.add(tn)
        uniq.append({"ticket_number": tn, "conversation_history": str(r.get("conversation_history", "") or "")})

    audited = set(_load()["tickets"]) if resume else set()
    todo = [r for r in uniq if r["ticket_number"] not in audited]
    yield {"stage": "start", "run_id": run_id, "total": len(uniq), "to_audit": len(todo),
           "already": len(uniq) - len(todo), "rubric_version": rubric.get("version")}

    done = cov = comp_sum = 0
    adh_vals: list[int] = []
    for r in todo:
        try:
            row = audit_ticket(r["ticket_number"], r["conversation_history"], rubric, sop_index, run_id)
        except Exception as e:  # noqa: BLE001 — one bad ticket must not kill the batch
            yield {"stage": "ticket", "ticket_number": r["ticket_number"], "error": str(e)[:160]}
            continue
        done += 1
        comp_sum += row["composite"]
        cov += 1 if row["covered"] else 0
        if row["adherence"] is not None:
            adh_vals.append(row["adherence"])
        yield {"stage": "ticket", "ticket_number": row["ticket_number"], "index": done, "total": len(todo),
               "composite": row["composite"], "covered": row["covered"], "disposition": row["disposition"],
               "matched_sop_id": row["matched_sop_id"], "adherence": row["adherence"]}
        if done % 10 == 0:
            yield {"stage": "progress", "done": done, "total": len(todo),
                   "avg_composite": round(comp_sum / done), "coverage_pct": round(100 * cov / done),
                   "adherence_pct": round(sum(adh_vals) / len(adh_vals)) if adh_vals else None}

    summary = {"run_id": run_id, "count": done,
               "coverage_pct": round(100 * cov / done) if done else 0,
               "adherence_pct": round(sum(adh_vals) / len(adh_vals)) if adh_vals else None,
               "avg_composite": round(comp_sum / done) if done else 0}
    with _lock:
        d = _load()
        d.setdefault("runs", []).append({**summary, "finished_at": _now(), "rubric_version": rubric.get("version")})
        _write(d)
    yield {"stage": "done", "summary": summary}


# ── dashboard aggregates ──────────────────────────────────────────────────────
def scores() -> dict:
    d = _load()
    tickets = list(d.get("tickets", {}).values())
    rubric = kapture_rubric.get_rubric()
    if not tickets:
        return {"count": 0, "avg_composite": None, "coverage_pct": None, "adherence_pct": None,
                "per_dimension_avg": {}, "by_disposition": {}, "novel_count": 0,
                "rubric_version": rubric.get("version"), "dimensions": rubric.get("dimensions", []),
                "runs": [], "history": []}
    n = len(tickets)
    cov = sum(1 for t in tickets if t.get("covered"))
    adh = [t["adherence"] for t in tickets if t.get("adherence") is not None]
    dsum: dict[str, float] = defaultdict(float)
    dcnt: dict[str, int] = defaultdict(int)
    for t in tickets:
        for k, v in (t.get("per_dimension") or {}).items():
            try:
                dsum[k] += float(v.get("score", 0) or 0)
                dcnt[k] += 1
            except (TypeError, ValueError):
                pass
    per_dimension_avg = {k: round(dsum[k] / dcnt[k], 3) for k in dsum if dcnt[k]}
    bd: dict[str, dict] = defaultdict(lambda: {"count": 0, "comp": 0, "cov": 0, "adh": []})
    for t in tickets:
        b = bd[t.get("disposition") or "unknown"]
        b["count"] += 1
        b["comp"] += t.get("composite", 0)
        b["cov"] += 1 if t.get("covered") else 0
        if t.get("adherence") is not None:
            b["adh"].append(t["adherence"])
    by_disposition = {k: {"count": v["count"], "avg_composite": round(v["comp"] / v["count"]),
                          "coverage_pct": round(100 * v["cov"] / v["count"]),
                          "adherence": round(sum(v["adh"]) / len(v["adh"])) if v["adh"] else None}
                      for k, v in bd.items()}
    history = sorted(tickets, key=lambda t: t.get("audited_at", ""), reverse=True)[:200]
    return {
        "count": n,
        "avg_composite": round(sum(t.get("composite", 0) for t in tickets) / n),
        "coverage_pct": round(100 * cov / n),
        "adherence_pct": round(sum(adh) / len(adh)) if adh else None,
        "per_dimension_avg": per_dimension_avg,
        "by_disposition": by_disposition,
        "novel_count": n - cov,
        "rubric_version": rubric.get("version"),
        "dimensions": rubric.get("dimensions", []),
        "runs": list(reversed(d.get("runs", []))),
        "history": history,
    }


# ── scores CSV export ─────────────────────────────────────────────────────────
def export_csv(run_id: str | None = None) -> str:
    d = _load()
    tickets = [t for t in d.get("tickets", {}).values() if (not run_id or t.get("run_id") == run_id)]
    tickets.sort(key=lambda t: str(t.get("ticket_number", "")))
    dim_keys: list[str] = []
    for t in tickets:
        for k in (t.get("per_dimension") or {}):
            if k not in dim_keys:
                dim_keys.append(k)
    fieldnames = (["ticket_number", "composite"] + [f"dim_{k}" for k in dim_keys]
                  + ["covered", "matched_sop_id", "disposition", "coverage_score", "adherence",
                     "resolution_action_followed", "key_findings", "overall_rationale"])
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()

    def _flat(s) -> str:
        return " ".join(str(s if s is not None else "").split())

    for t in tickets:
        row = {
            "ticket_number": t.get("ticket_number"),
            "composite": t.get("composite"),
            "covered": "yes" if t.get("covered") else "no",
            "matched_sop_id": t.get("matched_sop_id") or "",
            "disposition": t.get("disposition"),
            "coverage_score": t.get("coverage_score"),
            "adherence": t.get("adherence") if t.get("adherence") is not None else "",
            "resolution_action_followed": t.get("resolution_action_followed") or "",
            "key_findings": _flat("; ".join(t.get("key_findings") or [])),
            "overall_rationale": _flat(t.get("overall_rationale")),
        }
        for k in dim_keys:
            row[f"dim_{k}"] = (t.get("per_dimension") or {}).get(k, {}).get("score", "")
        w.writerow(row)
    return buf.getvalue()
