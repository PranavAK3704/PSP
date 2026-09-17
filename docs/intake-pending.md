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

## The language problem — measured, and it changes the architecture

Lexical matching does not degrade gracefully across scripts. It falls off a cliff.

| Group | Precision | Coverage | |
|---|---|---|---|
| English | **88.1%** | 81.6% | works |
| Hinglish (Latin script) | **72.2%** | 94.7% | degrades — 16 points |
| Devanagari | **score 0.0** | — | **fails completely** |

`मेरा पेमेंट नहीं आया` scores **0.00** and is always NOVEL. BM25 matches tokens; there are 5
Devanagari messages in 1,814, so a Devanagari message matches nothing at all. Same for vocabulary
the corpus has never seen — `gaadi kharab ho gayi` (vehicle broken down) also scores 0.00.

**And the corpus is the wrong population for this question.** It is 90.5% English because it
comes from *Kapture email tickets*. Slack skews more Hinglish; **WhatsApp will skew far more**,
with Devanagari and voice-note transcripts. We have **11 real Slack messages and zero WhatsApp
messages** to measure on — so the honest position is that the numbers above are a *floor* on the
problem, not a measurement of it.

**Conclusion: the deterministic tier cannot be the only tier once WhatsApp is live.** It stays as
the cheap first pass — it is free, explainable and handles the English bulk — with a model tier
behind it for what it refuses. That is an escape path, not a replacement, which is why it is
affordable (see item 8).

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
| a | Leave as is | none | precision stays ~68%; routing stays unreliable for 5 classes |
| b | **Collapse to one `money` class, sub-typed later from structured data** (deduction records, COD ledger) rather than from text | ~1 day | **68.4% → 86.3%, measured, at zero coverage cost** |
| c | Keep five, require a disambiguating question before routing | ongoing human cost | precise, slow, needs a reply path that does not exist yet |

**Measured on a held-out TEST half that nothing was tuned against** (`scripts/score_levers.py`):

| Configuration | Precision | Coverage |
|---|---|---|
| As it ships today | 68.4% | 79.6% |
| **+ money classes merged** | **86.3%** | **79.6%** |
| + also gate the 2 untrusted classes | 87.4% | 75.5% |

**+17.9 points for zero coverage.** Gating adds ~1 point of precision for ~4 of coverage —
measured and *not* recommended; it is recorded so it does not get re-litigated.

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

The *partner's* words, not the agent's summary. Takes exemplars from 1,814 to tens of thousands.

**Measured learning curve** (`score_levers.py`): **+3.5 precision points per 10× exemplars**, and
the curve is still rising at 1,259 — so another 10× plausibly puts this near 90%. Real, but
slow: worth acquiring, not the big lever. Item 1 is worth five times as much and costs a day.

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

### 8. LLM escape tier — the answer to Hinglish and Devanagari ⭐ needs your go-ahead
**Blocks: any surface that is not English-dominant**

The deterministic tier handles what it can for free and *refuses* the rest. Only the refusals
reach a model, which is what makes this cheap. Priced from the real funnel (56 messages → 23
issues, 8 refused), with an on-disk cache so a repeated message costs nothing:

| Volume | Escape | Opus 5 | Sonnet 5 | Haiku 4.5 |
|---|---|---|---|---|
| 100k msgs/mo | 20% | $41/mo | $17/mo | $8/mo |
| 1M msgs/mo | 20% | $413/mo | $165/mo | $83/mo |
| 1M msgs/mo | 35% (today's rate) | $723/mo | $289/mo | $145/mo |

Halve every figure with the Batch API where latency allows. **At 1M messages/month the most
capable model costs ~$413/mo** — against a support operation of this size, that is not a real
constraint, and the escape rate falls as exemplars grow.

Recommendation: **start on Opus 5**, because the escape path only sees what the cheap tier
already failed — that is exactly where capability matters. Measure, then step down to Haiku if
quality holds. The key already exists in `backend/.env`.

Guards that must ship with it: a hard `max_spend_usd` with its own ledger (**not**
`llm_spend.json` — that is the deployment's $45 chat budget), the on-disk cache so a re-run costs
nothing, and `intent_source` on every row so model-labelled tickets stay separable from
deterministic ones.

### 9. Catch the issues the gate wrongly killed ⭐ the biggest hole
**Blocks: trusting the gate at all**

Today the loop only learns in one direction. A NOVEL goes to a human and comes back as an
exemplar. But **a real issue killed by the noise gate is seen by nobody, ever** — it does not
appear in a queue, a count, or a report. That is the failure mode that matters most, because it
is silent.

Three mechanisms, cheapest first:

1. **Behavioural re-verification, free and deterministic.** A gated message that then gets a
   thread with replies, or a reaction, was probably an issue. We already store `reply_count` and
   thread structure — so the pipeline can re-open its own decision on evidence that arrived
   *later*, with no human and no model. This is the "re-verify it over and over" mechanism.
2. **A sampled rejection queue.** Every rejection already carries its score and components.
   Surface N per week next to the NOVEL queue; a human clicking "this was an issue" both raises
   the ticket and records a negative example.
3. **Feed those negatives into the gate.** The counterpart to the NOVEL loop, closing the
   negative half. Today they are collected but inert.

### 10. Feed "not an issue" confirmations back into the evidence gate
Collected today but inert — the gate is rule-based, so a negative has nowhere to go. ~1 day.

### 11. Fix dedupe's O(n²)
Measured **5.21s at 3,500 issues**, grows with the square. Needed before any backfill. ~half a day.

### 12. Real-time hardening: Socket Mode, a queue, settle delay
The 5s poll is fine for a demo, not for traffic. A burst must not drop messages, a crash must not
lose them, and you cannot dedupe a cross-post that has not arrived yet — the MX1 pair was 47s
apart. ~2 days.

### 13. `chmod 600 backend/.env`
Currently 644 while the token files are 600. Two minutes.

### 14. Remove the demo LLM path
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
| Disposition precision (answered) | **65.2%** full holdout · **68.4%** test half *(silver)* | **86.3%** with item 1, measured | `score_classifier.py`, `score_levers.py` |
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
   refuses rather than guesses, and why the refusal rate is a feature, not a defect. **But
   refusing harder is not a fix here** — pushing `min_margin` 0.15 → 0.60 cost 51 points of
   coverage to buy 13 of precision. The money-class errors are *confident*, not borderline, so
   no threshold reaches them. Only the taxonomy does.
5. **Human confirmations are the only data here nothing can regenerate.** They are gitignored and
   live on one machine. `cli.py labels --export` is the backup path.
