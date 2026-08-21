"""Deterministic policy execution (BRD §4.3 — execute the compiled policy).

Given a disposition, the grounded Captain Context, and extracted entities, this
runs the Executable Policy's checks in CODE (never the LLM) and returns a decision
with an evidence trail. This is where determinism is mandatory.
"""
from __future__ import annotations

from ..knowledge import policies as pol
from ..substrate import captain_context as ctx
from ..substrate import loss_db
from ..substrate.adapters.log10 import predicates as scan_pred
from ..tri import Tri


def _int(v):
    try:
        return int(float(str(v)))
    except (TypeError, ValueError):
        return None


def _find_disputed_debit(context: dict, amount: int | None, awb: str | None) -> dict | None:
    """Locate ONLY a debit the captain actually identified (by AWB or amount).
    NEVER fall back to an arbitrary debit — acting on a debit the captain didn't
    raise is a money-safety violation. No identifier match => None => escalate."""
    debits = [l for l in context.get("ledger", []) if l.get("type") == "debit"]
    if awb:
        for d in debits:
            if d.get("awb") == awb:
                return d
    if amount:
        for d in debits:
            if d.get("amount_inr") == amount:
                return d
    return None   # no identifier match — do not guess a debit


def execute(disposition: str, context: dict, entities: dict) -> dict:
    # ORDERS & PLANNING first, BEFORE the AWB branch. A load question is never answered by a
    # per-shipment lookup, and captains routinely paste an unrelated AWB into the same message
    # ("mera load kam hai, aur VL… ka kya hua"). Letting the AWB branch win there would answer
    # a loss question the captain did not ask and silently drop the one they did.
    if disposition == "load_planning":
        policy = pol.get_policy("load_planning")
        if policy:
            return _exec_load_planning(policy, context, entities)

    # DATA-GROUNDED PATH: if the captain gave an AWB, look it up in the real loss data
    # (valmo.db). The row's reason_l1 — not the LLM's guess — decides the disposition/policy,
    # and the real signals (facility_inscan / attribution_changed / loss_percentage) decide
    # the outcome. This is the production behaviour in miniature.
    awb = entities.get("awb")
    if awb:
        row = loss_db.get_loss_by_awb(awb)
        if row:
            disp = pol.reason_l1_to_disposition(row.get("reason_l1", ""))
            policy = pol.get_policy(disp) or pol.get_policy("hardstop_loss")
            pend = loss_db.get_pendency(awb)
            attrib = loss_db.get_attrib_change(awb)
            led = loss_db.get_attribution(awb)
            # captain_id is PASSED, not stashed in a module global. It used to be a global set
            # here and read inside _eval_real_loss, with four sqlite round-trips in between —
            # and sqlite releases the GIL, so under concurrent turns (sse_starlette drives the
            # generator through iterate_in_threadpool, across threads) captain A's decision could
            # read captain B's id. It only reached the seed-scan fallback, so no decision changed
            # and no cross-captain data was exposed — but the persisted evidence trail became
            # non-deterministic, which is the one thing an audit record cannot be.
            return _eval_real_loss(row, policy, awb, pend, attrib, led,
                                   captain_id=context.get("captain_id", ""))

    policy = pol.get_policy(disposition)
    if not policy:
        return {"action": "escalate", "disposition": disposition, "confidence": 0.0,
                "reason": "No Executable Policy for this disposition",
                "evidence_trail": [], "checks_run": [], "evidence_present": [], "policy": None}

    if disposition == "hardstop_loss":
        return _exec_hardstop(policy, context, entities)
    return {"action": "escalate", "disposition": disposition, "confidence": 0.3,
            "reason": "Policy present but no deterministic executor wired — escalating safely",
            "evidence_trail": [], "checks_run": [], "evidence_present": [], "policy": policy}


