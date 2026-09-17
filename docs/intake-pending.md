# Intake — the pending queue

**One ordered list. Work it top to bottom; each item says who owns it, what it unblocks, and
what "done" looks like.** Living document — updated as things land, so nothing important lives
only in a chat log.

Last updated: **2026-09-17**

---

## The one number that matters right now

Held-out disposition precision is **65.2%** — and the interesting part is *why*, because it
changes what you should do about it.

| Cut | Precision | What it says |
|---|---|---|
| 14 dispositions as they stand | **65.2%** | the headline |
| The 5 money classes merged into 1 | **87.0%** | **two-thirds of the error is one problem** |

62% of all errors never leave the money cluster — `payment_not_received`, `hardstop_loss`,
`cod_pendency`, `cod_shortfall`, `shortage_loss`. The confusion is *symmetric* (15 `hardstop_loss`
→ `payment_not_received`, 10 the other way), which is the signature of classes that are not
separable from the message text at all, rather than a classifier that is simply weak.

Per class it is bimodal — there is no "65% system", there are two groups:

| Disposition | Precision | n | |
|---|---|---|---|
| `load_planning` | 96.7% | 30 | ✅ usable today |
| `cod_shortfall` | 86.8% | 38 | ✅ usable today |
| `technical_issue` | 78.8% | 33 | ✅ usable today |
| `payment_not_received` | 69.9% | 83 | ⚠️ borderline |
| `hardstop_loss` | 31.2% | 32 | ❌ |
| `capacity_panel_issue` | 28.6% | 14 | ❌ |
| `consumables_order` | 28.6% | 7 | ❌ |
| `shortage_loss` | 20.0% | 5 | ❌ |
| `cod_pendency` | 18.2% | 11 | ❌ |

**And the reference is not truth.** These are *silver* labels — what the existing engine's own
LLM classifier assigned. Eyeballing the disagreements, a real share are the silver label being
wrong, not the matcher:

- *"16 March to 29 March ka peyment nahi aaya hai account me"* → silver says
  `capacity_panel_issue`, matcher says `payment_not_received`. **The matcher is right.**
- *"...regarding the COD shortfall deduction..."* → silver says `cod_pendency`, matcher says
  `cod_shortfall`. **The matcher is right.**
- *"MERA IS RIDER KA ROLE BRANCH MEIN ADD HO GYA HAI"* → silver `capacity_panel_issue` is right,
  matcher wrong.

So "65%" is three things tangled together: a taxonomy that does not separate, a reference that is
itself unreliable, and a genuinely weak matcher on the small classes. Items 1–3 untangle them,
in that order.

**What this does NOT threaten:** the ticket still exists, with the partner's words, identifiers,
permalink, recurrence count and validation flags. Disposition is a *routing hint*. The product —
a ticket per conversation, derived from the flow of the conversation — does not depend on it.

---

## Blocked on you

### 1. Decide the money taxonomy ⭐ highest value
**Owner: you (with me) · Blocks: everything downstream of disposition**

Five classes describe the same partner sentence — *"paisa nahi aaya"*. Which one is correct
usually depends on *why* it didn't arrive, and that reason is frequently **not in the message**.
No classifier, deterministic or neural, can recover information the text does not contain.

Three options, and I'd pick (b):

| | Option | Cost | Effect |
|---|---|---|---|
| a | Leave as is | none | precision stays ~65%; routing stays unreliable for 5 classes |
| b | **Collapse to one `money` class, sub-typed later from structured data** (deduction records, COD ledger) rather than from text | ~1 day | **precision → ~87%**; the split moves to where the answer actually lives |
| c | Keep five, and require a disambiguating question before routing | ongoing human cost | precise, slow, needs a reply path that does not exist yet |

**Done when:** you tell me which, and I re-score.

### 2. The authoritative disposition list
**Owner: you · Blocks: taxonomy reconciliation, item 1**

Three vocabularies in this repo disagree: `policies.py` has 26, the engine ~15, `tickets.db` 10
on 1.2% of rows. I do not know which one ops actually routes on.

**Done when:** I have that list, with a one-line meaning for each.

### 3. Human pass over ~800 audit rows
**Owner: you · Blocks: turning every score from silver to gold**

This is the ceiling. I measured embeddings against BM25 and **both fail on the same classes** —
which means the labels, not the method, are the limit. No modelling work moves the number until
this is done.

Stratified so the small classes are actually covered; ~800 is where per-class figures stop being
noise. I will generate the sheet when you are ready.

**Done when:** 800 rows carry `label_provenance: gold`, and `evaluate` reports gold separately.

### 4. Kapture export — partner's first inbound text + disposition
**Owner: you · Blocks: scaling exemplars beyond 1,814**

The *partner's* words, not the agent's summary. Takes exemplars from 1,814 to tens of thousands,
which is the cheapest available lift for the weak classes.

### 5. Residency answer + is there a CPU pod in `asia-south1`
**Owner: you · Blocks: hosted vs local. Nothing is waiting on it** — local-first is the design.

### 6. Volume per surface, now and in 12 months
**Owner: you · Blocks: whether a paid tier is ever built at all**

The funnel now prices this exactly: today 8 of 56 messages reach a human. Multiply by real volume
and you have the human cost — and the ceiling on what any paid tier could save.

### 7. Decisions I need a yes/no on

