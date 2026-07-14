# Rollout — pilot scope, data governance (PII), and the 3-month backtest

For the engineering / data reviewers and leadership. How we ship this safely while most operational
data isn't wired in yet.

---

## 1. Pilot: start where correctness doesn't need live data

We can only call a resolution *correct* if we know the right answer. That splits by data:

- **Pilot now — data-free dispositions:** where the correct answer is a **known policy/SOP**, not a
  per-captain lookup — education, eligibility, process/procedure, "how does X work". Authors can write
  the Brains/SOPs, and we can measure accuracy immediately (see §3).
- **After the data hookup — data-dependent dispositions:** losses, payments, FE-ID — accuracy needs the
  account data to know the right answer. These wait on the Metabase → app-DB sync.

**Per-disposition autonomy is earned, riskiest last:** Shadow (engine decides in the background, human
acts, we compare) → Assist (engine drafts, human approves) → Autonomous with a ₹ cap → Full. A
disposition only climbs a rung when it clears the quality bar and holds the guardrails.

---

## 2. Data governance / PII

**Pilot exposure is low by design:** the pilot runs on demo/canned captains + data-free dispositions,
so little to no real captain PII flows through it yet.

**Controls in place / going in with this deploy:**
- **Access is gated** — login + roles (author / approver / viewer). The review link is not open; every
  action is tied to a user. Only a senior (approver) can push a change live.
- **Secrets are never in the codebase** — API keys, DB tokens, and the auth secret are injected as
  environment variables (GCP/Render secrets), not committed.
- **Least data in the model** — the engine is prompted with only what's needed to resolve a concern.

**Controls we implement before real captain data flows in (with the sync):**
- **Replicate only the fields we need**, per domain — not whole tables, not raw PII we don't use.
- **Mask identifiers** in stored traces/logs where the raw value isn't needed for audit.
- **Retention limits** on traces + logs; access to them stays behind the auth roles.
- **Data-team sign-off** on scope before anything leaves the warehouse.

Net: the deploy is access-controlled from day one; the deeper PII controls land in lockstep with the
data that would require them.

---

## 3. Testing against the last 3 months of tickets (the eval / backtest harness)

**The method:** replay historical tickets through the engine and compare its decision to the **known
human outcome** (ground truth). Per disposition, that yields:
- **Correct-resolution %** (matched the right outcome),
- **Wrong-way-reversal %** (paid out when it shouldn't have — financial-leakage guardrail),
- **Missed-escalation %** (should have escalated, didn't — safety guardrail),
- plus the flow metrics you already track (resolution %, escalation %, abandonment, reroute to L2/L3).

**How it integrates:**
- A script ingests the ticket dump (CSV export), runs each ticket through the engine, scores the result
  with the **Auditing Studio rubric**, and compares the decision to the ticket's recorded outcome →
  a **per-disposition scorecard**.
- **Now:** runnable for **data-free** dispositions immediately — gives leadership a real accuracy number
  on one theme without waiting on the sync.
- **After the sync:** the same harness extends to data-dependent dispositions once the account data
  provides ground truth.

This is the number that graduates a disposition up the autonomy ladder in §1.
