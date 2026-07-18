"""SOP Compiler (BRD §4.3 — "the crown jewel").

Turns a plain-language SOP (as a functional team would write it) into a
structured ExecutablePolicy — ONCE, at authoring time, with an LLM (Opus tier).
This is where judgement is allowed; execution stays deterministic.
"""
from __future__ import annotations

import difflib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..llm import registry as llm_registry
from ..llm.gemini_provider import _parse_json
from ..state_paths import state_path
from ..durable_state import durable_path

# Compiled SOPs live as APPROVED KT (tagged compiled_sop) in the durable-state kt_queue —
# the SAME store the KT engine and retrieval corpus use (state_paths lists kt_queue as durable
# state). Locally $PSP_STATE_DIR is unset so this resolves to backend/data, unchanged.
_KT_STORE = durable_path("kt_queue.json")

_SYSTEM = """You are the SOP Compiler for Valmo's partner-support resolution engine.
You convert a plain-language Standard Operating Procedure into a strict, executable
policy. You do NOT resolve any case — you only structure the SOP so a deterministic
engine can execute it every time. Be precise and conservative. Money caps must be
explicit. Every check must name the evidence (source rows) it reads."""

_PROMPT = """Compile the following plain-language SOP into an ExecutablePolicy.

Return ONLY JSON with EXACTLY this shape:
{{
  "id": "pol_<short_slug>",
  "disposition": "<snake_case theme>",
  "version": "v1.0",
  "trigger": {{"keywords": ["..."], "preconditions": ["..."]}},
  "required_evidence": ["<source_row_name>", "..."],
  "checks": [
    {{"id": "<slug>", "description": "<the yes/no test>", "reads": ["<evidence>"], "expect": "<passing condition>"}}
  ],
  "resolution": {{"action": "<verb_snake>", "params": {{"idempotent": true}}, "cap_inr": <number or null>}},
  "escalation": {{"team": "<team (Ln)>", "handover": "<what to hand over>"}},
  "partner_rights": ["<guardrail from the Partner Constitution>"],
  "source_sop_ref": "manual_compile",
  "compiled_by": "sop_compiler"
}}

The Partner Constitution principles you may cite in partner_rights:
presumption of good faith, true-cause attribution, radical transparency,
guaranteed SLAs, auto error-correction, proportionality + downside caps,
right to appeal + a human, consistency, no silent policy changes.

PLAIN-LANGUAGE SOP:
---
{sop_text}
---
"""


def detect_policy_gaps(policy: dict) -> list[dict]:
    """Inline gaps for a compiled ExecutablePolicy (parity with the Blueprint side, req #4):
    each {severity, where, message}. Flags where the engine wouldn't know what to ask / how
    to act. Surfaced AT compile/save time, not a separate queue."""
    p = policy or {}
    gaps: list[dict] = []
    trig = p.get("trigger", {}) or {}
    if not (trig.get("keywords") or []):
        gaps.append({"severity": "warn", "where": "trigger.keywords",
                     "message": "No trigger keywords — retrieval won't know when to surface this SOP."})
    if not (p.get("required_evidence") or []):
        gaps.append({"severity": "warn", "where": "required_evidence",
                     "message": "No required evidence — the engine won't know what to gather / ask the captain."})
    checks = p.get("checks", []) or []
    if not checks:
        gaps.append({"severity": "high", "where": "checks",
                     "message": "No deterministic checks — there is no yes/no test to decide the case."})
    evidence_names = {str(e).strip().lower() for e in (p.get("required_evidence") or [])}
    for i, c in enumerate(checks):
        if not str(c.get("description", "")).strip():
            gaps.append({"severity": "warn", "where": f"checks[{i}]",
                         "message": "Check has no description — state the yes/no test it runs."})
        reads = [str(r).strip().lower() for r in (c.get("reads") or [])]
        missing = [r for r in reads if r and r not in evidence_names]
        if missing:
            gaps.append({"severity": "high", "where": f"checks[{i}].reads",
                         "message": f"Check reads {missing} but that evidence isn't in required_evidence — "
                                    f"the engine can't gather it."})
    res = p.get("resolution", {}) or {}
    if not str(res.get("action", "")).strip():
        gaps.append({"severity": "warn", "where": "resolution.action",
                     "message": "No resolution action — say what the engine does when the checks pass."})
    esc = p.get("escalation", {}) or {}
    if not str(esc.get("team", "")).strip():
        gaps.append({"severity": "high", "where": "escalation.team",
                     "message": "No escalation team — every SOP needs a safe fallback owner when checks fail."})
    return gaps


