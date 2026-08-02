"""Kapture-audit rubric — a DEDICATED, versioned quality rubric for auditing external
Kapture CRM tickets, kept separate from the internal-resolution rubric (audit/rubric.py)
so the two never interfere.

A QA lead authors HOW a Kapture resolution should be judged: which factors matter and how
heavily. The rubric is data, not code — editing it (or uploading a QA-guidelines document
that the machine structures) re-versions it, and the Kapture judge (audit/kapture.py) scores
each ticket against the current version, stamping every audit with its rubric_version.

Mirrors audit/rubric.py exactly (same store/versioning idiom) + a structure-from-text upload
path cloned from knowledge/governance.py::structure_framework_from_text.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from ..durable_state import durable_path
from ..llm import registry as llm_registry
from ..llm.gemini_provider import _parse_json
from .rubric import _clean_dimensions   # reuse the internal rubric's dimension normaliser

# MUTABLE authored content → durable state dir (survives redeploys); default backend/data.
_STORE = durable_path("kapture_rubric.json")
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Seed rubric (version 1) — QA factors humans use to audit a Kapture ticket ──
_SEED_DIMENSIONS = [
    {"key": "resolution_correctness", "label": "Resolution correctness", "weight": 0.25,
     "description": "Was the issue actually resolved, correctly and completely, per policy? "
                    "Right outcome, not just a polite reply."},
    {"key": "sop_adherence", "label": "SOP adherence", "weight": 0.20,
     "description": "Did the agent follow the applicable SOP — its required checks, the correct "
                    "action, and the right escalation path when checks failed?"},
    {"key": "accuracy_grounding", "label": "Accuracy & grounding", "weight": 0.15,
     "description": "Facts stated were accurate and grounded in the partner's data — no guessing, "
                    "no fabricated status, correct amounts/IDs."},
    {"key": "partner_supportedness", "label": "Partner-supportedness", "weight": 0.15,
     "description": "Partner-first — advocated for the partner, resolved rather than lazily "
                    "deflecting/closing; did not push avoidable work back onto the partner."},
    {"key": "tat_efficiency", "label": "TAT & efficiency", "weight": 0.15,
     "description": "Resolved promptly with minimal back-and-forth; no needless re-asking or delay."},
    {"key": "tone_empathy", "label": "Tone & empathy", "weight": 0.10,
     "description": "Warm, respectful, clear tone in the partner's language; acknowledged the "
                    "partner's frustration where warranted."},
]


def _seed() -> dict:
    return {"version": 1, "dimensions": [dict(d) for d in _SEED_DIMENSIONS], "updated_at": _now()}


def _load() -> dict | None:
    if _STORE.exists():
        try:
            data = json.loads(_STORE.read_text())
            if isinstance(data, dict) and data.get("dimensions"):
                return data
        except Exception:  # noqa: BLE001
            return None
    return None


def _write(rubric: dict) -> None:
    _STORE.write_text(json.dumps(rubric, indent=1))


def get_rubric() -> dict:
    """Current Kapture rubric (seeds the QA-factor version 1 on first run)."""
    with _lock:
        rubric = _load()
        if rubric is None:
            rubric = _seed()
            _write(rubric)
        return rubric


def save_rubric(dimensions: list[dict]) -> dict:
    """Persist an edited Kapture rubric → bumps version, returns the new rubric. Weights
    need not sum to 1 (the judge normalises at scoring time)."""
    cleaned = _clean_dimensions(dimensions)
    if not cleaned:
        raise ValueError("rubric must have at least one dimension")
    with _lock:
        current = _load() or _seed()
        rubric = {
            "version": int(current.get("version", 0)) + 1,
            "dimensions": cleaned,
            "updated_at": _now(),
        }
        _write(rubric)
        return rubric


# ── STRUCTURE-FROM-TEXT (upload a QA-guidelines doc → weighted factors) ────────
# Cloned from knowledge/governance.py::structure_framework_from_text, retargeted at the
# rubric's flat {key,label,weight,description} dimension shape.
_SYSTEM = """You are the Audit-Rubric Compiler for a partner-support QA team. A QA lead uploads
a document describing HOW support tickets should be audited — the factors/criteria they grade on
and how heavily each matters. You STRUCTURE that document into a strict rubric of weighted
dimensions. You do NOT invent criteria — infer only what the document supports. Be precise."""

_PROMPT = """Convert the following audit-guidelines document into a scoring rubric. Return ONLY
JSON with EXACTLY this shape (the array MUST be present and non-empty):
{{
  "dimensions": [
    {{ "key": "<snake_case>", "label": "<short label>", "weight": <number 0-1>, "description": "<what this factor grades and how a good vs bad resolution looks>" }}
  ],
  "note": "<one sentence on what this rubric captures>"
}}

Rules:
- Each factor/criterion the document describes becomes one dimension with a snake_case key.
- Set each weight from the emphasis the document gives it (higher = more important). Weights need
  not sum to 1 — they are normalised at scoring time. If the document gives no weights, distribute
  them by apparent priority.
- Write a concrete description for each dimension so a judge can score 0.0-1.0 consistently.
- Infer only what the document supports; do not invent factors.

AUDIT-GUIDELINES DOCUMENT:
---
{text}
---
"""


def structure_rubric_from_text(text: str) -> dict:
    """LLM-structure an uploaded QA-guidelines document into a DRAFT rubric (weighted
    dimensions) for human review — never auto-saved. Robust JSON parse with one stricter
    retry. Reuses for_node("sop_compile") + json_mode + _parse_json, exactly like the
    governance framework's structure-from-text."""
    provider, model = llm_registry.for_node("sop_compile")
    prompt = _PROMPT.format(text=(text or "").strip())

    def _try(extra: str = "") -> dict:
        res = provider.generate(prompt + extra, model=model, node="sop_compile",
                                system=_SYSTEM, json_mode=True)
        parsed = _parse_json(res.text)
        if isinstance(parsed, list) and parsed:   # some runs wrap the object in an array
            parsed = parsed[0]
        return parsed if isinstance(parsed, dict) else {}

    parsed = _try()
    if not parsed or not parsed.get("dimensions"):
        parsed = _try("\n\nReturn STRICT valid JSON only — no prose, no markdown fences.")

    dims = _clean_dimensions(parsed.get("dimensions") if isinstance(parsed, dict) else [])
    if not dims:   # never hand back an empty rubric — fall back to the seed for review
        dims = [dict(d) for d in _SEED_DIMENSIONS]
    current = _load() or _seed()
    return {
        "version": int(current.get("version", 1) or 1),
        "dimensions": dims,
        "status": "draft",
        "updated_at": _now(),
        "note": (parsed.get("note") if isinstance(parsed, dict) else "")
                or "Structured from an uploaded document — review the factors + weights before saving.",
    }