def _eval_real_loss(row: dict, policy: dict, awb: str, pend: dict | None = None,
                    attrib: dict | None = None, led: dict | None = None,
                    captain_id: str = "") -> dict:
    """Decide from the REAL loss row (valmo.db) + its enrichment (current pendency, attribution
    before→after, credit-note flag). Reversal signals: facility/LM in-scan, attribution changed,
    or a credit note already issued (cn_flag). Auto-reverses only for data-decidable categories
    within cap; otherwise grounds the full record and escalates to the owning team (never a
    dead-end). The trust gate + adversarial verifier still run after."""
    disp = policy["disposition"]
    reason_l1 = row.get("reason_l1", "")
    amount = _int(row.get("loss_value"))
    pct = (row.get("loss_percentage") or "").strip()
    inscan = (row.get("facility_inscan") or "").strip()
    attr_changed = (row.get("attribution_changed") or "").strip().lower() == "yes"
    cn_issued = (row.get("cn_flag") or "").strip().lower() == "yes"
    team = policy.get("escalation", {}).get("team", "Losses & Debits (L2)")
    action_kind = policy.get("resolution", {}).get("action", "escalate")
    cap = policy.get("resolution", {}).get("cap_inr")
    # the real loss row satisfies whatever this policy names as required evidence
    present = list(policy.get("required_evidence", ["loss_record"]))

    ev = [
        {"label": "Loss record", "value": f"{reason_l1} · {row.get('current_movement_type')} · "
         f"leg {row.get('leg')} · {row.get('location')}", "ref": f"valmo_lost#{awb}", "source": "loss_data"},
        {"label": "Debit amount", "value": f"₹{amount} ({pct})", "ref": f"valmo_lost#{awb}", "source": "loss_data"},
        {"label": "Facility in-scan", "value": inscan or "none on record", "source": "loss_data"},
        {"label": "Attribution changed", "value": "yes" + (f" ({row.get('row_count')} records)" if row.get("row_count", 1) > 1 else "")
         if attr_changed else "no", "source": "loss_data"},
    ]
    if cn_issued or (row.get("cn_number") or "").strip():
        ev.append({"label": "Credit note", "value": f"{row.get('cn_number') or 'issued'} → {row.get('credit_location') or 'credited'}",
                   "source": "loss_attrib"})
    # loss-attribution ledger state (debit/reversal workflow)
    if led:
        ev.append({"label": "Attribution ledger",
                   "value": f"{led.get('attribution_type','?')} · {led.get('attribution_state','?')} · "
                            f"₹{led.get('attribution_amount','?')}" + (f" · {led.get('cn_number')}" if (led.get('cn_number') or '').strip() else ""),
                   "source": "attribution_ledger"})
    # attribution history (before → after)
    if attrib:
        ev.append({"label": "Attribution before→after",
                   "value": f"{attrib.get('previous_leg1','?')}/{attrib.get('previous_loss_percentage','?')} → "
                            f"{attrib.get('latest_leg1','?')}/{attrib.get('latest_loss_percentage','?')}",
                   "source": "attrib_change"})
    # current shipment state (pendency)
    if pend:
        ev.append({"label": "Current shipment state",
                   "value": f"{pend.get('current_status','?')} · {pend.get('current_movement_type','?')} · "
                            f"{pend.get('current_location','?')}"
                            + (f" · misroute {pend.get('misroute_type')}" if (pend.get('misroute_type') or '').strip() else ""),
                   "source": "pendency"})
    reversal_signal = bool(inscan) or attr_changed

    # ── the typed scan timeline, as EVIDENCE on the money path ───────────────────────
    # This branch decides every real reversal, and it decides from two COLUMNS:
    # `facility_inscan` (a date) and `attribution_changed` (a flag). Neither says whether the
    # shipment actually moved on afterwards — a facility in-scan with no onward connection looks
    # identical to one followed by a clean handover.
    #
    # The timeline knows the difference, so it is recorded here. DELIBERATELY ADDITIVE: it does
    # not change the decision. Feeding a scan verdict into the 0.92 reverse branch is a real
    # behaviour change on the money path, and it needs its own review and its own regression run
    # against the pinned branch mix — not a quiet edit inside another change. What it does buy
    # today is that every reversal now carries the scan evidence a reviewer would ask for, and
    # a CONTRADICTION between the column and the timeline is visible instead of invisible.
    scan_note = None
    try:
        _t = _tracking_for(captain_id or "", awb)
        if _t is not None and _t.ok and _t.events:
            _conn, _row = scan_pred.forward_connection_within(_t)
            ev.append({**_row, "ref": f"log10_scans#{awb}"})
            ev.append({**scan_pred.timeline_summary(_t), "ref": f"log10_scans#{awb}"})
            if reversal_signal and _conn is Tri.NO:
                # The column says there is a reversal signal; the timeline says the shipment
                # never connected onward. Both are facts about the same shipment and they point
                # opposite ways. Flagged, not resolved — resolving it is a policy decision.
                scan_note = ("column signal and scan timeline DISAGREE — "
                             f"{_row['value']}. Worth a human look.")
                ev.append({"label": "Signal conflict", "value": scan_note,
                           "verdict": Tri.UNKNOWN.value, "source": "log10_scans"})
    except Exception:  # noqa: BLE001 — scan evidence is additive; never break the money path
        pass

    # 0) A credit note already issued ⇒ already reversed/credited.
    if cn_issued:
        return {"action": "respond", "disposition": disp, "amount_inr": amount, "awb": awb, "confidence": 0.9,
                "reason": f"Good news — a credit note ({row.get('cn_number') or 'issued'}) is already on record for AWB "
                          f"{awb}, so the ₹{amount} has been / is being credited back. Nothing pending from your side.",
                "evidence_trail": ev, "checks_run": [{"id": "credit_note", "description": "Credit note issued",
                "result": "PASS", "passed": True}], "evidence_present": present, "policy": policy}
    checks = [
        {"id": "loss_record_present", "description": "Disputed AWB found in the loss data",
         "result": "PASS", "passed": True},
        {"id": "reversal_signal", "description": "A data signal supports reversal (facility in-scan or attribution changed)",
         "result": ("PASS — " + ", ".join(([f"in-scan {inscan}"] if inscan else []) + (["attribution changed"] if attr_changed else [])))
         if reversal_signal else "NONE — no in-scan and attribution unchanged", "passed": reversal_signal},
    ]

    # 1) Not attributed to the partner at all (0% / Meesho leg) → nothing was debited.
    if pct in ("0%", "", None) or amount in (0, None) or (row.get("leg") or "").lower() == "meesho":
        return {"action": "respond", "disposition": disp, "amount_inr": amount, "awb": awb, "confidence": 0.9,
                "reason": f"On record this {reason_l1} loss was attributed to Meesho/upstream (loss {pct or '0%'}), "
                          f"so no debit was raised on your account for AWB {awb} — there is nothing to reverse.",
                "evidence_trail": ev, "checks_run": checks, "evidence_present": present, "policy": policy}

    # 2) Already revoked on record.
    if action_kind == "inform" or reason_l1 == "debit_revoked":
        return {"action": "respond", "disposition": disp, "amount_inr": amount, "awb": awb, "confidence": 0.9,
                "reason": f"Good news — the ₹{amount} debit on AWB {awb} is already marked REVOKED/reversed on record. "
                          f"Nothing is pending from your side.",
                "evidence_trail": ev, "checks_run": checks, "evidence_present": present, "policy": policy}

    # 3) Auto-reversible category WITH a reversal signal, within cap → reverse.
    if action_kind == "reverse_debit" and reversal_signal and amount is not None and (cap is None or amount <= cap):
        why = "the shipment has a facility in-scan on " + inscan if inscan else "the debit was already re-attributed"
        return {"action": "reverse_debit", "disposition": disp, "amount_inr": amount, "awb": awb,
                "debit_id": awb, "confidence": 0.92,
                "reason": f"The ₹{amount} debit on AWB {awb} was auto-marked as '{reason_l1}', but the loss record shows "
                          f"{why} — so it connected / is not attributable to you. Per policy this debit is reversed.",
                "evidence_trail": ev, "checks_run": checks, "evidence_present": present, "policy": policy}

    # 4) Everything else → ground the real record and escalate to the owning team.
    if action_kind == "reverse_debit" and amount is not None and cap is not None and amount > cap:
        note = f"debit ₹{amount} exceeds the ₹{cap} auto-reversal cap"
    elif action_kind == "reverse_debit":
        note = "no reversal signal on record (no facility in-scan, attribution unchanged)"
    else:
        note = f"a {reason_l1} dispute needs {team} to verify against the source SOP"
    return {"action": "escalate", "disposition": disp, "amount_inr": amount, "awb": awb, "confidence": 0.4,
            "reason": f"For AWB {awb} ({reason_l1}, ₹{amount}): {note}. I've filed the full loss record to {team} "
                      f"to review — this isn't a dead-end, they'll action it.",
            "evidence_trail": ev, "checks_run": checks, "evidence_present": present, "policy": policy}


