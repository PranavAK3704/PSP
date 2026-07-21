"""Audit + Continuous Problem Discovery (revised vision).

The audit layer reads the Concern Log (the full trace of every interaction) and
runs the satisfaction loop: after a resolution the partner is asked if they're
satisfied. If NOT, that is a CPD signal — we capture what was missing and log it
for the partner-support team to action (new SOP / KT / data gap).
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..ledger import concern_log
from ..state_paths import state_path
from ..durable_state import durable_path

# MUTABLE store → durable state dir (survives redeploys); default backend/data.
_STORE = durable_path("cpd_log.json")
_lock = threading.Lock()


def _load() -> list[dict]:
    if _STORE.exists():
        try:
            return json.loads(_STORE.read_text())
        except Exception:  # noqa: BLE001
            return []
    return []


def record_satisfaction(concern_id: str, captain_id: str, satisfied: bool, note: str = "") -> dict:
    """Log a satisfaction outcome. Dissatisfaction → a CPD item."""
    entry = {
        "id": "CPD-" + uuid.uuid4().hex[:6].upper(),
        "concern_id": concern_id, "captain_id": captain_id,
        "satisfied": satisfied, "note": note,
        "is_cpd": not satisfied, "status": "open" if not satisfied else "closed",
        "at": datetime.now(timezone.utc).isoformat(),
    }
    with _lock:                       # atomic read-modify-write (parity with the other ledger stores)
        items = _load()
        items.append(entry)
        _STORE.write_text(json.dumps(items, indent=1))
    return entry


def cpd_items() -> list[dict]:
    return [c for c in reversed(_load()) if c.get("is_cpd")]


def satisfaction_stats() -> dict:
    items = _load()
    total = len(items)
    sat = sum(1 for i in items if i.get("satisfied"))
    return {"responses": total, "satisfied": sat,
            "csat_pct": round(100 * sat / total) if total else None,
            "open_cpd": sum(1 for i in items if i.get("is_cpd") and i.get("status") == "open")}


def audit_trail(limit: int = 50) -> list[dict]:
    """Fair audit: for each Concern, the full chain (who → disposition → data →
    understanding → action → outcome), straight from the immutable Concern Log."""
    out = []
    for c in concern_log.all_concerns()[:limit]:
        out.append({
            "concern_id": c.get("id"), "captain_id": c.get("captain_id"),
            "channel": c.get("channel"), "conversation_id": c.get("conversation_id"),
            "disposition": c.get("disposition"), "intent": c.get("intent"),
            "data_used": [e.get("source") for e in c.get("evidence_trail", [])],
            "action_taken": c.get("action_taken"), "confidence": c.get("confidence"),
            "outcome": c.get("outcome"), "turns": c.get("turns"),
            "reply": c.get("reply"), "logged_at": c.get("logged_at"),
        })
    return out