# ── DEDUP guardrail: find already-compiled SOPs similar to the one being authored ──
_SIM_STOP = {"the", "a", "an", "of", "to", "for", "is", "are", "and", "or", "in", "on", "at",
             "by", "with", "when", "if", "not", "no", "this", "that", "from", "as", "it"}


def _sim_tokens(s: str) -> set[str]:
    """Lowercased alphanumeric tokens minus stopwords — for simple set-overlap similarity."""
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower())
            if w not in _SIM_STOP and len(w) > 1}


def _compiled_sops() -> list[dict]:
    """Approved KT entries tagged compiled_sop (the compiled-SOP corpus)."""
    try:
        items = json.loads(_KT_STORE.read_text()) if _KT_STORE.exists() else []
    except Exception:  # noqa: BLE001
        return []
    return [k for k in items if k.get("compiled_sop") and k.get("status") == "approved"]


def find_similar_sops(title: str, text: str = "", k: int = 3) -> list[dict]:
    """Scan existing compiled SOPs and return the closest matches to (title, text) by simple
    NORMALIZED overlap on title + keywords — NO new deps, stdlib only (set overlap + difflib).
    Score is 0–1 (max of token-set Jaccard and difflib title ratio); only matches above ~0.4
    are returned, best first, capped at k. Returns [{id, title, score}]."""
    q_title = (title or "").strip()
    q_tok = _sim_tokens(q_title + " " + (text or ""))
    out: list[dict] = []
    for entry in _compiled_sops():
        st = entry.get("structured", {}) or {}
        pol = entry.get("policy", {}) or {}
        cand_title = st.get("title") or pol.get("disposition") or entry.get("id") or ""
        keywords = (list(st.get("triggers", []) or []) + list(st.get("tags", []) or [])
                    + list((pol.get("trigger", {}) or {}).get("keywords", []) or []))
        c_tok = _sim_tokens(str(cand_title) + " " + " ".join(str(w) for w in keywords))
        union = q_tok | c_tok
        jaccard = len(q_tok & c_tok) / len(union) if union else 0.0
        ratio = (difflib.SequenceMatcher(None, q_title.lower(), str(cand_title).lower()).ratio()
                 if q_title and cand_title else 0.0)
        score = max(jaccard, ratio)
        if score >= 0.4:
            out.append({"id": entry.get("id"), "title": cand_title, "score": round(score, 2)})
    out.sort(key=lambda m: m["score"], reverse=True)
    return out[:k]


def compile_sop(sop_text: str) -> tuple[dict, dict]:
    """Return (executable_policy, meta). meta carries model + token usage."""
    provider, model = llm_registry.for_node("sop_compile")
    res = provider.generate(
        _PROMPT.format(sop_text=sop_text.strip()),
        model=model, node="sop_compile", system=_SYSTEM, json_mode=True,
    )
    policy = _parse_json(res.text)
    if isinstance(policy, list) and policy:   # some runs wrap the object in an array
        policy = policy[0]
    meta = {"model": res.model, "provider": provider.name,
            "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
            "latency_ms": res.latency_ms}
    return policy, meta


def compile_sop_streamed(sop_text: str):
    """Stream the compilation as stages so the UI can animate the real structuring/tiering
    (BRD §4.3). Yields: understand → the actual extracted sections of the ExecutablePolicy →
    done. Each stage carries the real data pulled from the compiled policy."""
    yield {"stage": "understand", "label": "Understanding the SOP",
           "detail": "Reading the plain-language SOP with the deep-tier model…"}

    policy, meta = compile_sop(sop_text)
    policy = policy or {}

    def _stage(stage, label, data):
        return {"stage": stage, "label": label, "data": data}

    trig = policy.get("trigger", {}) or {}
    yield _stage("trigger", "Extracting triggers",
                 {"keywords": trig.get("keywords", []), "preconditions": trig.get("preconditions", [])})
    yield _stage("required_evidence", "Mapping required evidence",
                 {"required_evidence": policy.get("required_evidence", [])})
    yield _stage("checks", "Compiling deterministic checks",
                 {"checks": policy.get("checks", [])})
    res = policy.get("resolution", {}) or {}
    yield _stage("resolution", "Setting the resolution + money cap",
                 {"action": res.get("action"), "cap_inr": res.get("cap_inr"), "params": res.get("params", {})})
    esc = policy.get("escalation", {}) or {}
    yield _stage("escalation", "Attaching escalation route",
                 {"team": esc.get("team"), "handover": esc.get("handover")})
    yield _stage("partner_rights", "Binding Partner-Constitution rights",
                 {"partner_rights": policy.get("partner_rights", [])})

    # DEDUP guardrail: surface already-compiled SOPs this one overlaps (non-blocking warning).
    similar = find_similar_sops(policy.get("disposition", "") or "", sop_text)
    yield {"stage": "done", "label": "Executable Policy compiled",
           "policy": policy, "gaps": detect_policy_gaps(policy), "meta": meta,
           "similar_sops": similar}


