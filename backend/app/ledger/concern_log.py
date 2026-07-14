"""The Concern Log (BRD §4.6, §8) — append-only event ledger + problem graph.

Every Concern, decision and outcome is an immutable event. Replay, audit,
analytics, Continuous Problem Discovery and the learning flywheel all fall out of
this for free. For the demo it is an in-process append-only list persisted to
JSON (Postgres in production, BRD §8/§12).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..state_paths import state_path

# MUTABLE ledger → durable state dir (survives redeploys); default backend/data.
_STORE = Path(state_path("concern_log.json"))
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> list[dict]:
    if _STORE.exists():
        try:
            return json.loads(_STORE.read_text())
        except Exception:  # noqa: BLE001
            return []
    return []


def append(concern: dict) -> dict:
    """Append an immutable Concern record. Returns the stored record."""
    with _lock:
        log = _load()
        concern = {**concern, "logged_at": _now(), "seq": len(log) + 1}
        log.append(concern)
        _STORE.write_text(json.dumps(log, indent=1))
        return concern


def all_concerns() -> list[dict]:
    return list(reversed(_load()))   # newest first


def stats() -> dict:
    log = _load()
    resolved = [c for c in log if c.get("action_taken") in {"reverse_debit", "clear_pendency", "respond"}]
    escalated = [c for c in log if c.get("action_taken") == "escalate"]
    money = sum(c.get("amount_inr", 0) or 0 for c in log if c.get("action_taken") == "reverse_debit")
    by_disp: dict[str, int] = {}
    for c in log:
        d = c.get("disposition", "unknown")
        by_disp[d] = by_disp.get(d, 0) + 1
    return {
        "total": len(log),
        "resolved_in_conversation": len(resolved),
        "escalated": len(escalated),
        "money_recovered_for_partners_inr": money,
        "by_disposition": by_disp,
    }
