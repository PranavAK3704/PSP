"""Proactive Monitoring Layer (BRD §5).

The SAME engine, entered from a data-change event instead of a chat message. A cheap
deterministic pass filters the cohort so an LLM runs ONLY when a real risk fires — cost scales
with risk events, not captain count (BRD §5.2). Warnings are partner-protective and shipped
shadow-first (BRD §5.3).

══ WHAT CHANGED HERE, AND WHY IT MATTERED MORE THAN IT LOOKED ══════════════════════════════

**The shipment source.** This read `context["shipments"]` → `SEED[captain_id]`, keyed on three
demo captain ids. The Captain Panel's partner ids come from `valmo.db` — a different namespace —
so docking this monitor on that page returned "No risk" every single time. It now reads the
`risk` cohort from `captain_context`, which is real rows out of the loss ledger. Set
`PSP_RISK_SOURCE=seed` for the old behaviour, byte for byte.

**A real AWB was being sent to a third-party model.** `_compose_nudge` interpolated
`risk['awb']` into the prompt. `engine/dataplane.py` defines a leak as precisely "an identifier
the model would NOT otherwise have had", and this is the ONLY LLM path in the repo with no
captain message to allow-list against — so the allowed set is empty and every AWB is a
violation. It was latent only because the seed AWB was fictional; under real data one LZ5 scan
would have shipped 26 real AWBs. The cohort prompt now carries counts, money, the hub code
(permitted — a hub is a facility, not a person) and the deadline. Nothing else. And the check
runs AT RUNTIME, not only in the harness, because a guard that only exists in a test is a
comment.

**No spend ceiling applied to this path at all.** `provider.generate` was called without
`turn=`, so `meter.check`'s per-turn branch was skipped entirely and only the deployment-lifetime
`LLM_BUDGET_USD` stood between a scan and the bill. N model calls where N is read from the data
is not a budget: 26 for LZ5, 34 for LZI, unbounded for a real partner. There is now one
`TurnMeter` per scan, and exactly ONE compose call.

**One cohort nudge, not one per risk.** This also fixes the Pipeline node-collapse (it keys by
node id, latest-wins, so 25 of 26 nudges were silently discarded) and it is the better message:
a captain does not want 26 notifications, they want "26 shipments, ₹11,567, here is what the
record supports". Cost per scan drops from ~$0.044 to ~$0.0023.

**The `firstpass` label named two steps and neither existed.** It said "rules + vector". There is
no vector step — this module never calls `knowledge/store.py`, and that module has no embeddings
either. Now: "deterministic rules".
"""
from __future__ import annotations

import uuid
from typing import Iterator

from ..engine import dataplane
from ..ledger import concern_log
from ..llm import meter as llm_meter
from ..llm import registry as llm_registry
from ..substrate import captain_context as ctx

#: Monotonic, so `Pipeline` can key its collapse map on `node#seq` rather than on `node` alone.
#: Without it a repeated node id overwrites its predecessor and the earlier event vanishes.
_SEQ = {"n": 0}


def _evt(node, label, status, detail="", data=None, seq=None):
    """One trace event. `seq` is monotonic UNLESS the caller passes one.

    Passing an existing seq is how a `running` → `done` pair on the SAME logical step collapses
    into one node in the UI, while two genuinely distinct occurrences of the same node id keep
    separate slots. `Pipeline.jsx` keys its collapse map on `node#seq` for exactly this reason.
    """
    if seq is None:
        _SEQ["n"] += 1
        seq = _SEQ["n"]
    return {"node": node, "label": label, "status": status, "detail": detail,
            "data": data or {}, "seq": seq}


