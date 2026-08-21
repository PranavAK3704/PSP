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

# The actions that move a captain's money. Three things key off this set — the money cap,
# `requires_adversarial_verify`, and the simulated-write outcome in engine/tools.py — so it is a
# named constant rather than an inline literal. Losing a member here silently disables the
# adversarial verifier for that action, which is the quietest way to break the trust spine.
#
# `raise_for_reversal`, NOT `reverse_debit`. The engine cannot reverse anything: there is no HTTP
# write endpoint for a loss adjustment anywhere in the stack (reversal is a Kafka message
# consumed by LMS's own scheduler — see engine/write_mode.py). The old name asserted an action
# the system has never been able to take, and it leaked all the way to the captain, where the UI
# rendered "✓ Debit reversed in-conversation" over a decision that had written nothing.
#
# It stays in MONEY_ACTIONS despite writing nothing, because what the set really gates is "does
# this decision need the adversarial verifier and the cap check" — and a recommendation that L2
# will act on needs both exactly as much as a write would.
MONEY_ACTIONS = frozenset({"raise_for_reversal", "clear_pendency", "credit"})

# Historical rows carry the old name. Anything READING a stored action must canonicalise first,
# or three years of ledger stats silently start excluding every pre-rename reversal.
LEGACY_ACTION_ALIASES = {"reverse_debit": "raise_for_reversal"}


def canonical_action(action: str | None) -> str:
    """Map a stored action name to its current one. Identity for anything already current."""
    a = (action or "").strip()
    return LEGACY_ACTION_ALIASES.get(a, a)


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
    money_moving = canonical_action(decision.get("action")) in MONEY_ACTIONS
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
    con = check_constitution(policy, decision)
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
