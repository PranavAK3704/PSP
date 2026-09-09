# Intake — known issues, measured

**As of 9 Sep 2026.** Everything here is a number someone can reproduce, not an impression.
Reproduce the accuracy figures with:

```bash
cd backend
.venv/bin/python scripts/build_bombard_fixture.py      # 155 adversarial messages, scored
.venv/bin/python -m pytest tests/intake -q             # 126 tests
.venv/bin/python scripts/check_intake.py               # the repo-native harness
```

## Where it stands

| | on hand-built fixtures | on 155 adversarial messages |
|---|---|---|
| DC code precision | 100% | **95.2%** |
| DC code recall | 100% | **85.1%** |
| Noise gate | 14/14 | **12/33** |
| Informational (weather + announcements) | 3/3 | **19/28** |

**The two columns differ because I wrote both the messages and the rules in the left one.**
That is the single most important line in this document. A fixture whose author also wrote the
extractor measures agreement with itself. The right-hand column is the number to quote.

---

## 1. Blocked on someone else

### 1.1 The DC registry is incomplete — this is the recall ceiling
**Impact: high. Owner: ops.**

`config/dc_codes.txt` holds 11,723 codes derived from `valmo.db` and `tickets.db`. Every one is
*observed in transactional data*, so a hub with no loss/ticket/QC history is simply absent.

Measured: **21 of 21 remaining false negatives** are bare codes missing from that registry —
`R2F`, `L9D`, `T5X`, `RX3`, `AML`, `VN4`. Tier A finds them when a label is adjacent; Tier B
cannot find them at all without the registry, and roughly a third of code-bearing messages
carry no label.

This was predicted when the registry was built (5 of 25 observed Slack codes missing) and is
now confirmed against adversarial data. **Recall does not move until the ops master arrives.**
`evaluate` reports registry coverage as its own number so this never reads as poor regex recall.

Ops also still owes **names and cities**. No code→name mapping exists anywhere in the repo;
`attribution.metadata_party_name` looks like one and is exactly `lower(entity_id)` in 100% of rows.

### 1.2 `docs/message-schema-v1.md` does not exist
**Impact: low.** `docs/field-map.md` and `tools/schema.json` cover field semantics and key
order. That one document is the only unread part of the contract.

### 1.3 The 490-record export is not on this machine
**Impact: high for confidence, zero for progress.** Everything is measured on synthetic data
until it lands. See §4.1.

---

## 2. Our work, outstanding

### 2.1 Recurrence counting is specified but not built
**Impact: high — this is the headline use case.**

Today `emit` **suppresses** the second occurrence of an issue. That destroys exactly the signal
the project exists to capture: one hub raising seven tickets over five months, where the *value*
is the number seven.

Three changes agreed and not yet made:
- one ticket per issue *group* carrying `occurrence_count` and a list of occurrences
- **per-kind join windows** — `mobile`/`pilot_id` identify a *person* and should join over
  months; `waybill` identifies one shipment and should join over days; `dc_code` never. Today
  `window_days: 7` applies uniformly, so a match five months apart is discarded — which is the
  seven-tickets case exactly
- a confidence threshold: auto-count only on strong joins, surface weaker ones as
  *"possible recurrence — confirm?"* rather than folding them in silently

`TicketSink` also needs `update()`, since occurrence two arrives after the ticket exists.

### 2.2 The noise gate catches 12 of 33
**Impact: medium.** Part of this is a genuine miss and part is a disagreement worth recording.

The corpus labels bare follow-ups (`"any update ??"`, `"?"`, `"gentle reminder"`) as noise.
**This pipeline deliberately does not gate them.** The brief calls orphan follow-ups "the real
mess" and routes them to adjudication. An un-gated orphan is visible; a gated one is gone. That
position is a choice, not an oversight — but if the desk disagrees it is a config change.

The real misses are longer Hinglish acks that carry a DC code (`"thanks CGV team 🙏 sorted"`).
Gating those would lose the code, so they are currently kept.

### 2.3 Informational catches 19 of 28
**Impact: medium.** The 9 remaining are mostly **Devanagari** (`सभी हब इंचार्ज ध्यान दें…`) and
announcement phrasings not in the list. The classifier is deliberately conservative because the
risk is asymmetric: a real issue wrongly marked informational is **held back and never
ticketed**, while a missed announcement merely raises a ticket somebody closes.

