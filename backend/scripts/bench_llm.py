"""Benchmark the ACTIVE LLM across every pipeline node — accuracy AND performance.

    cd backend && . scripts/load_env.sh && python scripts/bench_llm.py

Why per-node: models.yaml routes each node to a tier, and a stop-gap model can be perfectly
adequate for one node (composing a reply) and unusable for another (strict-JSON SOP compilation).
Averaging one score over the whole pipeline hides exactly that. Each check below has a
deterministic pass condition — no LLM grades another LLM here, so the result is reproducible.

Measures per node: latency, whether the output is structurally VALID (parses + has the required
shape), and a correctness probe where one exists (e.g. does the adversarial verifier actually
reject an unfunded payout; does the judge fail a reply that ignores the partner's question).

Nothing is persisted. Results print as a table and are written to data/llm_bench.json.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm import registry                      # noqa: E402
from app.llm.gemini_provider import _parse_json   # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "llm_bench.json"


def _call(node: str, prompt: str, system: str, json_mode: bool) -> tuple[float, str, str]:
    """Returns (seconds, text, error)."""
    prov, model = registry.for_node(node)
    t0 = time.time()
    try:
        res = prov.generate(prompt, model=model, node=node, system=system, json_mode=json_mode)
        return round(time.time() - t0, 2), getattr(res, "text", str(res)), ""
    except Exception as e:  # noqa: BLE001
        return round(time.time() - t0, 2), "", f"{type(e).__name__}: {str(e)[:90]}"


# ── the checks: (node, label, system, prompt, validator) ─────────────────────
def _v_json_keys(*keys):
    def v(txt):
        d = _parse_json(txt)
        if isinstance(d, list) and d:
            d = d[0]
        if not isinstance(d, dict):
            return False, "not a JSON object"
        miss = [k for k in keys if k not in d]
        return (not miss), (f"missing {miss}" if miss else "ok")
    return v


def _v_verdict_fail(txt):
    """The judge must FAIL a reply that ignores what the partner actually asked."""
    d = _parse_json(txt)
    if isinstance(d, list) and d:
        d = d[0]
    if not isinstance(d, dict):
        return False, "not JSON"
    pd = d.get("per_dimension") or {}
    if not pd:
        return False, "no per_dimension"
    hit = str((pd.get("fatal_incomplete_info") or {}).get("verdict", "")).lower()
    return (hit == "fail"), f"fatal_incomplete_info={hit or 'absent'} (want fail)"


def _v_reject(txt):
    """The adversarial verifier must NOT approve a payout with no supporting evidence."""
    d = _parse_json(txt)
    if isinstance(d, list) and d:
        d = d[0]
    if not isinstance(d, dict):
        return False, "not JSON"
    ok = d.get("approve", d.get("approved", d.get("verdict")))
    approved = str(ok).lower() in ("true", "yes", "approve", "approved")
    return (not approved), f"approve={ok} (want reject)"


def _v_hinglish(txt):
    t = (txt or "").strip()
    if len(t) < 20:
        return False, "too short"
    if "{" in t[:2]:
        return False, "returned JSON, not prose"
    return True, f"{len(t)} chars"


CHECKS = [
    ("intent", "Intent + entity extraction",
     "Extract structured intent. Strict JSON only.",
     'Captain says: "mera 1860 ka paisa kaat liya hardstop bolke, AWB VL0093310077 delivered tha". '
     'Return json: {"intent":"<short>","entities":{"awb":"<awb or null>","amount_inr":<num or null>}}',
     _v_json_keys("intent", "entities"), True),

    ("classify", "Disposition classification",
     "Classify the issue. Strict JSON only.",
     'Issue: "loss marked on me but shipment was delivered". Choose ONE disposition from '
     '["hardstop_loss","shortage_loss","cod_shortfall","payment_not_received","technical_issue"]. '
     'Return json: {"disposition":"<one of the list>","confidence":<0-1>}',
     _v_json_keys("disposition", "confidence"), True),

    ("policy_reasoning", "Money-touching judgement",
     "You decide whether to reverse a debit. Strict JSON only.",
     'SOP: reverse the debit ONLY IF facility_inscan exists AND loss_percentage is 100%. '
     'Data: facility_inscan="2026-06-25", loss_percentage="100%", amount=1860, cap=2000. '
     'Return json: {"action":"reverse_debit|escalate","amount_inr":<num>,"reason":"<one line>"}',
     _v_json_keys("action", "reason"), True),

    ("adversarial_verify", "Adversarial verifier (must reject)",
     "You are a skeptical verifier. Reject anything not fully evidenced. Strict JSON only.",
     'A decision proposes paying a captain Rs.5000. Evidence provided: NONE — no AWB, no scan '
     'record, no loss row, no SOP reference. Return json: {"approve":<true|false>,"reason":"<why>"}',
     _v_reject, True),

    ("explain", "Grounded Hinglish reply",
     "Reply to the delivery partner warmly in simple Hinglish. Plain prose, no JSON.",
     "Tell the captain their Rs.1860 hardstop debit has been reversed because the shipment was "
     "scanned at the facility, and the credit appears in the next payout cycle.",
     _v_hinglish, False),

    ("sop_compile", "SOP -> executable policy",
     "You compile SOPs into strict JSON policies. Strict JSON only.",
     'Compile this SOP: "If a captain reports a COD shortfall, verify the amount entered at DRS '
     'closure, then send the reversal form. Reactivate the FE ID within 2-3 days of credit." '
     'Return json: {"title":"<t>","checks":[{"description":"<c>"}],'
     '"resolution":{"action":"<a>"},"escalation":{"team":"<t>"}}',
     _v_json_keys("title", "checks", "resolution"), True),

    ("audit_judge", "Audit judge (must fail an evasive reply)",
     "You are a QA auditor. Strict JSON only.",
     'PARTNER: "My account was ON HOLD so the credit never arrived — bank statement attached. '
     'Please check and resolve." AGENT: "Your payment succeeded with UTR AXIS123. Please verify '
     'your bank account." The agent ignored the account-hold problem entirely. '
     'Return json: {"per_dimension":{"fatal_incomplete_info":{"verdict":"pass|fail",'
     '"rationale":"<one line>"}},"overall_rationale":"<2 sentences>"}',
     _v_verdict_fail, True),

    ("monitor_compose", "Proactive nudge",
     "Compose a short proactive message to a delivery partner. Plain prose, no JSON.",
     "3 shipments at this captain's hub are 6 days past inscan with no connect scan and will "
     "auto-hardstop tomorrow. Warn them and say what to do.",
     _v_hinglish, False),
]


def main() -> int:
    label = registry.active_model_label()
    prov = registry.provisional_label()
    print(f"LLM: {label}")
    if prov:
        print(f"  PROVISIONAL bridge ({prov}) — results are for measurement only, never published.")
    print()
    print(f"{'node':20} {'check':38} {'sec':>6} {'valid':>6}  detail")
    print("-" * 100)

    rows = []
    for node, lbl, system, prompt, validator, json_mode in CHECKS:
        sec, txt, err = _call(node, prompt, system, json_mode)
        if err:
            ok, detail = False, err
        else:
            try:
                ok, detail = validator(txt)
            except Exception as e:  # noqa: BLE001
                ok, detail = False, f"validator {type(e).__name__}"
        print(f"{node:20} {lbl:38} {sec:>6} {'PASS' if ok else 'FAIL':>6}  {detail[:44]}")
        rows.append({"node": node, "check": lbl, "seconds": sec, "valid": ok,
                     "detail": detail[:200], "error": err})

    n = len(rows)
    passed = sum(1 for r in rows if r["valid"])
    lat = [r["seconds"] for r in rows if not r["error"]]
    print("-" * 100)
    print(f"passed {passed}/{n}  |  median latency "
          f"{sorted(lat)[len(lat)//2] if lat else '-'}s  |  total {round(sum(r['seconds'] for r in rows),1)}s")
    failed = [r["node"] for r in rows if not r["valid"]]
    if failed:
        print(f"UNUSABLE for: {', '.join(failed)}")

    OUT.write_text(json.dumps({"llm": label, "provisional": bool(prov),
                               "passed": passed, "total": n, "checks": rows}, indent=1))
    print(f"\nwrote {OUT.name}")
    return 0 if passed == n else 1


if __name__ == "__main__":
    sys.exit(main())
