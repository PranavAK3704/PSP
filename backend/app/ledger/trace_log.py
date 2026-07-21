"""Resolution Trace Log — the persisted, replayable stage trace of every turn.

The live engine (engine/conversation.py) STREAMS a trace of the resolution as it
works (capture → tools → checks → decision → reply). That stream is ephemeral — it
scrolls past in the UI and is gone. This store keeps a bounded, replayable copy of
that trace keyed by concern_id, so the Concern Log can expand any resolution and
show HOW the engine got there — and so the Auditing Studio's LLM judge can score it.

Append-only-ish JSON store (data/traces.json), mirroring concern_log.py's
file+lock style. Purely a sidecar: writing here must NEVER break the live turn
(the caller wraps save() in try/except — see conversation.py).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from ..state_paths import state_path
from ..durable_state import durable_path

# MUTABLE store → durable state dir (survives redeploys); default backend/data.
_STORE = durable_path("traces.json")
_lock = threading.Lock()

_MAX_TRACES = 2000       # cap the store — keep the most recent N concerns' traces
_MAX_ARRAY = 10          # cap rows/sources/list arrays inside an event's data
_MAX_STR = 500           # truncate long strings inside an event's data


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict:
    if _STORE.exists():
        try:
            data = json.loads(_STORE.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _write(store: dict) -> None:
    _STORE.write_text(json.dumps(store, indent=1))


def _trim(value):
    """Bound an arbitrary JSON value so the store stays small: truncate long
    strings, cap list length (recursing into items), recurse into dicts."""
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[:_MAX_STR] + "…"
    if isinstance(value, list):
        return [_trim(v) for v in value[:_MAX_ARRAY]]
    if isinstance(value, dict):
        return {k: _trim(v) for k, v in value.items()}
    return value


def _trim_event(event: dict) -> dict:
    """Keep the event's shape ({node,label,status,tier,detail,data}) but bound its data."""
    ev = {
        "node": event.get("node"),
        "label": event.get("label"),
        "status": event.get("status", "done"),
        "tier": event.get("tier"),
        "detail": _trim(event.get("detail", "")),
        "data": _trim(event.get("data") or {}),
    }
    return ev


def save(concern_id: str, captain_id: str, conversation_id: str, events: list[dict]) -> dict | None:
    """Persist the list of trace events for a turn under concern_id. Trims each
    event's data (arrays ~10, strings ~500 chars) and caps the store at ~2000
    traces. Returns the stored record, or None if concern_id is falsy."""
    if not concern_id:
        return None
    record = {
        "concern_id": concern_id,
        "captain_id": captain_id,
        "conversation_id": conversation_id,
        "events": [_trim_event(e) for e in (events or []) if isinstance(e, dict)],
        "saved_at": _now(),
    }
    with _lock:
        store = _load()
        store[concern_id] = record
        # cap: keep the most recently saved N (order by saved_at, drop oldest)
        if len(store) > _MAX_TRACES:
            ordered = sorted(store.items(), key=lambda kv: kv[1].get("saved_at", ""))
            for cid, _ in ordered[: len(store) - _MAX_TRACES]:
                store.pop(cid, None)
        _write(store)
    return record


def get(concern_id: str) -> dict | None:
    """Return the stored trace for a concern, or None."""
    if not concern_id:
        return None
    return _load().get(concern_id)
