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


def _parse_iso(iso: str):
    """Lenient ISO-8601 → aware datetime (UTC default). None if unparseable."""
    try:
        t = datetime.fromisoformat(iso)
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None


def _fmt_hours(h: float | None) -> str:
    """Human, honest duration label. Never fabricates: None → '—'."""
    if h is None:
        return "—"
    if h >= 24:
        return f"{h / 24:.1f}d"
    if h >= 1:
        return f"{h:.1f}h"
    m = h * 60
    if m >= 1:
        return f"{round(m)}m"
    if m > 0:
        return "<1m"
    return "instant"


def resolution_time_stats() -> dict:
    """Best-effort OPS metric — mean elapsed time from a concern's ``logged_at`` to its
    resolution, measured straight off the append-only log.

    - resolved-in-conversation → the resolution IS the logged event, so elapsed ≈ 0 (instant).
    - L3-resolved → elapsed = (follow-up.logged_at − original.logged_at), both timestamps present.

    Honest by construction: only measurable resolutions are counted, and if there are none
    the display is "—" (never a fabricated number). Never raises — safe to call from insights.
    """
    try:
        log = _load()
        resolutions = {c["resolves_concern_id"]: c
                       for c in log if c.get("resolves_concern_id")}
        durations: list[float] = []      # hours, over every measurable resolution
        l3_durations: list[float] = []   # hours, L3-elapsed only
        in_conversation = 0
        for c in log:
            if c.get("action_taken") in {"reverse_debit", "clear_pendency", "respond"}:
                durations.append(0.0)    # resolved in-conversation → instant
                in_conversation += 1
            elif c.get("outcome") == "escalated":
                res = resolutions.get(c.get("id"))
                if not res:
                    continue             # still open — no resolution to measure
                t0 = _parse_iso(c.get("logged_at", ""))
                t1 = _parse_iso(res.get("logged_at", ""))
                if t0 and t1 and t1 >= t0:
                    h = (t1 - t0).total_seconds() / 3600.0
                    durations.append(h)
                    l3_durations.append(h)
        n = len(durations)
        if n == 0:
            return {"display": "—", "hours": None, "sample": 0,
                    "in_conversation": 0, "via_l3": 0,
                    "l3_mean_display": None, "basis": "no resolutions logged yet"}
        mean_h = sum(durations) / n
        l3_mean = (sum(l3_durations) / len(l3_durations)) if l3_durations else None
        return {
            "display": _fmt_hours(mean_h),
            "hours": round(mean_h, 4),
            "sample": n,
            "in_conversation": in_conversation,
            "via_l3": len(l3_durations),
            "l3_mean_display": _fmt_hours(l3_mean) if l3_mean is not None else None,
            "basis": "mean logged_at → resolution",
        }
    except Exception:  # noqa: BLE001 — insights must never break on this ops metric
        return {"display": "—", "hours": None, "sample": 0,
                "in_conversation": 0, "via_l3": 0,
                "l3_mean_display": None, "basis": "n/a"}
