"""The Concern Log (BRD §4.6, §8) — append-only event ledger + problem graph.

Every Concern, decision and outcome is an immutable event. Replay, audit,
analytics, Continuous Problem Discovery and the learning flywheel all fall out of
this for free. For the demo it is an in-process append-only list persisted to
JSON (Postgres in production, BRD §8/§12).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from ..durable_state import durable_path

# Stored action names are canonicalised on read — see trust/gate.canonical_action.
from ..trust.gate import canonical_action as _canon

# MUTABLE ledger → durable state dir (survives redeploys); default backend/data.
_STORE = durable_path("concern_log.json")
_lock = threading.Lock()

# ── PROVENANCE ────────────────────────────────────────────────────────────────────────────────
# WHY: this ledger is the substrate under "992 Concerns Logged" and "₹81,840 Recovered" on the
# deck, and under `avg_resolution_time` in /api/insights. Measured over 1,014 rows: 702 were
# written on one afternoon (2026-08-24), 462 carry the captain id `VLMO-CPT-4471` which matches
# no real partner in valmo.db, and 301 have `intent` equal to a disposition token rather than
# anything a person typed.
#
# An earlier version of this comment ended "Genuinely operational: ten." That number does not
# reproduce and is removed rather than corrected, because it also contradicted the
# classification shipped alongside it: `partner` is ZERO. Nothing in this ledger can be SHOWN
# to be partner traffic — the field did not exist when these rows were written. A count of
# real traffic is a thing to earn by running live turns, not to assert in a comment.
#
# The rows are not fake — nothing was fabricated, they are real engine output. They are just
# not PARTNER traffic, and a ledger that cannot tell the difference cannot be aggregated
# honestly. So every row now carries where it came from.
#
# `unclassified` is the default and `partner` is never inferred. That direction is the whole
# point: a writer I forgot to label shows up as an unclassified row in the provenance strip —
# visible and wrong-looking — instead of silently inflating the number the demo rests on.
SOURCES = ("partner", "operator", "monitor", "l3", "harness", "unclassified")

def _provenance(explicit: str | None) -> str:
    """FORCED env → explicit argument → $PSP_CONCERN_SOURCE → `unclassified`.

    ── WHY `FORCE` OUTRANKS AN EXPLICIT CLAIM ────────────────────────────────────────────────
    `scripts/_contain.py` sets both `PSP_CONCERN_SOURCE=harness` and `..._FORCE=1` for the life
    of a test run. Without the force leg this leaked in the one direction that matters:
    `/api/whatsapp/webhook` asserts `source="partner"` server-side (an inbound webhook IS a real
    handset — normally the soundest assertion in the system), and `check_followups_e2e.py` drives
    exactly that route. Explicit-wins therefore stamped harness rows `partner` — the single label
    that must never be produced by anything but a captain. Inside a contained run the truth is
    unconditional, so it is asserted unconditionally.

    ── AND WHY THERE IS NO ContextVar HERE ANY MORE ──────────────────────────────────────────
    The original design had a `writing_as()` context manager between the explicit argument and
    the env var. It is gone, for two reasons that compound. First, it CANNOT work for the
    request path — MEASURED, not assumed: the chat and WhatsApp routes return
    `EventSourceResponse(_sse(handle_turn(...)))`, and Starlette iterates a sync generator
    through `anyio.to_thread.run_sync`, which hands every `next()` a fresh copy of the caller's
    context, so a `.set()` inside such a generator survives to the first yield and no further:

        V.set("X"); yield V.get()   ->  "X"
                    yield V.get()   ->  ""      # the default is back

    Every append in a turn happens long after the first yield, so it would have stamped
    `unclassified` on all real partner traffic while looking like a working implementation.
    Second, having established that, nothing else called it — it had zero call sites, so the
    middle leg of this resolution was dead code that read as a live mechanism. The request path
    threads `source` as an argument, which is the only thing that survives a yield.
    """
    if os.environ.get("PSP_CONCERN_SOURCE_FORCE") == "1":
        forced = os.environ.get("PSP_CONCERN_SOURCE")
        if forced in SOURCES:
            return forced
    for candidate in (explicit, os.environ.get("PSP_CONCERN_SOURCE")):
        if candidate in SOURCES:
            return candidate
    return "unclassified"


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
    """Append an immutable Concern record. Returns the stored record.

    THE ONE CHOKE POINT for `source`. Every writer in the app funnels through here, so a
    provenance vocabulary enforced at this line cannot be bypassed by adding a caller.
    """
    with _lock:
        log = _load()
        concern = {**concern, "source": _provenance(concern.get("source")),
                   "logged_at": _now(), "seq": len(log) + 1}
        log.append(concern)
        _STORE.write_text(json.dumps(log, indent=1))
        return concern


def all_concerns() -> list[dict]:
    return list(reversed(_load()))   # newest first


def provenance_counts() -> dict:
    """How the ledger breaks down by where each row came from, plus what the aggregates drop.

    Exists so a number on the deck can be shown NEXT TO its own composition. "Concerns Logged"
    filtered to inbound is the honest figure, but a filtered number with nothing explaining the
    filter is its own kind of misleading — a reader cannot tell whether 431 means "quiet month"
    or "we excluded 589 rows". This makes the exclusion visible instead of implied.
    """
    log = _load()
    counts = {s: 0 for s in SOURCES}
    for c in log:
        counts[c.get("source") if c.get("source") in SOURCES else "unclassified"] += 1
    dropped = sum(counts[s] for s in NON_INBOUND_SOURCES)
    return {
        "counts": counts,
        "total": len(log),
        "inbound": len(log) - dropped,
        "excluded": dropped,
        "excluded_sources": list(NON_INBOUND_SOURCES),
    }


def inbound_concerns() -> list[dict]:
    """`all_concerns()` minus the rows nobody asked for — harness runs and monitor detections.

    THE DISTINCTION THIS EXISTS TO KEEP: the append-only LOG shows everything, because a test
    row is provenance and deleting it would make the ledger a worse record. But anything that
    AGGREGATES or SAMPLES — a score, a rate, an audit queue, a themes list — is answering a
    question about captain traffic, and 588 harness rows in a 1,014-row ledger will dominate any
    such answer.

    Not a filter to reach for by default. A lookup BY ID must use `all_concerns()`, or a row that
    was audited while it was visible becomes unreadable afterwards; the same goes for anything
    rendering the log itself. Every call site of this function should be a place where the answer
    is a statistic.
    """
    return [c for c in all_concerns() if c.get("source") not in NON_INBOUND_SOURCES]


#: Rows that are not a captain raising a concern. `harness` is a test run; `monitor` is the
#: platform noticing something nobody asked about (a real event, but not inbound traffic).
#: `unclassified` is deliberately NOT here — it predates the field and may well be real, and
#: excluding it would under-report rather than over-report. See ledger/concern_log's provenance
#: block and scripts/classify_concern_provenance.py.
NON_INBOUND_SOURCES = ("harness", "monitor")


def stats(include_test: bool = False) -> dict:
    """Deck-facing counts. EXCLUDES non-inbound rows unless asked not to.

    This function already justified excluding L3 follow-ups because "they are NOT captain
    concerns" — and once `source` existed it could see two more classes in exactly that
    category and was still counting them. Measured at the time of the fix: of 1,014 rows, 588
    were stamped `harness`, so "Concerns Logged" was 58% test traffic and every rupee under
    "Recovered for Partners" came from a harness row. The filter is the same principle the
    docstring already claimed, applied to the classes it can now identify.
    """
    log = _load()
    if not include_test:
        log = [c for c in log if c.get("source") not in NON_INBOUND_SOURCES]
    # Synthetic L3 follow-ups (they carry resolves_concern_id and action_taken=resolved_by_l3)
    # are NOT captain concerns — they shadow an original. Exclude them so total /
    # by_disposition don't double-count every resolved case (finding #31).
    real = [c for c in log if not c.get("resolves_concern_id")]
    # Canonicalised, because these read HISTORY: rows written before the
    # reverse_debit -> raise_for_reversal rename still carry the old name, and matching on the
    # new one alone would quietly drop every pre-rename reversal out of the totals.
    _act = lambda c: _canon(c.get("action_taken"))
    resolved = [c for c in real if _act(c) in {"raise_for_reversal", "clear_pendency", "respond"}]
    escalated = [c for c in real if c.get("action_taken") == "escalate"]
    money = sum(c.get("amount_inr", 0) or 0 for c in real if _act(c) == "raise_for_reversal")
    by_disp: dict[str, int] = {}
    for c in real:
        d = c.get("disposition", "unknown")
        by_disp[d] = by_disp.get(d, 0) + 1
    return {
        "total": len(real),
        "resolved_in_conversation": len(resolved),
        "escalated": len(escalated),
        "money_recovered_for_partners_inr": money,
        "by_disposition": by_disp,
        # So a caller can SAY what it excluded rather than presenting a filtered number as the
        # whole truth. The append-only view shows everything; the aggregate shows this.
        "excluded_sources": [] if include_test else list(NON_INBOUND_SOURCES),
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
            if _canon(c.get("action_taken")) in {"raise_for_reversal", "clear_pendency", "respond"}:
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
