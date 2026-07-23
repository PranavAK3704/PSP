"""Auditing Studio — the editable, VERSIONED quality rubric for the LLM judge.

A domain owner (or QA lead) authors HOW a resolution should be judged: which
dimensions matter and how heavily. The rubric is data, not code — editing it here
(or via the Auditing Studio UI) re-versions it and the judge (audit/runner.py)
scores against the current version, stamping each audit with its rubric_version so
scores stay comparable within a version.

JSON store (data/audit_rubric.json), mirroring the blueprints/kt store style.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from ..durable_state import durable_path

# MUTABLE authored content → durable state dir (survives redeploys); default backend/data.
_STORE = durable_path("audit_rubric.json")
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Seed rubric (version 1) — 6 dimensions, weights sum to 1.0 ───────────────
_SEED_DIMENSIONS = [
    {"key": "identification", "label": "Identification", "weight": 0.20,
     "description": "captured every available signal; identified the right concern(s); "
                    "caught multi-concern messages"},
    {"key": "grounding", "label": "Grounding", "weight": 0.15,
     "description": "used real/live data; correct lookups"},
    {"key": "decision", "label": "Decision", "weight": 0.20,
     "description": "correct action within policy; grounded, not guessed"},
    {"key": "partner_supportedness", "label": "Partner-supportedness", "weight": 0.25,
     "description": "partner-first — reversed/educated when warranted instead of lazy-escalating; "
                    "warm tone; captain's language; advocated FOR the partner"},
    {"key": "efficiency", "label": "Efficiency", "weight": 0.10,
     "description": "minimal turns; asked only for what it couldn't derive; no redundant questions"},
    {"key": "safety", "label": "Safety", "weight": 0.10,
     "description": "honest about uncertainty; within policy caps; adversarial verifier agreement"},
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
    """Current rubric (seeds version 1 on first run)."""
    with _lock:
        rubric = _load()
        if rubric is None:
            rubric = _seed()
            _write(rubric)
        return rubric


def _clean_dimensions(dimensions: list[dict]) -> list[dict]:
    """Normalise author-supplied dimensions into the stored shape. Blank keys are
    derived from the label; weights coerced to float (default 0)."""
    out: list[dict] = []
    for d in dimensions or []:
        label = str(d.get("label", "")).strip()
        key = str(d.get("key", "")).strip() or label.lower().replace(" ", "_").replace("-", "_")
        if not key:
            continue
        try:
            weight = float(d.get("weight", 0) or 0)
        except (TypeError, ValueError):
            weight = 0.0
        out.append({
            "key": key,
            "label": label or key.replace("_", " ").title(),
            "weight": max(0.0, weight),
            "description": str(d.get("description", "")).strip(),
        })
    return out


def save_rubric(dimensions: list[dict]) -> dict:
    """Persist an edited rubric → bumps version, returns the new rubric. Weights
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
