"""Data-query path (BRD §4.4 tier (b) — live/past data questions).

The LLM SELECTS which named query to run (it never writes or touches SQL); the query runs
deterministically against the grounded context (Metabase + Log10 in prod); and then CODE
COMPOSES the answer. The model receives the composed sentence and a row count — never the
rows. See engine/dataplane.py for why that boundary exists and what it costs to cross it.

This is how "where is my shipment / truck timing / payout status" is answered.

Two functions, deliberately separate:
  _run(...)              → the rows. For the TRACE only: local, auditable, never sent out.
  run_and_compose(...)   → (answer_sentence, rows). The tool layer sends the sentence.

Composing in code rather than letting the model narrate rows is not only a privacy
boundary — it is the only way the sentence is REPRODUCIBLE. A composed answer is the same
for the same rows every time, which is what makes it auditable against the trace.
"""
from __future__ import annotations

from ..substrate import captain_context as ctx

# Named, whitelisted queries. New data need = a new entry here (or emitted by the
# SOP), NOT free-form SQL. Each maps to a deterministic function over the context.
from ..substrate import loss_db          # noqa: E402

QUERIES = {
    "shipment_status": "Current status + manifest path of the captain's open shipments (Log10)",
    "scan_history": "Full scan trail for a specific AWB (Log10) — needs an awb",
    "payout_status": "The captain's most recent payout/credit (Metabase)",
    "loss_summary": "Summary of losses/debits marked against the captain (Metabase)",
    "cod_status": "COD pendency + latest CMS deposit (Metabase)",
    "payout_deductions": ("What the loss deduction on a payout is made of — the lump sum split "
                          "into its AWBs, each with its loss type, amount and date "
                          "(valmo.db attribution, grouped on payout_id)"),
    "load_status": ("Why this captain's hub is getting the load it is getting — the order "
                    "waterfall, which stage lost the orders, the four performance levers "
                    "against their targets, and the ₹ at stake (Growth Dashboard)"),
}


def _run(name: str, params: dict, context: dict) -> list[dict]:
    cid = context["captain_id"]
    if name == "shipment_status":
        return [{"awb": s["awb"], "status": s["status"], "on_manifest_path": s.get("on_correct_manifest_path"),
                 "days_since_inscan": s.get("days_since_inscan"), "hub": s.get("hub")}
                for s in context.get("shipments", [])]
    if name == "scan_history":
        awb = params.get("awb") or (context.get("shipments") or [{}])[0].get("awb")
        scans = ctx.get_scans(cid, awb) if awb else None
        if not scans:
            return []
        return [{"awb": awb, "scan": e["scan"], "at": e["at"], "node": e["node"]} for e in scans.get("events", [])]
    if name == "payout_deductions":
        # ── THE LUMP-SUM QUESTION ───────────────────────────────────────────────────────────
        # "Rs 20,129 kaat liya, kis cheez ka?" A captain sees ONE figure and knows about two or
        # three AWBs. `attribution.payout_id` bundles them and had never been read — 1,438
        # payouts in this dataset carry more than one AWB, and every one of those questions was
        # an escalation.
        b = loss_db.payout_breakdown(str(cid), params.get("payout_id"))
        if not b:
            return []
        return [{"_summary": True, **{k: v for k, v in b.items() if k != "lines"}}] + b["lines"]
    if name == "payout_status":
        credits = [l for l in context.get("ledger", []) if l.get("type") == "credit"]
        return [{"id": c["id"], "amount_inr": c["amount_inr"], "date": c["date"], "narration": c["narration"]}
                for c in credits[-3:]]
    if name == "loss_summary":
        return [{"awb": l["awb"], "loss_type": l["loss_type"], "date": l["loss_date"],
                 "reason": l["reason_l1"], "attributed_node": l["attributed_node"]}
                for l in context.get("losses", [])]
    if name == "load_status":
        # ONE row, already shaped. The two growth endpoints are read by the connector during
        # get_context; this arm only projects what came back. Server-side verdicts are passed
        # THROUGH (is_good, banner_type, the counts, right_panel statuses) and never recomputed
        # — see adapters/growth/contract.py for which fields are whose.
        from ..substrate.adapters.growth import contract as gc
        g = context.get("growth") or {}
        if not g.get("available"):
            return []
        ym, osum = g.get("your_metrics") or {}, g.get("order_summary") or {}
        dominant, dominant_n = gc.dominant_reason(osum)
        return [{
            "hub": g.get("hub_code", ""),
            "window": f"{ym.get('start_date', '?')}–{ym.get('end_date', '?')}",
            "current_orders": ym.get("current_orders"),
            "max_potential": ym.get("max_potential"),
            "is_good": ym.get("is_good"),
            "banner_type": osum.get("banner_type"),
            "waterfall": {k: osum.get(k) for k in gc.WATERFALL},
            "dominant_reason": dominant,
            "dominant_count": dominant_n,
            "extra_earnings_loss": osum.get("extra_earnings_loss"),
            "reasons": osum.get("reasons") or [],
            "levers": [{"title": l["title"], "current": l["current"], "target": l["target"],
                        "status": l["status"].value, "why": l["why"]}
                       for l in gc.levers(ym)],
        }]

    if name == "cod_status":
        cash = context.get("cash", {})
        # `{}` from the provider means the cash system is NOT CONNECTED; a present key with
        # value 0 means a real, grounded zero pendency. Those are different answers and the
        # difference has to survive this function — `cash.get("cod_pendency_inr", 0)` used to
        # flatten both to 0, which turned "we cannot see your cash" into "you owe nothing".
        if not cash:
            return []
        return [{"cod_pendency_inr": cash.get("cod_pendency_inr"),
                 "deposits": cash.get("deposits", []),
                 "cms_assigned": cash.get("cms_assigned")}]
    return []


