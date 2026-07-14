"""Trust gate (BRD §11): calibrated confidence + policy-as-code + Constitution.

The model proposes; policy disposes. Hard limits enforced in code OUTSIDE the
prompt: money caps and required-evidence gates. Then a calibrated confidence
threshold. Money-moving actions additionally require the adversarial verifier
(called by the pipeline). Read-only / error-correction paths clear the gate first.
"""
from __future__ import annotations

from .constitution import check_constitution

# Calibrated gate threshold. In production this is recalibrated continuously so a
# 0.9-confident decision is right ~90% of the time (BRD §11).
CONFIDENCE_THRESHOLD = 0.80


def evaluate(policy: dict, decision: dict, grounded: dict) -> dict:
    """Return a gate verdict with an explicit, auditable trail of what was checked."""
    reasons: list[str] = []
    blocks: list[str] = []

    # 1) required-evidence gate (policy-as-code)
    required = set(policy.get("required_evidence", []))
    present = set(decision.get("evidence_present", []))
    missing = required - present
    if missing:
        blocks.append(f"Missing required evidence: {', '.join(sorted(missing))}")
    else:
        reasons.append("All required evidence present")

    # 2) money cap (policy-as-code, hard limit outside the prompt)
    cap = (policy.get("resolution") or {}).get("cap_inr")
    amt = decision.get("amount_inr")
    money_moving = decision.get("action") in {"reverse_debit", "clear_pendency", "credit"}
    if money_moving and cap is not None and amt is not None and amt > cap:
        blocks.append(f"Amount ₹{amt} exceeds auto-action cap ₹{cap}")
    elif money_moving and cap is not None:
        reasons.append(f"Amount ₹{amt} within cap ₹{cap}")

    # 3) calibrated confidence gate
    conf = float(decision.get("confidence", 0.0) or 0.0)
    if conf < CONFIDENCE_THRESHOLD:
        blocks.append(f"Confidence {conf:.2f} below calibrated threshold {CONFIDENCE_THRESHOLD:.2f}")
    else:
        reasons.append(f"Confidence {conf:.2f} clears threshold {CONFIDENCE_THRESHOLD:.2f}")

    # 4) Partner Constitution
    con = check_constitution(policy, decision, grounded)
    if not con["passed"]:
        blocks += con["violations"]
    reasons += [f"Upholds: {u}" for u in con["upheld"]]

    passed = len(blocks) == 0
    return {
        "passed": passed,
        "money_moving": money_moving,
        "confidence": conf,
        "threshold": CONFIDENCE_THRESHOLD,
        "reasons": reasons,
        "blocks": blocks,
        "constitution": con,
        # money-moving + passed => pipeline must run the adversarial verifier next
        "requires_adversarial_verify": passed and money_moving,
    }