| Decision | Why it is blocked | Consequence of "yes" |
|---|---|---|
| Slack `reactions:write` for acknowledgement | Needs a reinstall + approval | The raiser sees a ✅ when their message becomes a ticket. Today they get nothing |
| Second surface: email or WhatsApp first | *Parked at your request* | Email is much cheaper — no Business API account, no public webhook, no queue |
| Is the Apps Script sheet the real register, or interim? | — | Decides whether I harden it or plan its replacement |

---

## Mine to build

### 8. Feed "not an issue" confirmations back into the evidence gate
Collected today but inert — the gate is rule-based, so a negative has nowhere to go. ~1 day.

### 9. Fix dedupe's O(n²)
Measured **5.21s at 3,500 issues**, grows with the square. Needed before any backfill. ~half a day.

### 10. Real-time hardening: Socket Mode, a queue, settle delay
The 5s poll is fine for a demo, not for traffic. A burst must not drop messages, a crash must not
lose them, and you cannot dedupe a cross-post that has not arrived yet — the MX1 pair was 47s
apart. ~2 days.

### 11. `chmod 600 backend/.env`
Currently 644 while the token files are 600. Two minutes.

### 12. Remove the demo LLM path
Conditional — delete once the local index exists and the deterministic tiers clear threshold on
held-out data.

### Parked at your request
- **WhatsApp reader** — validator rules and markup are done; needs a Business API account, a
  public webhook and the queue, because there is no polling equivalent to Slack's.
- **Email reader** — validator rules and quoted-history stripping are done. This is the cheap one
  when you want a second surface.

---

## Done

| Item | Evidence | Landed |
|---|---|---|
| **Schema v2 + `Source` protocol** | Slack, WhatsApp and email validate in one corpus; every Slack-only check kept *for Slack* and dispatched on `source_system`. See [adding-a-surface.md](adding-a-surface.md) | 2026-09-16 |
| **`evaluate` funnel** — per-tier escape rate, coverage, NOVEL rate | 56 msgs → 6 gated → 6 not-an-issue → 41 group into 23 issues → 15 answered → 8 reach a human. Every tier $0.00 | 2026-09-16 |
| **NOVEL queue + human confirm → exemplar** | 8 NOVEL → a human answers one over the real endpoint → 7, zero model calls. `gold_weight` is what stops one human label losing on volume to 80 machine labels | 2026-09-16 |
| **Ticket sink that creates real tickets** | Google Sheet via Apps Script, idempotent **sheet-side**: 20 created, re-run creates 0 under a different run id | 2026-09-16 |
| Type-vs-instance split + recurrence counting | The same issue raised twice counts 2 — not suppressed, not duplicated | 2026-09-12 |
| Tier 2 BM25 exemplar matcher | First real disposition number, on a held-out split | 2026-09-12 |
| Structured ticket payload + pre-emit validation | Every field typed; a flag on every failed check | 2026-09-12 |
| **Positive-evidence gate** — the play-arena fix | `can we go to play arena?` → `NOT AN ISSUE score=0.0`. **0 of 84 real tickets rejected** | 2026-09-10 |
| Labelled corpus from `kapture_audits.json` | 1,814 partner-voice messages, **13.7% NOVEL**, stratified split, no residual PII | 2026-09-10 |
| Slack app: 8 read scopes, **0 write scopes** | Verified against `X-OAuth-Scopes` | 2026-09-09 |
| Live read → contract-valid NDJSON → full pipeline | `validate.js` PASS on a live pull | 2026-09-09 |
| Cross-channel dedupe, linked not merged | MX1 pair at 0.97 similarity, 47s apart | 2026-09-08 |
| Two-tier DC extraction + 11,723-code registry | 95.2% precision on 155 adversarial messages | 2026-09-08 |

---

## Numbers to watch

| Metric | Now | Target | Where |
|---|---|---|---|
| Disposition precision (answered) | **65.2%** *(silver)* | ~87% with item 1 | `score_classifier.py` |
| Disposition coverage | **82.4%** | — | `score_classifier.py` |
| Taxonomy coverage (NOVEL rate) | **13.7%** *(silver)* | falling as the queue is worked | `build_corpus_from_audits.py` |
| Messages reaching a human | **8 / 56** | falls as exemplars grow | `evaluate` → `funnel` |
| DC precision / recall | **95.2% / 85.1%** | recall capped by the registry | `build_bombard_fixture.py` |
| Real tickets killed by the evidence gate | **0 / 84** | must stay 0 | `test_evidence.py` |
| Escape rate to a **paid** tier | **0%** — no paid tier exists | keep near zero | `evaluate` → `funnel` |
| Tests | **212 passing** | — | `pytest tests/intake` |

---

## Standing risks

1. **The evidence gate is the one place a real issue can die silently.** Every rejection is
   stored with its score and components. **Sample them weekly and tune against the sample, not
   against intuition.**
2. **The feedback loop can self-train into a rut.** Only *human-confirmed* labels become
   exemplars. Model output must never promote itself.
3. **Every score here is silver until item 3 is done.** They measure agreement with the old
   classifier, not correctness. Do not quote them externally yet.
4. **A confidently wrong disposition is invisible; a NOVEL is not.** That is why the matcher
   refuses rather than guesses, and why the refusal rate is a feature, not a defect.
5. **Human confirmations are the only data here nothing can regenerate.** They are gitignored and
   live on one machine. `cli.py labels --export` is the backup path.