# ── composers: rows → one sentence the model can relay ───────────────────────────────
# House rules for every composer below:
#   · Never emit an AWB, a phone number, or a partner id. Say "one shipment", not which.
#     The captain knows which shipment they asked about; the model does not need to.
#   · Money and dates ARE allowed in composed form — they are the answer to the question,
#     and they arrive as a derived total rather than a row the model could mine.
#   · Never assert WHY a result is empty. "Nothing matched" is a fact; "the system is not
#     connected" is a claim about the environment that this function cannot verify and that is
#     wrong under a different provider. Name the source from `_sources` and stop there.
#   · An absent data source says so ONLY where the provider genuinely signals absence — for
#     COD that signal is a literally empty `cash` dict, which LocalDbProvider returns on
#     purpose. A silent "₹0 pending" is a specific, checkable, false claim about a captain's
#     cash; a "₹0 pending" from a provider that HAS the field is the correct answer.

def _num(v) -> float:
    """Never raises. A composer that dies on one malformed cell answers nothing at all."""
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v or 0).replace(",", "").replace("₹", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


def _inr(v) -> str:
    return "₹" + f"{int(round(_num(v))):,}"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {many or one + 's'}"


def _tally(rows: list[dict], key: str) -> str:
    """"5 hardstop, 3 shortage" — a counted breakdown, most common first."""
    counts: dict[str, int] = {}
    for r in rows:
        k = str(r.get(key) or "other").replace("_", " ").strip() or "other"
        counts[k] = counts.get(k, 0) + 1
    return ", ".join(f"{n} {k}" for k, n in sorted(counts.items(), key=lambda kv: -kv[1]))


def _compose_shipment_status(rows: list[dict], params: dict, src: dict) -> str:
    if not rows:
        # DO NOT assert why. An earlier version said "the scan system is not connected in this
        # environment" on every empty result, which is false under the default demo provider
        # and would be false under a live Log10 the moment a captain simply has no open
        # shipments — the agent would tell them tracking was down and escalate. Report the
        # fact (nothing matched), name the source, and hand the ambiguity to the model.
        return (f"No open shipments matched for this captain (source: {src.get('shipments', '?')}). "
                "That could mean they have none open, or that the shipment source has nothing "
                "for them — this query cannot tell the two apart, so do not assert either.")
    off = [r for r in rows if r.get("on_manifest_path") is False]
    stale = [r for r in rows if (r.get("days_since_inscan") or 0) >= 3]
    parts = [f"{_plural(len(rows), 'open shipment')} on record — {_tally(rows, 'status')}."]
    if off:
        parts.append(f"{_plural(len(off), 'is', 'are')} off the correct manifest path.")
    if stale:
        parts.append(f"{_plural(len(stale), 'has', 'have')} had no in-scan for 3+ days.")
    return " ".join(parts)


def _compose_scan_history(rows: list[dict], params: dict, src: dict) -> str:
    if not rows:
        awb_given = bool((params or {}).get("awb"))
        return ((f"No scan trail came back{'' if awb_given else ' (and no AWB was given to look up)'} "
                 f"(source: {src.get('shipments', '?')}). ")
                + "Without a scan timeline no date-based rule can be evaluated — the 5/7-day "
                  "connection window, the 3-attempt rule, the 2-day misroute window all need it. "
                  "Do not estimate any of them: say the timeline isn't available and escalate if "
                  "the captain needs it.")
    last = rows[-1]
    return (f"{_plural(len(rows), 'scan')} on record for that shipment. Latest: "
            f"{last.get('scan', 'unknown')} at {last.get('node', 'unknown node')} "
            f"on {last.get('at', 'an unrecorded date')}.")


def _compose_payout_deductions(rows: list[dict], params: dict, src: dict) -> str:
    """Answer the lump sum with its parts, biggest first.

    Written to be SPOKEN as well as read: the total comes first, then how many shipments, then
    the largest few. A captain listening to this on the read-aloud path needs the number in the
    first clause — a sentence that builds to it loses them before it arrives.
    """
    head = next((r for r in rows if r.get("_summary")), None)
    lines = [r for r in rows if not r.get("_summary")]
    if not head or not lines:
        return ("No payout with a loss deduction came back for this captain. That does NOT mean "
                "nothing was deducted — it means no payout in the attribution ledger carries a "
                "loss line for them. A deduction the captain can see on their invoice that is "
                "absent here belongs with Payments (L2), not answered from this.")
    parts = ", ".join(f"{t['n']} × {t['loss_type']} = {_inr(t['amount_inr'])}"
                      for t in (head.get("by_loss_type") or [])[:4])
    top = "; ".join(f"{l['awb']} {_inr(l['amount_inr'])}" for l in lines[:3])
    return (f"{_inr(head['total_deducted_inr'])} was deducted for losses in payout "
            f"{head.get('payout_id')}, across {_plural(head['shipments'], 'shipment')}"
            + (f" from the cycle starting {head['cycle_start']}" if head.get("cycle_start") else "")
            + f". By type: {parts}. Largest: {top}. "
            f"Every line is a real attribution row — the total is the sum of those rows, not a "
            f"figure quoted from a summary field.")


def _compose_payout_status(rows: list[dict], params: dict, src: dict) -> str:
    if not rows:
        # Same discipline as above: state what came back, not a claim about the environment.
        # "Nothing was paid" and "this source holds no payout rows" are very different
        # sentences to a captain waiting on money, and only the payments system can tell them
        # apart — so neither is asserted here.
        return (f"No payout or credit rows came back (source: {src.get('account', '?')}). "
                "This does NOT establish that nothing was paid — it establishes that this "
                "source has no payout row to show. Never tell the captain they were not paid "
                "on the strength of this; escalate to Payments (L2) instead.")
    total = sum(_num(r.get("amount_inr")) for r in rows)
    latest = rows[-1]
    return (f"{_plural(len(rows), 'credit')} in the recent window, {_inr(total)} in total. "
            f"Most recent: {_inr(latest.get('amount_inr'))} on "
            f"{latest.get('date') or 'an unrecorded date'}"
            + (f" ({latest.get('narration')})" if latest.get("narration") else "") + ".")


def _compose_loss_summary(rows: list[dict], params: dict, src: dict) -> str:
    if not rows:
        return "No losses or debits are marked against this captain in the ledger."
    n = len(rows)
    return (f"{_plural(n, 'loss debit')} on record — {_tally(rows, 'loss_type')}. "
            f"Most recent on {rows[0].get('date') or 'an unrecorded date'}. "
            f"Ask the captain which one they mean before acting on any of them; do not "
            f"volunteer debits they did not raise.")


def _compose_cod_status(rows: list[dict], params: dict, src: dict) -> str:
    row = rows[0] if rows else {}
    # `{}` from the provider is the DESIGNED answer for a source that isn't connected —
    # LocalDbProvider.get_cash() returns it deliberately rather than fabricating a zero.
    # A composed "₹0 pending" here would convert that honest gap into a false certainty.
    if not row or row.get("cod_pendency_inr") is None:
        # Unlike the other composers, this branch MAY name a cause: an empty `cash` dict is an
        # explicit provider signal, not merely an empty result. LocalDbProvider.get_cash()
        # returns {} on purpose because valmo.db holds no cash data, and it documents why.
        return (f"The active data provider ({src.get('account', '?')}) carries no COD or cash "
                "data at all, so the pendency figure cannot be checked — this is a missing "
                "source, not a zero balance. Do NOT state a zero or any amount: say the cash "
                "system isn't reachable and escalate to Cash / COD (L2).")
    # Reaching here means the source IS connected, so a zero is a real zero and saying so is
    # the correct answer — not a hedge.
    pend = row.get("cod_pendency_inr") or 0
    deps = row.get("deposits") or []
    out = ("No COD pendency is outstanding — the balance is clear."
           if not pend else f"COD pendency stands at {_inr(pend)}.")
    if deps:
        out += f" {_plural(len(deps), 'CMS deposit')} on record."
    elif pend:
        out += " No CMS deposit is on record against it."
    return out


def _compose_load_status(rows: list[dict], params: dict, src: dict) -> str:
    """Why the load is what it is, in the vocabulary the dashboard already uses.

    Reads only server-side verdicts and the levers' own targets. The `why` strings come from
    the panel's METRIC_TARGET_TOOLTIPS — the authored explanation a captain never opens the
    tooltip to see, which is the whole value PSP adds over the dashboard itself.
    """
    if not rows:
        return (f"No growth-dashboard data came back for this captain's hub "
                f"(source: {src.get('growth', '?')}). That could mean the hub has no data this "
                f"cycle, or that the growth source has nothing for it — this query cannot tell "
                f"the two apart, so do not assert either.")
    r = rows[0]
    w = r.get("waterfall") or {}
    hub, window = r.get("hub", "?"), r.get("window", "?")
    cur, mx = r.get("current_orders"), r.get("max_potential")

    parts = [f"Hub {hub}, {window}: {_plural(cur or 0, 'order')} manifested out of a maximum "
             f"potential of {mx}."]

    # The waterfall, stage by stage, with bar4's SIGN respected — the same number means orders
    # won or orders lost depending on which way it points.
    b4 = w.get("bar4_value")
    stages = [f"{w.get('missed_in_allocation')} lost before allocation",
              f"{w.get('current_eligible')} eligible after that"]
    if isinstance(b4, (int, float)) and b4:
        stages.append(f"{abs(int(b4))} {'extra orders won' if b4 > 0 else 'lost to a capacity cut'}")
    parts.append("Order flow: " + ", ".join(stages) + f", {w.get('final_manifested')} finally manifested.")

    # A green hub is NOT a grievance. `is_good` is the server's own verdict, so when it says the
    # hub is fine, the dominance line is suppressed — otherwise "126 orders lost at allocation"
    # sits next to "every lever is meeting target" and reads as a contradiction.
    dom = None if r.get("is_good") is True else r.get("dominant_reason")
    if dom == "allocation_miss":
        parts.append(f"The biggest single loss is at allocation — {r.get('dominant_count')} orders — "
                     f"which is decided by hub performance before any order reaches you.")
    elif dom == "capacity_loss":
        parts.append(f"The biggest single loss is a capacity cut — {r.get('dominant_count')} orders — "
                     f"which is applied when performance stays below the floor.")

    # Only levers the panel's own rule says are DEFINITELY missing target. UNKNOWN is excluded:
    # an unreadable value is not a finding to put in front of a captain.
    missing = [l for l in r.get("levers", []) if l["status"] == "NO"]
    if missing:
        parts.append("Levers below target: " + "; ".join(
            f"{l['title']} at {l['current']} against a target of {l['target']} ({l['why']})"
            for l in missing) + ".")
    elif r.get("is_good") is True:
        parts.append("Every performance lever is meeting its target, so there is no performance "
                     "reason for a shortfall here — say so plainly rather than inventing one.")

    loss = r.get("extra_earnings_loss")
    if isinstance(loss, (int, float)) and loss > 0:
        parts.append(f"Estimated earnings forgone this cycle: {_inr(loss)}.")
    return " ".join(str(p) for p in parts)


_COMPOSERS = {
    "payout_deductions": _compose_payout_deductions,
    "shipment_status": _compose_shipment_status,
    "scan_history": _compose_scan_history,
    "payout_status": _compose_payout_status,
    "loss_summary": _compose_loss_summary,
    "cod_status": _compose_cod_status,
    "load_status": _compose_load_status,
}


def run_and_compose(name: str, params: dict, context: dict) -> tuple[str, list[dict]]:
    """Run a named query and compose its answer. Returns (answer, rows).

    The CALLER sends only the answer to the model and puts the rows in the trace. An
    unknown query name is answered as such rather than as an empty result — the model
    picked a name outside the whitelist and needs to be told, not left to infer that the
    captain has no data.
    """
    if name not in QUERIES:
        return (f"'{name}' is not a known query. Available: "
                + ", ".join(sorted(QUERIES)) + "."), []
    rows = _run(name, params, context)
    composer = _COMPOSERS.get(name)
    # Provenance is PASSED IN, never inferred from an empty result. An empty result says
    # "nothing matched"; only `_sources` says which system was asked.
    src = context.get("_sources", {}) or {}
    answer = composer(rows, params, src) if composer else f"{len(rows)} row(s) returned."
    return answer, rows