def _log_detection(captain_id: str, summary: dict, banded: list, *,
                   nudge: str | None, blocked: str = "") -> dict:
    """Record the DETECTION, whether or not a nudge was composed.

    WHY THIS IS SEPARATE FROM THE NUDGE. The append used to sit after `_compose_nudge`, so every
    path that returned early — nothing banded, a data-plane refusal, a provider failure — wrote
    NOTHING. That is why a scan of 26 real at-risk shipments left no trace in the ledger at all:
    there are zero records with `channel: "proactive"` in the entire log, because the compose step
    has never once completed on this deployment.

    The detection is the part that cost nothing and is always true. A model being unreachable is
    a reason to have no nudge TEXT; it is not a reason to forget that 26 shipments were banded.
    """
    return concern_log.append({
        "id": "CNC-" + uuid.uuid4().hex[:8].upper(), "captain_id": captain_id,
        "channel": "proactive",
        "intent": f"{len(banded)} shipment(s) banded at {summary.get('hub') or captain_id}",
        "disposition": "proactive_nudge" if nudge else "risk_detected",
        "action_taken": "nudge_sent" if nudge else "none",
        "amount_inr": summary.get("total_amount_at_risk_inr"),
        "outcome": "proactive_nudge" if nudge else "risk_detected",
        "reply": nudge or "",
        **({"blocked": blocked} if blocked else {}),
        "evidence_trail": [
            {"label": "Cohort", "value": f"{summary.get('total_shipments')} shipment(s)",
             "source": "risk.derive"},
            {"label": "Amount on record",
             "value": f"₹{summary.get('total_amount_at_risk_inr', 0):,.0f}",
             "source": "risk.derive"},
            {"label": "Reversal signal",
             "value": (f"{summary.get('reversal_signal', {}).get('n', 0)}"
                       f"/{summary.get('reversal_signal', {}).get('of', 0)} on record"),
             "source": "policy_exec reversal_signal columns"},
            {"label": "Clock", "value": summary.get("clock_rule", ""), "source": "risk.derive"},
        ],
        "awbs": [s.get("awb") for s in banded if s.get("awb")],
    })


