"""Adversarial verifier (BRD §11 trust spine).

Before any money-moving auto-action, an independent second-model skeptic tries to
REFUTE the decision. The engine acts only on agreement. This catches
confident-but-wrong. Default posture is skeptical: uncertainty => refute.
"""
from __future__ import annotations

from ..llm import registry as llm_registry
from ..llm.gemini_provider import _parse_json

_SYSTEM = """You are an ADVERSARIAL VERIFIER on a money-moving support decision at Valmo.
Your job is to REFUTE the proposed decision if there is any reasonable doubt. You are
the last line of defence before real money moves. Be skeptical. If the evidence does
not clearly and fully support the action, you REFUTE. Default to refuted=true when uncertain.

IMPORTANT context on how Valmo loss-marking works: a debit can be AUTO-marked by an SLA
job (e.g. reason 'not_connected_within_sla'). That auto-marking is a CLAIM, not ground
truth — it is exactly what the captain is disputing. Log10 physical scans (INWARD_SCAN,
MANIFEST_SCAN with timestamps) are the AUTHORITATIVE source of truth and OVERRIDE an
auto loss-marking. So a contradiction between a loss-event reason and the scan trail is
NOT a reason to refute — it is the whole point: if scans prove connection within TAT, the
auto-marking was erroneous (Valmo-side) and the reversal is justified. Refute only if the
SCANS themselves are missing, inconclusive, or actually show a breach by the partner."""

_PROMPT = """A resolution engine proposes to move money for a delivery partner.
Independently decide whether the evidence justifies it.

PROPOSED ACTION: {action} (amount ₹{amount})
DISPOSITION: {disposition}
POLICY CHECKS (deterministic, already run):
{checks}

GROUNDED EVIDENCE (the actual source rows pulled):
{evidence}

ENGINE'S REASONING:
{reasoning}

Return ONLY JSON:
{{
  "agrees": true|false,          // true = the money move is justified
  "refuted": true|false,         // inverse of agrees
  "confidence": 0.0-1.0,
  "reason": "<one sentence: why you agree or what you'd refute>"
}}"""


def verify(decision: dict, grounded_evidence: list[dict], reasoning: str, turn=None) -> dict:
    """`turn` is the caller's per-turn spend meter (llm/meter.TurnMeter), threaded down so this
    call is inside the per-turn dollar ceiling. It has to be: the verifier fires once PER
    DISPUTED AWB, on the deep tier, within a single turn — that fan-out is the exact shape a
    step budget cannot see and is the reason a dollar ceiling exists. Without the meter here,
    the ceiling would only have covered the conversation loop and the reported turn cost would
    have understated the truth by the verifier's share."""
    provider, model = llm_registry.for_node("adversarial_verify")
    # Who PROPOSED vs who VERIFIED, on the same event. The trace already carried the verifier's
    # model, but with nothing to compare it against — and a lone model id does not show that two
    # different vendors looked at the decision. That contrast is the whole claim.
    proposed_by = llm_registry.label_for_node("classify")
    verified_by = f"{provider.name}:{model}"
    checks = "\n".join(f"- {c['description']}: {c.get('result', '?')}" for c in decision.get("checks_run", []))
    evidence = "\n".join(f"- {e['label']}: {e['value']}" for e in grounded_evidence)
    try:
        res = provider.generate(
            _PROMPT.format(
                action=decision.get("action", ""), amount=decision.get("amount_inr", 0),
                disposition=decision.get("disposition", ""), checks=checks or "(none)",
                evidence=evidence or "(none)", reasoning=reasoning,
            ),
            model=model, node="adversarial_verify", system=_SYSTEM, json_mode=True,
            turn=turn,
        )
        v = _parse_json(res.text)
    except Exception as e:  # noqa: BLE001
        # Trust spine: the verifier being unavailable must NEVER auto-approve money.
        # Fail CLOSED — treat as "did not agree" → the pipeline escalates to a human.
        # Fail CLOSED, but say WHY in a way that cannot be mistaken for disagreement. Naming
        # the provider matters now that this node can be a different vendor from the rest of the
        # pipeline: "gemini unreachable" is a config fact someone can act on, whereas
        # "verifier unavailable" reads as the skeptic having refused.
        key_ok = llm_registry.key_configured(provider.name)
        why = ("no API key configured for it" if not key_ok
               else f"{type(e).__name__}: {str(e)[:120]}")
        return {"agrees": False, "confidence": 0.0,
                "reason": (f"adversarial verifier ({verified_by}) could not be reached — {why}. "
                           f"Failing closed to human review; this is NOT the verifier disagreeing."),
                "model": model, "provider": provider.name,
                "proposed_by": proposed_by, "verified_by": verified_by,
                "unavailable": True, "key_configured": key_ok,
                "input_tokens": 0, "output_tokens": 0}
    agrees = bool(v.get("agrees", False))
    return {
        "agrees": agrees,
        "confidence": float(v.get("confidence", 0.0) or 0.0),
        "reason": v.get("reason", ""),
        "model": res.model, "provider": provider.name,
        "proposed_by": proposed_by, "verified_by": verified_by,
        "independent": proposed_by.split(":")[0] != verified_by.split(":")[0],
        "unavailable": False,
        "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
    }