def _exec_hardstop(policy: dict, context: dict, entities: dict) -> dict:
    cap_id = context["captain_id"]
    amount = entities.get("amount_inr")
    awb = entities.get("awb")
    debit = _find_disputed_debit(context, amount, awb)
    evidence_trail: list[dict] = []
    checks_run: list[dict] = []
    present: list[str] = []

    if not debit:
        # No live DB in this deploy: an AWB/amount we can't match against the captain's
        # records can't be verified here. Never dead-end — escalate to the team WITH what the
        # captain gave, honestly (they verify when connectivity resumes).
        ident = awb or (f"₹{amount}" if amount else "the reference given")
        ev = []
        if awb:
            ev.append({"label": "AWB (captain-provided)", "value": awb, "source": "captain_input"})
        if amount:
            ev.append({"label": "Amount (captain-provided)", "value": f"₹{amount}", "source": "captain_input"})
        if loss_db.available():
            reason = (f"I couldn't find {ident} in the loss records right now. I've filed it to "
                      f"Losses & Debits with your details so they check it against the live system "
                      f"and reverse it if it's wrong.")
            result = "NOT FOUND in loss records"
        else:
            reason = (f"Couldn't verify {ident} against live records right now (no database "
                      f"connectivity in this environment). Filing to Losses & Debits with your "
                      f"details so they verify and reverse when connectivity resumes.")
            result = "UNAVAILABLE"
        return {"action": "escalate", "disposition": "hardstop_loss", "confidence": 0.2, "amount_inr": amount,
                "reason": reason, "evidence_trail": ev,
                "checks_run": [{"id": "db_verify", "description": "Locate the debit in the loss records",
                                "reads": ["loss_data"], "result": result, "passed": False}],
                "evidence_present": [], "policy": policy}

    awb = awb or debit.get("awb")
    amount = debit.get("amount_inr")
    evidence_trail.append({"label": "Ledger debit", "value": f"{debit['id']} — ₹{amount} ({debit['reason']})",
                           "ref": f"payments_ledger#{debit['id']}", "source": "get_payments_ledger"})
    present.append("ledger_debit")

    # ── the scan timeline, TYPED ─────────────────────────────────────────────────────
    # This used to read two precomputed booleans out of the seed fixture
    # (`connected_within_tat`, `hardstop_sop_followed`) — the engine never looked at an event
    # type at all, while the reply string below *named* INWARD_SCAN and MANIFEST_SCAN as prose.
    # Now the verdicts are DERIVED from the events, and the crucial change is three-valued:
    # `bool(scans and scans.get(...))` turned "no scan data" into False, i.e. into evidence
    # AGAINST the captain, when the truth was "we cannot see the scans".
    tracking = _tracking_for(cap_id, awb)
    loss = next((l for l in context.get("losses", []) if l.get("awb") == awb), None)

    if tracking is not None and tracking.ok and tracking.events:
        present.append("scan_trail")
        evidence_trail.append({**scan_pred.timeline_summary(tracking),
                               "ref": f"log10_scans#{awb}"})
    if loss:
        present.append("loss_event")
        evidence_trail.append({
            "label": "Loss-marking event",
            "value": f"{loss['loss_type']} attributed to {loss['attributed_node']} "
                     f"on {loss['loss_date']} (reason: {loss['reason_l1']})",
            "ref": f"losses#{awb}", "source": "get_loss_attribution"})

    # ── Check 1: evidence present ──
    required = set(policy["required_evidence"])
    c1_ok = required.issubset(set(present))
    checks_run.append({"id": "evidence_present", "description": "All required source rows present",
                       "result": "PASS" if c1_ok else f"MISSING {sorted(required - set(present))}",
                       "passed": c1_ok})

    # ── Check 2: attributable to partner? (the key SOP check) ──
    # Three outcomes, not two. The old code had `attributable = None` for "no scans" but got
    # there via `bool(...)`, so an absent timeline and a timeline showing no movement produced
    # the same `False` for `connected` — and the escalation reason said "breach attributable"
    # in both cases. On a money decision those are opposite claims about the captain.
    connected, conn_row = scan_pred.forward_connection_within(tracking)
    if not any(r.get("label") == conn_row["label"] and r.get("value") == conn_row["value"]
               for r in evidence_trail):
        evidence_trail.append({**conn_row, "ref": f"log10_scans#{awb}"})

    # Process-breach signals from the SAME timeline. These replace `hardstop_sop_followed`,
    # which was a field that exists in no real dataset — it was written into the seed file by
    # hand. A misroute or a tamper IS a recorded process breach; a boolean someone typed is not.
    misrouted, mis_row = scan_pred.misroute_within(tracking)
    tampered, tam_row = scan_pred.tampered(tracking)
    for row in (mis_row, tam_row):
        # Only surface a finding or an unknown — and never the SAME row twice. When there is no
        # timeline at all every predicate returns the same "no tracking data" row, which would
        # otherwise print three identical lines and read like three separate problems.
        if row.get("verdict") == Tri.NO.value:
            continue
        if any(r.get("label") == row["label"] and r.get("value") == row["value"]
               for r in evidence_trail):
            continue
        evidence_trail.append({**row, "ref": f"log10_scans#{awb}"})

    breach = Tri.YES if Tri.YES in (misrouted, tampered) else (
        Tri.UNKNOWN if Tri.UNKNOWN in (misrouted, tampered) else Tri.NO)

    if connected is Tri.UNKNOWN:
        # NOT "attributable". We cannot see the scans, so we cannot say whose fault it is.
        c2_result = f"UNDETERMINED — {conn_row['value']}"
        c2_pass, attributable = False, None
    elif connected is Tri.YES and breach is not Tri.YES:
        c2_result = ("Valmo-side — the shipment connected onward within TAT and no misroute or "
                     "tamper is recorded → NOT the partner's fault")
        c2_pass, attributable = True, False
    elif connected is Tri.YES and breach is Tri.YES:
        c2_result = "Connected within TAT, but a misroute/tamper is on record — needs a human"
        c2_pass, attributable = False, None
    else:
        c2_result = f"Attributable — {conn_row['value']}"
        c2_pass, attributable = False, True
    checks_run.append({"id": "attributable_to_partner",
                       "description": "Is the loss attributable to the partner?",
                       "result": c2_result, "passed": c2_pass,
                       "verdict": connected.value})

    # ── Check 3: within reversal cap ──
    cap = policy["resolution"]["cap_inr"]
    c3_ok = amount is not None and amount <= cap
    checks_run.append({"id": "within_reversal_cap", "description": f"Debit ₹{amount} within cap ₹{cap}",
                       "result": "PASS" if c3_ok else "EXCEEDS CAP", "passed": c3_ok})

    # Decision: reverse only if all deterministic checks pass and loss is NOT the partner's fault.
    if c1_ok and c2_pass and c3_ok and attributable is False:
        # The reason is built from what the predicate ACTUALLY FOUND. The old text hardcoded
        # "INWARD_SCAN on {date} and a forward MANIFEST_SCAN within the LM-forward D5 TAT",
        # asserting specific event types regardless of which events the timeline held — a
        # sentence that could be false while every number in it was right.
        reason = (
            f"The ₹{amount} debit was AUTO-marked by the SLA job as "
            f"'{(loss or {}).get('reason_l1', 'not_connected')}'. The scan timeline — the "
            f"authoritative source — shows {conn_row['value']}, and no misroute or tamper is on "
            f"record. The auto-marking is therefore erroneous and Valmo-side; per SOP HS_1_1 "
            f"this debit is not attributable to the partner and should be raised for reversal."
        )
        return {"action": "reverse_debit", "disposition": "hardstop_loss",
                "amount_inr": amount, "awb": awb, "debit_id": debit["id"],
                "confidence": 0.93, "scenario": "HS_1_1",
                "reason": reason,
                "evidence_trail": evidence_trail, "checks_run": checks_run,
                "evidence_present": present, "policy": policy}

    # Ambiguous / inconclusive → escalate with the fully-worked case (never guess).
    # The REASON distinguishes the three ways of getting here, because they need three different
    # follow-ups and they used to share one sentence:
    #   attributable is None  → we could not see enough to decide (or a breach needs a human)
    #   attributable is True  → the scans are evidence against reversal
    #   a cap/evidence failure→ the decision was blocked on policy, not on the shipment
    if attributable is None:
        why = (f"I can't determine from the scan record whether this was our fault or not — "
               f"{conn_row['value']}. I've sent the worked case to the Losses & Debits team so a "
               f"human can check the shipment directly.")
    elif attributable is True:
        why = (f"The scan record doesn't support a reversal on its own — {conn_row['value']}. "
               f"I've sent the full case to the Losses & Debits team for review rather than "
               f"closing it here.")
    else:
        why = ("The deterministic checks couldn't all be satisfied, so I've escalated the worked "
               "case to the Losses & Debits team rather than deciding it here.")
    return {"action": "escalate", "disposition": "hardstop_loss",
            "amount_inr": amount, "awb": awb, "debit_id": debit["id"],
            "confidence": 0.45,
            "reason": why,
            "attributable": None if attributable is None else bool(attributable),
            "scan_verdict": connected.value,
            "evidence_trail": evidence_trail, "checks_run": checks_run,
            "evidence_present": present, "policy": policy}


