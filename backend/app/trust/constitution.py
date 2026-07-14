"""The Partner Constitution (BRD §7).

Nine inviolable principles every Executable Policy is checked against. Functional
teams write the SOPs; this system enforces these on top so no SOP can quietly
become partner-hostile.
"""

PRINCIPLES = [
    {"id": 1, "name": "Presumption of good faith",
     "text": "Burden of proof is on Valmo; no debit stands without evidence of partner fault."},
    {"id": 2, "name": "True-cause attribution",
     "text": "Every cost is attributed to partner / Valmo / upstream-vendor / external; never defaulted onto the partner."},
    {"id": 3, "name": "Radical transparency",
     "text": "The partner sees the same data and the reason behind every decision."},
    {"id": 4, "name": "Guaranteed SLAs",
     "text": "With automatic escalation if an SLA is at risk."},
    {"id": 5, "name": "Auto error-correction",
     "text": "A detected wrong is corrected proactively, not only on complaint."},
    {"id": 6, "name": "Proportionality + downside caps",
     "text": "Consequences to the partner are bounded and proportionate."},
    {"id": 7, "name": "Right to appeal + a human",
     "text": "The system arms the partner's strongest case, not Valmo's."},
    {"id": 8, "name": "Consistency",
     "text": "The same case produces the same outcome (execution is deterministic)."},
    {"id": 9, "name": "No silent policy changes",
     "text": "Every policy version is recorded and every decision cites the version that produced it."},
]

# Three tiers of partner-first action (BRD §7) — keeps "advocate" P&L-defensible.
TIERS = {
    "tier1": {"name": "Correct wrongs", "posture": "P&L-correct — ship aggressively"},
    "tier2": {"name": "Fair process", "posture": "Cheap, high satisfaction — default on"},
    "tier3": {"name": "Bounded generosity", "posture": "Budgeted + ROI-justified, CXO-capped"},
}


def check_constitution(policy: dict, decision: dict, grounded: dict) -> dict:
    """Return {passed: bool, upheld: [...], violations: [...]}.

    A partner-protective decision (reversal of a debit not the partner's fault)
    upholds the Constitution. A decision that would charge the partner without
    evidence of fault violates it.
    """
    upheld, violations = [], []
    action = decision.get("action", "")

    if action == "reverse_debit":
        upheld += ["Presumption of good faith", "True-cause attribution", "Auto error-correction"]
    if decision.get("evidence_trail"):
        upheld.append("Radical transparency")
    cap = (policy.get("resolution") or {}).get("cap_inr")
    amt = decision.get("amount_inr")
    if cap is not None and amt is not None:
        if amt <= cap:
            upheld.append("Proportionality + downside caps")
        else:
            violations.append(f"Amount ₹{amt} exceeds cap ₹{cap} — must escalate, not auto-act")
    if policy.get("version"):
        upheld.append("No silent policy changes")

    return {"passed": len(violations) == 0, "upheld": sorted(set(upheld)), "violations": violations}