Measured after the last change: **2 real tickets marked informational, both flagged BORDERLINE**
— surfaced for review rather than dropped. **Zero real tickets wrongly gated.**

### 2.4 Six remaining false positives
**Impact: low.** Two shapes:
- A real DC code sitting inside an ack (`"noted for HY9 IAM VN4 👍 thanks"`). The code *is*
  there; whether an ack should carry one is a judgement call.
- `312` extracted from `"RX3 hub, 312 deliveries"` — label-adjacent and numeric. Denying all
  purely-numeric tokens in Tier A would fix it but would also reject a genuine `"DC 353"`.

### 2.5 `INV` is both a hub code and "invoice"
**Impact: low, but it will recur.** `INV` is in the registry, is not denylisted, and the brief
records it as a real bare code — so `"Invoice Series: INV/26-27/00451"` yields a DC code. An
observed code outranks an acronym guess, so it stays extractable. A slash-context rule would fix
it properly.

### 2.6 Dedupe is O(n²)
**Impact: none today, real at backfill.** Measured: **5.21s at 3,500 issues**, and it grows with
the square. Bucketing by author and time window before the pairwise scan makes it near-linear.
Fix before any 90-day backfill.

### 2.7 Stage 6 is not implemented
**By design, not an accident.** Intent reads `(unadjudicated)`, nothing reaches `RESOLVED` or
`CLOSED`, and closure is the biggest gap in the source data. It is gated on
`THE_NUMBER_grouping_without_stage_6` from `evaluate` — if deterministic grouping holds up on
real data, this stage may never be needed. It is also the only stage that costs money
(~$1.51/cold run, 93% of it stage 6b/c).

---

## 3. Untested, pending a five-minute action

### 3.1 `files:read` and `reactions:read` — the recoverable-data question
**Do this first.** `attachments` and `reactions` came back structurally empty across the whole
467-record export, attributed to the exporting tool being unable to read them. **A missing scope
produces exactly that symptom** — empty arrays, not an error.

Both scopes are now granted. Posting **one image with no caption** and **one emoji reaction** in
`#intake-test` settles it. If they come back populated, that export is recoverable by re-running
rather than lost.

---

## 4. Things that look like gaps and are not

### 4.1 `evaluate` reports 100% grouping accuracy
That number means **the rules agree with the fixture author** — the golden labels are all
synthetic. `evaluate` prints a `CAVEAT` saying so. The comparison *between* settings
(entity-join-only vs +dc_code) is meaningful; the absolute number is not. Real hand-labels
append to the same CSV with `source=real` and are scored separately.

### 4.2 Nothing appears in Slack when you post
Correct. The app is installed with eight scopes, all `:history` or `:read`, and **no
`chat:write`** — verified against `auth.test`'s `X-OAuth-Scopes`. It is structurally incapable
of posting. There is no send path in `slack_source.py` or `live_demo.py`, and a test asserts the
emit module imports no HTTP client.

### 4.3 No ticket is ever created
Phase 1 drafts and stops. `DryRunTicketSink` returns `DRY-…` references so one can never be
mistaken for a ticket id. The payload and the idempotency key — the parts painful to retrofit —
are done; the sink is three lines whenever a destination is chosen.

### 4.4 PSP has no ticket-create endpoint
True, and not a problem for this module. PSP is one candidate sink among several. See
[`psp-ticket-contract.md`](psp-ticket-contract.md).

---

## 5. WhatsApp, for later

- The **Groups API shipped in 2026**, but caps at **8 participants** and only works for groups
  the business *creates* — existing partner groups cannot be joined or read.
- It requires an **Official Business Account**, which is a Meta Trust & Safety decision,
  submitted by a BSP, not purchasable. *Ask first whether Valmo already has one — there is a
  `#valmo-gupshup-internal` channel, so a BSP relationship may exist.*
- **There is no history API.** Webhooks are forward-only; no backfill, ever. That inverts the
  Slack strategy — you must connect before you can measure anything.
- `validate.js` enforces `message_id` matching `^\d{10}\.\d{6}$`; a `wamid.…` fails. Schema v1.1
  is Slack-shaped and would need a v2 or a per-source validator.