def _tracking_for(captain_id: str, awb: str):
    """The typed scan timeline for an AWB, from whichever source is configured.

    ONE code path for fixtures, live, and the legacy seed captains — the seed blob is lifted
    into the same `Tracking` shape rather than being handled separately. That is deliberate: two
    branches would let the typed path and the boolean path drift, and the whole point is that
    the seed captains now flow through the same predicates a real fixture does.

    Returns None when there is nothing to read, which the predicates render as UNKNOWN.
    """
    try:
        from ..substrate.adapters.log10 import Log10Connector, dto as log10_dto
        conn = Log10Connector()
        t = conn.get_tracking(awb)
        if t is not None and t.ok and t.events:
            return t
        # Nothing typed for this AWB — fall back to a seed captain's legacy blob, LIFTED.
        return log10_dto.from_legacy_scans(ctx.get_scans(captain_id, awb), awb)
    except Exception:  # noqa: BLE001 — a scan-source failure must read as UNKNOWN, not crash
        return None


# ─────────────────────────────────────────────────────────────────────────────
# ORDERS & PLANNING (forward) — "why is my load low"
#
# The one queue that resolves with NO stub: the resolution IS the reply. `respond` is not in
# `gate.MONEY_ACTIONS`, so `money_moving` is False, `requires_adversarial_verify` is False, and
# no money is ever at stake. That is why this can reach 0.9 honestly while the loss path cannot.
#
# Everything here READS the Growth Dashboard's own verdicts and never re-derives them:
# `is_good`, `banner_type`, the five waterfall counts, the right-panel section counts, and the
# per-metric statuses inside right_panel are all computed server-side. The only thing computed
# locally is each LEVER's status, and that is a verbatim mirror of the captain panel's own
# client-side rule (see adapters/growth/contract.py) — because the wire carries no status for
# the four levers, only `{current, target}`.
#
# ── DIVERGENCE FROM SOURCE, recorded deliberately ───────────────────────────────────────────
# 1) The Metabase "Pin x Polygon" query this replaced had a real bug in its remark builder: the
#    `cps_delta` branch emitted "Improve your RTO Performance by [ocf_delta/0.65]%" — reached
#    via CPS, naming RTO, computing from OCF. Three defects in one sentence. Nothing here
#    replicates it: a lever is named only from its own value and its own target.
# 2) `growth-dashboard`'s `is_good` is measured against an ABSOLUTE TARGET. The Metabase
#    `L1_remarks` column branched on RANK against competitors. The two can disagree about the
#    same hub on the same day, and neither is wrong — they answer different questions. Do not
#    reconcile them later by assuming one is stale.
# ─────────────────────────────────────────────────────────────────────────────