def scan_captain(captain_id: str, *, as_of=None, clock_mode: str | None = None) -> Iterator[dict]:
    """Run the monitor over one captain's at-risk cohort.

    `as_of` + `clock_mode="replay"` evaluate the ladder at one shared date instead of at each
    row's own terminal date. The mode is on every event, because a replay distribution must never
    be readable as a live one.
    """
    turn = llm_meter.TurnMeter()
    context = ctx.get_context(captain_id) or {}
    risk = context.get("risk") or {}
    summary = risk.get("summary") or {}
    shipments = risk.get("shipments") or []

    # ── PROVENANCE FIRST, before any number ─────────────────────────────────────
    # Deliberately event 1. The same reasoning growth/connector.py gives for putting `source`
    # into `_sources` where the composers print it: a reader who sees a rupee figure before they
    # see where it came from has already formed an impression.
    yield _evt("source", "Risk source", "done",
               detail=(f"{summary.get('source', 'unknown')} · "
                       f"clock: {summary.get('clock_rule', 'n/a')}"),
               data={"source": summary.get("source"), "mode": summary.get("mode"),
                     "clock_mode": summary.get("clock_mode"),
                     "clock_rule": summary.get("clock_rule"),
                     "as_of": summary.get("as_of"), "keyed_on": summary.get("keyed_on")})

    raw = summary.get("raw_rows", len(shipments))
    consolidated = summary.get("consolidated_awbs", 0)
    yield _evt("stream", "Data-change events", "done",
               detail=(f"{len(shipments)} shipment(s) on the stream"
                       + (f" — {raw} raw rows, {consolidated} consolidated by AWB"
                          if consolidated else "")),
               data={"shipments": len(shipments), "raw_rows": raw,
                     "consolidated_awbs": consolidated})

    # Banded means the ladder could read it. Tri.UNKNOWN never fires a nudge — that is the whole
    # point of tri.py: "no facility in-scan on record" and "not at risk" are different sentences.
    banded = [s for s in shipments if s.get("risk_verdict") == "YES" and s.get("risk_category")]
    unknown = summary.get("uncategorised", {}).get("n", 0)
    yield _evt("firstpass", "Cheap first-pass (deterministic rules)", "done",
               detail=(f"{len(banded)} banded, {unknown} UNKNOWN — the model is invoked once, "
                       f"for the cohort" if banded
                       else "Nothing banded — discarded, no LLM"),
               data={"banded": len(banded), "unknown": unknown,
                     "events_scanned": len(shipments),
                     "by_category": summary.get("by_category", {})})

    if not banded:
        # THREE distinct cases, because they are three different sentences and the old single
        # "No risk" string covered all of them.
        if not shipments:
            detail = ("No loss rows join this partner in the ledger. That is an absence of DATA, "
                      "not an absence of risk.")
        elif unknown:
            detail = (f"{unknown} shipment(s) could not be read — no in-scan on record, or a leg "
                      f"with no SLA. UNKNOWN is not a clean bill of health; it is a reason for a "
                      f"human to look.")
        else:
            detail = "Nothing past the first rung — cost stayed at zero (no LLM)."
        yield _evt("clear", "Nothing banded", "done", detail=detail,
                   data={"shipments": len(shipments), "unknown": unknown})
        yield _evt("honesty", "What this is and is not", "done",
                   detail=(risk.get("provenance", {}) or {}).get("cohort", ""),
                   data=risk.get("provenance", {}))
        return

    # ── the ONE compose call ────────────────────────────────────────────────────
    # Captured so every resolution of THIS step reuses it and collapses onto the same node.
    compose = _evt("compose", "Compose cohort nudge", "running",
                   detail=f"{len(banded)} banded shipment(s) — composing one note", data={})
    compose_seq = compose["seq"]
    yield compose

    prompt = _cohort_prompt(context.get("profile") or {}, summary, banded)

    # THE DATA-PLANE CHECK, at runtime. The allowed set is EMPTY on this path: there is no captain
    # message here, so nothing has been supplied and every identifier is unsupplied by
    # definition. Refuse-as-DATA rather than raise — the `tools._act` lesson, where raising after
    # work was done shredded the case.
    leaks = dataplane.violations(prompt, set())
    if leaks:
        det = _log_detection(captain_id, summary, banded, nudge=None,
                             blocked=f"dataplane: {len(leaks)} unsupplied identifier(s)")
        yield _evt("compose", "Compose cohort nudge", "blocked",
                   detail=(f"Refused — {len(leaks)} unsupplied identifier(s) in the prompt. "
                           f"No nudge was composed and nothing was sent to a model. The "
                           f"detection is still on the record."),
                   data={"kinds": sorted({l["kind"] for l in leaks}), "count": len(leaks),
                         "concern_id": det.get("id")},
                   seq=compose_seq)
        yield _evt("honesty", "What this is and is not", "done",
                   detail=(risk.get("provenance", {}) or {}).get("cohort", ""),
                   data=risk.get("provenance", {}))
        return

    try:
        nudge, meta = _compose_nudge(prompt, turn=turn)
    except Exception as e:  # noqa: BLE001 — a compose failure must not truncate the stream
        # The DETECTION still happened and is still true. Without this, a dead or budget-exhausted
        # key means a scan of 26 banded shipments leaves no record at all — which is exactly the
        # state this deployment has been in.
        det = _log_detection(captain_id, summary, banded, nudge=None,
                             blocked=f"{type(e).__name__}: {str(e)[:120]}")
        yield _evt("compose", "Compose cohort nudge", "blocked",
                   detail=(f"{type(e).__name__}: {str(e)[:170]} — the detection is on the "
                           f"record without a nudge."),
                   data={"concern_id": det.get("id")}, seq=compose_seq)
        yield _evt("cost", "Scan cost", "done",
                   detail=f"${turn.summary()['cost_usd']:.4f}", data=turn.summary())
        yield _evt("honesty", "What this is and is not", "done",
                   detail=(risk.get("provenance", {}) or {}).get("cohort", ""),
                   data=risk.get("provenance", {}))
        return

    # `compose` RESOLVES. It used to be emitted `running` and never superseded, so Pipeline left
    # a bead spinning forever after a finished scan. The payload rides on the `done` event and the
    # `running` one carries `{}`, so a collapse in either order keeps the data.
    yield _evt("compose", "Compose cohort nudge", "done",
               detail=f"composed by {meta['model']}",
               data={"model": meta["model"], "provider": meta["provider"],
                     "prompt_checked": True, "identifiers_in_prompt": 0},
               seq=compose_seq)

    # The concern log is LOCAL and durable, so the AWB list is allowed here — it is the trace,
    # not an outbound payload. outcome="proactive_nudge", never "escalated", so it stays out of
    # the L3 inbox while still showing in Ledger/Audit/Insights.
    concern = _log_detection(captain_id, summary, banded, nudge=nudge)

    yield _evt("nudge", "Proactive nudge (shadow-first)", "done", detail=nudge,
               data={"nudge": nudge, "model": meta["model"], "shadow": True,
                     "hub": summary.get("hub", ""), "concern_id": concern["id"],
                     "banded": len(banded), "by_category": summary.get("by_category", {}),
                     "amount_inr": summary.get("total_amount_at_risk_inr"),
                     "clock_mode": summary.get("clock_mode")})

    cost = turn.summary()
    yield _evt("cost", "Scan cost", "done",
               detail=(f"${cost['cost_usd']:.4f} · {cost['calls']} model call(s) for "
                       f"{len(banded)} banded shipment(s)"),
               data=cost)

    # TERMINAL, and not a footnote on the nudge. A reader who stops at the nudge must still have
    # had the provenance streamed to them.
    yield _evt("honesty", "What this is and is not", "done",
               detail=(risk.get("provenance", {}) or {}).get("cohort", ""),
               data=risk.get("provenance", {}))


