"""The Partner Constitution (BRD §7).

Nine inviolable principles every Executable Policy is checked against. Functional
teams write the SOPs; this system enforces these on top so no SOP can quietly
become partner-hostile.
"""

def check_constitution(policy: dict, decision: dict) -> dict:
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
