"""Assertions for the write path. NO LLM CALLS, NO NETWORK.

    python scripts/check_writes.py

The thing being guarded: `tools._act()` used to return `applied: True` while performing no I/O
at all, and the trace said "ACT — idempotent write". Both were false, and neither could be
made true — LossManagementService has no HTTP write endpoint for a loss adjustment or reversal
(reversal is a Kafka message consumed by its own scheduler cron).

So these checks are about the honesty of a claim, not the behaviour of a feature. They fail if
the engine ever again tells anyone that money moved.

Exit code 0 = clean.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts._contain import contain; contain()   # MUST precede every `app.` import — see _contain.py
from scripts._harness import FAILED, check, head   # noqa: E402



# Words that must never reach a captain while nothing is written. Present tense / past tense
# claims of completed payment — the failure mode is a reply saying "₹244 has been reversed".
_CLAIMS = ("has been reversed", "have been reversed", "has been credited", "have been credited",
           "has been refunded", "money has been", "amount has been", "reversed successfully",
           "credited successfully", "payment has been made")


def main() -> int:
    from app.engine import tools, write_mode
    from app.trust import gate as trust_gate

    saved = dict(os.environ)
    try:
        # ── 1. the flag ──────────────────────────────────────────────────────────
        head("[1] WRITE_MODE — defaults safe, unknown values fall back to simulated")
        for raw, want in ((None, "simulated"), ("", "simulated"), ("simulated", "simulated"),
                          ("SIMULATED", "simulated"), (" Live ", "live"), ("live", "live"),
                          ("yes", "simulated"), ("true", "simulated"), ("garbage", "simulated")):
            os.environ.pop("WRITE_MODE", None)
            if raw is not None:
                os.environ["WRITE_MODE"] = raw
            got = write_mode.mode()
            check(f"WRITE_MODE={raw!r} → {got}", got == want, f"expected {want}")
        os.environ.pop("WRITE_MODE", None)
        check("a typo can never resolve to 'live'",
              write_mode.mode() == "simulated",
              "an unrecognised value deciding that money moves is the one unacceptable default")

        # ── 2. live refuses, and says why ────────────────────────────────────────
        head("[2] live is accepted and REFUSES — a silent no-op would be the real bug")
        os.environ["WRITE_MODE"] = "live"
        raised = None
        try:
            write_mode.assert_writable()
        except write_mode.WriteNotAvailable as e:
            raised = str(e)
        check("assert_writable() raises under live", raised is not None)
        if raised:
            for term in ("Kafka", "no producer", "scheduler"):
                check(f"the reason names '{term}'", term.lower() in raised.lower())
        # _act must NOT raise — it reports refusal as data. It used to raise here, and because
        # the call sits AFTER the verifier has agreed and BEFORE concern_log.append, the raise
        # discarded the concern, the trace and the money already spent on the verifier call.
        # Only successful reversals vanished; escalations were fine. A case-shredder.
        r = tools._act({"action": "raise_for_reversal", "amount_inr": 244, "debit_id": "D1"})
        check("_act() does NOT raise under live — it refuses as DATA",
              isinstance(r, dict) and r.get("blocked") is True,
              "raising here discarded the captain's case entirely")
        check("   the refusal is not mislabelled as simulated", r.get("simulated") is False)
        check("   and it says the case is preserved", "preserved" in r.get("detail", ""))
        # A non-money action must NOT raise under live — there is nothing to write, so there is
        # nothing to refuse. Raising here would break every ordinary conversational resolution.
        try:
            r = tools._act({"action": "respond"})
            check("_act(respond) does NOT raise under live", r.get("simulated") is False,
                  "nothing to write, so nothing to refuse")
        except Exception as e:  # noqa: BLE001
            check("_act(respond) does NOT raise under live", False, f"{type(e).__name__}")
        os.environ["WRITE_MODE"] = "simulated"

        # ── 3. the return shape ──────────────────────────────────────────────────
        head("[3] _act() — `applied` is GONE, not False")
        money = sorted(trust_gate.MONEY_ACTIONS)
        for a in money:
            r = tools._act({"action": a, "amount_inr": 244, "debit_id": "D1"})
            check(f"{a:15} simulated=True", r.get("simulated") is True)
            check(f"{a:15} 'applied' key absent", "applied" not in r,
                  "absent, so a stale `act.get('applied')` reader degrades falsy")
            check(f"{a:15} carries would_have", bool(r.get("would_have")), r.get("would_have", ""))
            check(f"{a:15} detail says NOT WRITTEN", "NOT WRITTEN" in r.get("detail", ""))
            check(f"{a:15} no past-tense claim in detail",
                  not any(c in r.get("detail", "").lower() for c in _CLAIMS))
        r = tools._act({"action": "respond"})
        check("respond          simulated=False", r.get("simulated") is False,
              "nothing was ever going to be written, so 'simulated' would be its own inaccuracy")
        check("respond          'applied' key absent", "applied" not in r)

        # ── 4. the outcome mapping, through the REAL _apply_policy ───────────────
        head("[4] outcome mapping — a new value only where a write was implied")
        from app.engine import policy_exec
        from app.ledger import concern_log
        from app.trust import verifier

        appended: list[dict] = []
        orig_append, orig_exec, orig_verify = (
            concern_log.append, policy_exec.execute, verifier.verify)
        tools.concern_log.append = lambda c: (appended.append(c), {**c, "id": c["id"]})[1]
        tools.verifier.verify = lambda *a, **k: {
            "agrees": True, "confidence": 0.95, "reason": "stub", "model": "stub",
            "provider": "stub", "input_tokens": 0, "output_tokens": 0}

        POLICY = {"id": "pol_x", "version": "v1", "required_evidence": [],
                  "resolution": {"action": "raise_for_reversal", "cap_inr": 5000},
                  "escalation": {"team": "Losses & Debits (L2)"}, "partner_rights": []}

        def fake(action, conf):
            return {"action": action, "disposition": "hardstop_loss", "confidence": conf,
                    "amount_inr": 244, "debit_id": "D1", "awb": "VL0000000000001",
                    "reason": "Your ₹244 debit is being reversed.",
                    "evidence_trail": [{"label": "Loss record", "value": "hardstop"}],
                    "checks_run": [], "evidence_present": [], "policy": POLICY}

        cases = [("raise_for_reversal", 0.92, "simulated_resolution"),
                 ("respond", 0.90, "resolved_in_conversation"),
                 ("escalate", 0.40, "escalated")]
        for action, conf, want_outcome in cases:
            appended.clear()
            tools.policy_exec.execute = lambda *a, _d=(action, conf), **k: fake(*_d)
            result, events, concern, taken = tools.dispatch(
                "apply_policy", {"disposition": "hardstop_loss", "awb": "VL0000000000001"},
                "P1", {"captain_id": "P1", "profile": {}, "ledger": [], "losses": [],
                       "cash": {}, "shipments": []})
            check(f"{action:14} → outcome {concern['outcome']}",
                  concern["outcome"] == want_outcome, f"expected {want_outcome}")
            check(f"{action:14} → action_taken unchanged ({concern['action_taken']})",
                  concern["action_taken"] == ("escalate" if action == "escalate" else action),
                  "concern_log.stats() branches on action_taken, so counts stay correct")
            check(f"{action:14} → write_mode stamped", concern.get("write_mode") == "simulated")
            check(f"{action:14} → model told money_moved=False", result.get("money_moved") is False)
            blob = json.dumps(result, ensure_ascii=False).lower()
            hits = [c for c in _CLAIMS if c in blob]
            check(f"{action:14} → no completed-payment claim to the model", not hits, str(hits))
            act_evts = [e for e in events if e["node"] == "act"]
            if action in trust_gate.MONEY_ACTIONS:
                check(f"{action:14} → ACT event label is honest",
                      bool(act_evts) and "simulated write" in act_evts[0]["label"],
                      act_evts[0]["label"] if act_evts else "no act event")
                check(f"{action:14} → ACT status=blocked",
                      bool(act_evts) and act_evts[0]["status"] == "blocked",
                      "so the existing dot styling renders it as not-done")
                check(f"{action:14} → relay names the owning team",
                      "Losses & Debits (L2)" in (result.get("reason") or ""))
            elif action == "respond":
                check("respond        → ACT label carries no write claim",
                      bool(act_evts) and "no write required" in act_evts[0]["label"],
                      act_evts[0]["label"] if act_evts else "no act event")

        # ── 4b. THE GAP THE REVIEW FOUND: what does `live` do END TO END? ───────
        # NOTE the stubs from [4] are STILL INSTALLED here, deliberately. An earlier version of
        # this section ran after they were restored, so it exercised the real concern_log and
        # wrote a junk row into data/concern_log.json — a harness that mutates production state
        # is worse than a missing harness.
        # Section [2] only proved `_act` refuses in isolation, then section [4] ran with
        # WRITE_MODE=simulated — so the harness certified "live refuses" while never checking
        # what the refusal did to the concern log or the trace. That is precisely where the
        # case-shredder hid.
        head("[4b] WRITE_MODE=live end to end — the case must survive")
        os.environ["WRITE_MODE"] = "live"
        appended.clear()
        tools.policy_exec.execute = lambda *a, **k: fake("raise_for_reversal", 0.92)
        result, events, concern, taken = tools.dispatch(
            "apply_policy", {"disposition": "hardstop_loss", "awb": "VL0000000000001"},
            "P1", {"captain_id": "P1", "profile": {}, "ledger": [], "losses": [],
                   "cash": {}, "shipments": []})
        check("the concern is still LOGGED", concern is not None and len(appended) == 1,
              f"{len(appended)} logged — 0 means the captain's dispute vanished")
        check("the trace still reaches the panel", len(events) >= 4,
              f"{len(events)} events — POLICY/GATE/VERIFY must not be discarded")
        act_evts = [e for e in events if e["node"] == "act"]
        check("an ACT event says BLOCKED", bool(act_evts) and "BLOCKED" in act_evts[0]["label"],
              act_evts[0]["label"] if act_evts else "no act event")
        check("it escalates rather than claiming success", taken == "escalate",
              f"action_taken={taken}")
        check("the captain gets a real answer, not a tool error",
              bool(result.get("reason")) and "WriteNotAvailable" not in str(result),
              (result.get("reason") or "")[:60])
        check("no LMS/Kafka internals leak to the captain",
              not any(w in (result.get("reason") or "") for w in ("Kafka", "LossManagement", "LMS")),
              "the tool-error path used to paraphrase service internals into the reply")
        os.environ["WRITE_MODE"] = "simulated"
        # Stubs restored only now that BOTH sections that need them have run.
        tools.concern_log.append, tools.policy_exec.execute, tools.verifier.verify = (
            orig_append, orig_exec, orig_verify)

        # ── 5. the money set is one constant ────────────────────────────────────
        head("[5] MONEY_ACTIONS is a single source of truth")
        import inspect
        gsrc = inspect.getsource(trust_gate.evaluate)
        check("gate.evaluate() uses the constant, not a literal",
              "MONEY_ACTIONS" in gsrc and '{"reverse_debit"' not in gsrc)
        check("tools._act() keys off the same constant",
              "trust_gate.MONEY_ACTIONS" in inspect.getsource(tools._act))
        # A constant compared to a copy of itself proves nothing. What matters is that the
        # rename held IN LOCKSTEP: the verifier must still fire on the reversal slice, a stored
        # row under the OLD name must still count, and policy_exec must not still be comparing
        # against a literal the policy no longer contains. Every one of those fails silently.
        check("the money set uses the honest name",
              "raise_for_reversal" in trust_gate.MONEY_ACTIONS
              and "reverse_debit" not in trust_gate.MONEY_ACTIONS,
              ", ".join(sorted(trust_gate.MONEY_ACTIONS)))
        check("a legacy stored action still canonicalises",
              trust_gate.canonical_action("reverse_debit") == "raise_for_reversal",
              "history predates the rename; matching only the new name would drop it from stats")
        check("a current action is unchanged by canonicalisation",
              trust_gate.canonical_action("raise_for_reversal") == "raise_for_reversal"
              and trust_gate.canonical_action("respond") == "respond")
        _pol = {"resolution": {"cap_inr": 5000}}
        _dec = {"amount_inr": 100, "confidence": 0.95, "evidence_present": []}
        v = trust_gate.evaluate(_pol, {**_dec, "action": "raise_for_reversal"}, {})
        check("the verifier still fires on the renamed action",
              v["money_moving"] and v["requires_adversarial_verify"],
              "a rename that misses gate.py silently disables the skeptic")
        v2 = trust_gate.evaluate(_pol, {**_dec, "action": "reverse_debit"}, {})
        check("   and on a legacy action name too", v2["requires_adversarial_verify"],
              "a replayed historical decision must not skip the verifier")
        _pe = inspect.getsource(policy_exec)
        check("policy_exec compares action_kind against the CURRENT name",
              'action_kind == "reverse_debit"' not in _pe,
              "action_kind is read from the policy, which the rename also changed — a stale "
              "literal here makes the reversal branch unreachable with no error anywhere")
        check("the honest name reaches the captain-facing UI",
              "Debit reversed in-conversation" not in
              (Path(ROOT).parent / "frontend/src/pages/CaptainPanel.jsx").read_text(),
              "the UI asserted a completed payment over a decision that wrote nothing")
    finally:
        os.environ.clear()
        os.environ.update(saved)

    print(f"\n{'═' * 78}")
    if FAILED:
        print(f"FAILED {len(FAILED)}:")
        for f in FAILED:
            print(f"  · {f}")
        return 1
    print("WRITE PATH VERIFIED — nothing claims to have written anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
