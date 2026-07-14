"""Deterministic policy execution (BRD §4.3 — execute the compiled policy).

Given a disposition, the grounded Captain Context, and extracted entities, this
runs the Executable Policy's checks in CODE (never the LLM) and returns a decision
with an evidence trail. This is where determinism is mandatory.
"""
from __future__ import annotations

from ..knowledge import policies as pol
from ..substrate import captain_context as ctx
from ..substrate import loss_db


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
            return _eval_real_loss(row, policy, awb, pend, attrib, led)

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
                    attrib: dict | None = None, led: dict | None = None) -> dict:
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
    reversal_signal = bool(inscan) or attr_changed or cn_issued

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

    scans = ctx.get_scans(cap_id, awb)
    loss = next((l for l in context.get("losses", []) if l.get("awb") == awb), None)

    if scans:
        present.append("scan_trail")
        last = scans["events"][-1] if scans.get("events") else {}
        evidence_trail.append({
            "label": "AWB scan trail",
            "value": f"{awb}: in-scan {scans.get('inscan_date')} at {scans.get('hub')}; "
                     f"{len(scans.get('events', []))} scans; connected_within_TAT="
                     f"{scans.get('connected_within_tat')}",
            "ref": f"log10_scans#{awb}", "source": "get_shipment_scan_history"})
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
    connected = bool(scans and scans.get("connected_within_tat"))
    sop_followed = bool(scans and scans.get("hardstop_sop_followed", True))
    if scans is None:
        c2_result, c2_pass, attributable = "INCONCLUSIVE — no scan trail", False, None
    elif connected and sop_followed:
        c2_result = "FAILS Valmo-side — in-scan within TAT & hardstop SOP followed → NOT partner's fault"
        c2_pass, attributable = True, False
    else:
        c2_result = "Scans show breach attributable to partner"
        c2_pass, attributable = False, True
    checks_run.append({"id": "attributable_to_partner",
                       "description": "Is the loss attributable to the partner?",
                       "result": c2_result, "passed": c2_pass})

    # ── Check 3: within reversal cap ──
    cap = policy["resolution"]["cap_inr"]
    c3_ok = amount is not None and amount <= cap
    checks_run.append({"id": "within_reversal_cap", "description": f"Debit ₹{amount} within cap ₹{cap}",
                       "result": "PASS" if c3_ok else "EXCEEDS CAP", "passed": c3_ok})

    # Decision: reverse only if all deterministic checks pass and loss is NOT the partner's fault.
    if c1_ok and c2_pass and c3_ok and attributable is False:
        reason = (
            f"The ₹{amount} debit was AUTO-marked by the SLA job as '{(loss or {}).get('reason_l1', 'not_connected')}'. "
            f"However Log10 scans — the authoritative source of truth — show INWARD_SCAN on "
            f"{scans.get('inscan_date')} and a forward MANIFEST_SCAN within the LM-forward D5 TAT "
            f"(connected_within_TAT=True, hardstop SOP followed). The auto-marking is therefore "
            f"erroneous and Valmo-side; per SOP HS_1_1 this debit is not attributable to the partner "
            f"and must be reversed."
        )
        return {"action": "reverse_debit", "disposition": "hardstop_loss",
                "amount_inr": amount, "awb": awb, "debit_id": debit["id"],
                "confidence": 0.93, "scenario": "HS_1_1",
                "reason": reason,
                "evidence_trail": evidence_trail, "checks_run": checks_run,
                "evidence_present": present, "policy": policy}

    # Ambiguous / inconclusive → escalate with the fully-worked case (never guess).
    return {"action": "escalate", "disposition": "hardstop_loss",
            "amount_inr": amount, "awb": awb, "debit_id": debit["id"],
            "confidence": 0.45,
            "reason": "Checks could not be fully satisfied (scans inconclusive or breach attributable) "
                      "— escalating the worked case to the Losses & Debits team.",
            "evidence_trail": evidence_trail, "checks_run": checks_run,
            "evidence_present": present, "policy": policy}