def _sop_entry(p: dict, contributor: str, status: str) -> dict:
    """Build the KT entry for a compiled SOP (draft or approved)."""
    trig = p.get("trigger", {}) or {}
    res = p.get("resolution", {}) or {}
    esc = p.get("escalation", {}) or {}
    # Render the structured policy into a canonical, retrievable knowledge statement.
    lines = []
    if p.get("required_evidence"):
        lines.append("Required from the captain: " + ", ".join(p["required_evidence"]) + ".")
    for c in (p.get("checks") or []):
        if c.get("description"):
            lines.append("Check: " + c["description"])
    if res.get("action"):
        cap = f" (cap ₹{res.get('cap_inr')})" if res.get("cap_inr") is not None else ""
        lines.append(f"Resolution: {res['action']}{cap}.")
    if esc.get("team"):
        lines.append(f"Escalate to {esc['team']}" + (f" — {esc['handover']}" if esc.get("handover") else "") + ".")
    knowledge = "\n".join(lines) or (p.get("disposition") or "SOP")
    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "id": "SOP-" + uuid.uuid4().hex[:8].upper(),
        "contributor": contributor, "raw_text": json.dumps(p),
        "structured": {"title": p.get("disposition") or p.get("id") or "SOP",
                       "type": "policy", "queue": (esc.get("team") or p.get("disposition") or "general"),
                       "triggers": (trig.get("keywords") or []) + [p.get("disposition", "")],
                       "knowledge": knowledge,
                       "tags": ["sop", "compiled"] + (trig.get("keywords") or [])},
        "type": "policy", "status": status, "compiled_sop": True,
        "policy": p, "submitted_at": now,
    }
    if status == "approved":
        entry["reviewed_by"] = contributor
        entry["reviewed_at"] = now
    return entry


def _persist_sop(policy: dict, contributor: str, status: str, sop_id: str = "") -> dict:
    """Persist a compiled SOP as a KT entry (status 'draft' or 'approved'). When sop_id is given
    (editing from the Knowledge Base) the existing entry is UPDATED in place, keeping its id — no
    duplicate. Otherwise a new entry is created; any earlier DRAFT of the same disposition is
    dropped so drafts don't pile up and a queued draft becomes its approved form cleanly. Approved
    SOPs reload the retrieval corpus so the engine follows them immediately (parity with kt.review)."""
    p = policy or {}
    # Ensure the first-run seed is planted before we touch the store, so writing an SOP can
    # never pre-empt the seed (which would otherwise be skipped once the file is non-empty).
    from ..kt import engine as _kt
    _kt.ensure_seeded()
    entry = _sop_entry(p, contributor, status)
    if sop_id:
        entry["id"] = sop_id                       # keep the same id when editing in place
    items = json.loads(_KT_STORE.read_text()) if _KT_STORE.exists() else []
    disp = str(p.get("disposition") or "").strip().lower()
    items = [e for e in items
             if e.get("id") != sop_id                                    # drop the edited entry
             and not (not sop_id and e.get("compiled_sop") and e.get("status") == "draft"
                      and disp and str((e.get("policy") or {}).get("disposition", "")).strip().lower() == disp)]
    items.append(entry)
    _KT_STORE.write_text(json.dumps(items, indent=1))
    if status == "approved":
        from . import store
        store.reload()
    return entry


def approve_sop(policy: dict, contributor: str = "sop-author", sop_id: str = "") -> dict:
    """A reviewed structured SOP enters the retrieval corpus (with reload) so the engine
    follows it — persisted as an APPROVED, compiled KT entry. Returns the stored entry."""
    return _persist_sop(policy, contributor, "approved", sop_id)


def save_sop_draft(policy: dict, contributor: str = "sop-author", sop_id: str = "") -> dict:
    """Save a compiled SOP as a DRAFT so it is never lost — it shows in the library (as 'draft')
    and can be approved later. Does NOT enter the retrieval corpus until approved."""
    return _persist_sop(policy, contributor, "draft", sop_id)


def delete_sop(sop_id: str) -> bool:
    """Remove a compiled SOP by id (Knowledge Base management). Reloads the corpus so the engine
    stops following it immediately. Returns True if an entry was removed."""
    if not sop_id:
        return False
    from ..kt import engine as _kt
    _kt.ensure_seeded()
    items = json.loads(_KT_STORE.read_text()) if _KT_STORE.exists() else []
    kept = [e for e in items if e.get("id") != sop_id]
    if len(kept) == len(items):
        return False
    _KT_STORE.write_text(json.dumps(kept, indent=1))
    from . import store
    store.reload()
    return True