def _exec_load_planning(policy: dict, context: dict, entities: dict) -> dict:
    """Explain a hub's load from the Growth Dashboard. Never moves money.

    `evidence_present` is built ONLY from what was actually read — deliberately NOT the
    `present = policy["required_evidence"]` shortcut `_eval_real_loss` uses at :83-84, which
    declares the policy's own requirements satisfied and so makes the gate's evidence check
    incapable of ever failing. Here a missing waterfall really does block.
    """
    from ..substrate.adapters.growth import contract as gc
    from ..tri import Tri

    g = context.get("growth") or {}
    hub = g.get("hub_code") or (context.get("profile") or {}).get("hub") or ""
    team = (policy.get("escalation") or {}).get("team", "Orders & Planning (L2)")
    checks: list[dict] = []
    present: list[str] = []
    ev: list[dict] = []

    def _out(action, conf, reason):
        return {"action": action, "disposition": "load_planning", "confidence": conf,
                "reason": reason, "evidence_trail": ev, "checks_run": checks,
                "evidence_present": present, "policy": policy, "hub": hub}

    # ── check 1 — is there growth data for this hub at all? ──────────────────────────
    ym, osum = (g.get("your_metrics") or {}), (g.get("order_summary") or {})
    have = bool(g.get("available")) and bool(ym) and bool(osum)
    checks.append({"id": "hub_in_growth_data",
                   "description": "The captain's hub returns data from both growth endpoints",
                   "result": f"PASS — hub {hub}" if have else
                             f"FAIL — no growth data for hub {hub or '(unknown)'}",
                   "passed": have})
    if not have:
        return _out("escalate", 0.4,
                    f"I couldn't read the Growth Dashboard for hub {hub or 'this captain'}, so I "
                    f"can't say what reduced the load. Routing to {team} to check it directly.")
    present += ["growth_your_metrics", "growth_order_summary"]
    ev.append({"label": "Growth source", "value": f"hub {hub} · {g.get('source', '?')}",
               "ref": g.get("endpoints", {}).get("order_summary", ""), "source": "growth_dashboard"})

    # ── check 2 — is the waterfall complete, and is any lever readable? ──────────────
    wf = {k: osum.get(k) for k in gc.WATERFALL}
    wf_ok = all(isinstance(v, (int, float)) for v in wf.values())
    checks.append({"id": "order_waterfall_complete",
                   "description": "All five stages of the order waterfall are readable",
                   "result": ("PASS — " + " → ".join(str(wf[k]) for k in gc.WATERFALL)) if wf_ok
                             else "FAIL — " + ", ".join(
                                 f"{k}={wf[k]!r}" for k in gc.WATERFALL
                                 if not isinstance(wf[k], (int, float))),
                   "passed": wf_ok})
    if not wf_ok:
        return _out("escalate", 0.4,
                    f"The order breakdown for hub {hub} is incomplete, so I can't attribute the "
                    f"shortfall to a stage. Routing to {team}.")
    present.append("order_waterfall")
    ev.append({"label": "Order waterfall",
               "value": " → ".join(f"{k.replace('_', ' ')} {wf[k]}" for k in gc.WATERFALL),
               "source": "growth_dashboard"})

    levers = gc.levers(ym)
    readable = [l for l in levers if l["status"] is not Tri.UNKNOWN]
    unreadable = [l["title"] for l in levers if l["status"] is Tri.UNKNOWN]
    checks.append({"id": "performance_levers_readable",
                   "description": "At least one performance lever has a readable value and target",
                   "result": f"PASS — {len(readable)}/{len(levers)} readable"
                             + (f"; unreadable: {', '.join(unreadable)}" if unreadable else "")
                             if readable else "FAIL — no lever value could be parsed",
                   "passed": bool(readable)})
    if not readable:
        # UNKNOWN is not evidence in either direction. The captain panel's own parseNumeric
        # would have called these "good" by returning 0; saying "your metrics are fine" off an
        # unreadable value is the specific failure this branch exists to avoid.
        return _out("escalate", 0.4,
                    f"None of the performance metrics for hub {hub} could be read, so I won't "
                    f"guess at the cause. Routing to {team}.")
    present.append("performance_levers")

    failing = [l for l in levers if l["status"] is Tri.NO]
    for l in failing:
        ev.append({"label": f"{l['title']} below target",
                   "value": f"{l['current']} vs target {l['target']} — {l['why']}",
                   "source": "growth_dashboard"})
    if unreadable:
        # On the record, so a reviewer sees what could NOT be checked, not just what failed.
        ev.append({"label": "Levers unreadable", "value": ", ".join(unreadable),
                   "source": "growth_dashboard"})

    # ── check 3 — which stage lost the orders? (server-side counts, read not derived) ─
    dominant, dom_n = gc.dominant_reason(osum)
    is_good = ym.get("is_good")
    checks.append({"id": "loss_attributable_to_a_stage",
                   "description": "One right-panel section carries the larger missed-order count",
                   "result": f"PASS — {dominant} ({dom_n} orders)" if dominant
                             else "FAIL — neither section carries a count",
                   "passed": bool(dominant)})
    if not dominant and is_good is not True:
        return _out("escalate", 0.4,
                    f"Hub {hub} shows a shortfall but the dashboard doesn't attribute it to "
                    f"allocation or to a capacity cut, so I can't explain the cause. Routing to {team}.")
    present.append("right_panel_counts")
    if dominant:
        rp = (osum.get("right_panel") or {}).get(dominant) or {}
        window = (f" (cut {rp.get('cut_start_date')}–{rp.get('cut_end_date')})"
                  if dominant == "capacity_loss" and rp.get("cut_start_date") else "")
        ev.append({"label": "Dominant loss stage",
                   "value": f"{dominant.replace('_', ' ')} — {dom_n} orders{window}",
                   "source": "growth_dashboard"})
    loss = osum.get("extra_earnings_loss")
    if isinstance(loss, (int, float)):
        ev.append({"label": "Earnings forgone (cycle)", "value": f"₹{int(loss)}",
                   "source": "growth_dashboard"})
    ev.append({"label": "Dashboard verdict",
               "value": f"is_good={is_good} · banner={osum.get('banner_type', '—')}"
                        + (f" · reasons: {', '.join(osum.get('reasons') or [])}"
                           if osum.get("reasons") else ""),
               "source": "growth_dashboard"})

    # ── resolve ─────────────────────────────────────────────────────────────────────
    # 0.9 on the same footing as the loss path's cn_flag branch: every field this decision
    # rests on is a value the dashboard already computed and published to the captain. There is
    # no inference to be wrong about — only a reading, and the reading is checked above.
    window = f"{ym.get('start_date', '?')}–{ym.get('end_date', '?')}"
    orders, mx = ym.get("current_orders"), ym.get("max_potential")
    money = f" That gap is worth about ₹{int(loss)} to you this cycle." if isinstance(loss, (int, float)) and loss else ""

    if is_good is True and not failing:
        return _out("respond", 0.9,
                    f"Hub {hub} received {orders} of a possible {mx} orders in {window}, and every "
                    f"performance metric is meeting its target — so there is no performance "
                    f"penalty on your load right now. Volume follows demand in your polygon, so "
                    f"it can still move week to week.")

    if dominant == "capacity_loss":
        lead = (f"Hub {hub} received {orders} of a possible {mx} orders in {window}. "
                f"{dom_n} of those were lost to a capacity cut, which is applied when "
                f"performance stays below the floor.")
    else:
        lead = (f"Hub {hub} received {orders} of a possible {mx} orders in {window}. "
                f"{dom_n} were lost before allocation — allocation is decided by hub "
                f"performance, so it is the metrics below that reduced the load.")
    if failing:
        detail = " " + " ".join(
            f"{l['title']} is {l['current']} against a target of {l['target']} ({l['why']})."
            for l in failing)
    else:
        detail = (" No individual metric is below target, so the shortfall is in allocation "
                  "volume rather than in your performance.")
    return _out("respond", 0.9, lead + detail + money)