def _cohort_prompt(profile: dict, summary: dict, banded: list[dict]) -> str:
    """The prompt. AWB-free and partner-id-free BY CONSTRUCTION, not by filtering afterwards.

    What it may carry: counts, money, the hub code, and the deadline. `dataplane.py` is explicit
    that a hub is a facility rather than a person, and the loss ledger publishes hub codes on
    exactly that reasoning.

    The deadline quoted is `connect_sla_hours`, never `sla_within` — see the SLA reconciliation in
    risk/ladder.py. Telling a captain they have three days on a 48-hour leg is the bug
    engine/algo/followups.py was rewritten to fix.
    """
    hub = summary.get("hub") or ""
    n = len(banded)
    amount = summary.get("total_amount_at_risk_inr", 0)
    rs = summary.get("reversal_signal", {})
    terminal = summary.get("already_terminal", 0)
    hours = next((s.get("connect_sla_hours") for s in banded if s.get("connect_sla_hours")), None)
    lost_day = next((s.get("sla_lost") for s in banded if s.get("sla_lost")), None)

    lines = [
        f"Compose a SHORT, warm, partner-protective note (2-3 sentences) in "
        f"{profile.get('language', 'hinglish')} for a Valmo delivery partner"
        + (f" at hub {hub}." if hub else "."),
        "",
        "FACTS — use only these, invent nothing:",
        f"  - {n} shipment(s) carry Rs {amount:,.0f} attributed to this partner on record.",
    ]
    if terminal:
        lines.append(f"  - {terminal} of them are ALREADY MARKED as losses. The window to "
                     f"connect them has closed.")
    if rs.get("n"):
        lines.append(f"  - {rs['n']} have a facility in-scan and/or a changed attribution on "
                     f"record — the same signal our loss engine reads when it recommends a "
                     f"reversal.")
    if hours and lost_day:
        lines.append(f"  - The connect SLA on this leg is {hours} hours; a loss is marked on "
                     f"D{lost_day}.")
    lines += [
        "",
        "RULES:",
        "  - Do NOT say anything was saved, prevented, avoided or caught. Nothing was.",
        "  - Do NOT promise a reversal or a refund. Say the record supports RAISING a dispute.",
        "  - Do NOT invent a shipment id, a date or an amount.",
        "Reply with ONLY the note text.",
    ]
    return "\n".join(lines)


def _compose_nudge(prompt: str, *, turn=None) -> tuple[str, dict]:
    """One metered call. `turn=` is what puts this path inside the per-turn dollar ceiling."""
    provider, model = llm_registry.for_node("monitor_compose")
    res = provider.generate(prompt, model=model, node="monitor_compose",
                            json_mode=False, turn=turn)
    return res.text.strip(), {"model": res.model, "provider": provider.name}
