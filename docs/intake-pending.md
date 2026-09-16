# Intake — pending tracker

**Living document. Updated as things land, so nothing important lives only in a chat log.**
Last updated: **2026-09-10**

## Open — blocked on you

| Item | Blocks | Status | Updated |
|---|---|---|---|
| **Home embedding run + local labelling** (5080 / 16GB VRAM) | Tier 3 semantic matching, threshold calibration, the linear probe | **in progress — tonight** | 2026-09-10 |
| **Human pass over ~800 `kapture_audits` rows** | Turns the 86.3% coverage figure from *silver* to *gold*. Until then it measures "the old classifier assigned a bucket", not "the taxonomy covers reality". **Now the highest-value item you own** — the embedding comparison showed both methods failing on the *same* classes, which means the labels, not the method, are the ceiling | open | 2026-09-16 |
| **150–300 real off-topic messages** (the "play arena" class) | ⚠️ The evidence gate is **built but unmeasured on real negatives** — the 155-message corpus has only ~4. **Now self-collecting:** every "not an issue" click in the NOVEL queue stores one, so this fills up as the queue is worked rather than needing a separate exercise | open — collecting | 2026-09-16 |
| **Authoritative disposition list** | Taxonomy reconciliation. Three vocabularies in the repo disagree: `policies.py` 26, engine ~15, `tickets.db` 10 on 1.2% of rows | open | 2026-09-10 |
| **Kapture export: partner's first inbound text + disposition** | Scales exemplars from 1,814 to tens of thousands. The *partner's* words, not the agent's summary | open | 2026-09-10 |
| **Residency answer** + is there a CPU pod in `asia-south1` | Hosted vs local. Nothing blocked meanwhile — local-first is the design | open | 2026-09-10 |
| **Volume per surface**, now and in 12 months | Decides whether Tier 3 is built at all | open | 2026-09-10 |

## Open — mine

| Item | Blocks | Status | Updated |
|---|---|---|---|
| Type-vs-instance split + recurrence counting | The seven-tickets-over-five-months signal | next | 2026-09-10 |
| Tier 2 BM25 exemplar matcher over the 1,814 corpus | First real disposition accuracy number | next | 2026-09-10 |
| Structured ticket payload + pre-emit validation | Machine-consumable output | next | 2026-09-10 |
| `evaluate`: per-tier escape rate, NOVEL rate, coverage | The KPIs you manage this by | next | 2026-09-10 |
| Schema v2 + `Source` protocol | Second surface. **Ten Slack-shaped assumptions, eight load-bearing** | next | 2026-09-10 |
| Fix dedupe's O(n²) — 5.21s at 3,500 issues | Any backfill | before backfill | 2026-09-10 |
| Real-time: Socket Mode, queue, settle delay | Production traffic | after demo | 2026-09-10 |
| **Remove the demo LLM path** | — | conditional: delete when the local index exists and Tier 2/3 beat threshold on held-out data | 2026-09-10 |
| `chmod 600 backend/.env` (currently 644; token files are 600) | — | open | 2026-09-10 |

## Done

| Item | Evidence | Landed |
|---|---|---|
| **NOVEL queue + human confirm → exemplar** | The loop closes end to end: 8 NOVEL issues → a human answers one over the real endpoint → 7, and that issue carries the human's label via BM25 with **zero model calls**. Held-out score unchanged at 65.2%/82.4% | 2026-09-16 |
| **Ticket sink that creates real tickets** | Google Sheet via Apps Script. Idempotent **sheet-side**: 20 created, re-run creates 0 under a different run id. `--sink` defaults to dry | 2026-09-16 |
| **Positive-evidence gate** — the play-arena fix | `can we go to play arena?` → `NOT AN ISSUE score=0.0`. **Zero of 84 real corpus tickets rejected.** 154 tests | 2026-09-10 |
| Labelled corpus extracted from `kapture_audits.json` | 1,814 partner-voice messages, 15 dispositions, **13.7% NOVEL** reproduced; stratified 1458/356 split; no residual PII | 2026-09-10 |
| Slack app: bot token, Socket Mode token, 8 read scopes, 0 write scopes | `auth.test` ok, handshake ok, verified against `X-OAuth-Scopes` | 2026-09-09 |
| Live read → contract-valid NDJSON → full pipeline | `validate.js` PASS on a live pull | 2026-09-09 |
| Live dashboard (`live_demo.py`) | Polls every 5s, loopback only, no write path | 2026-09-09 |
| Ticket drafting with source-derived idempotency key | Keys stable across runs and run-ids | 2026-09-09 |
| Cross-channel dedupe, linked not merged | MX1 pair at 0.97 similarity, 47s apart | 2026-09-08 |
| Two-tier DC extraction | 95.2% precision on 155 adversarial messages | 2026-09-08 |
| DC registry — 11,723 codes + 140-token denylist | `342` rejected; `TID`/`RVP`/`SIR` denied | 2026-09-08 |

## Numbers to watch

| Metric | Now | Target | Where |
|---|---|---|---|
| Taxonomy coverage (NOVEL rate) | **13.7%** *(silver)* | falling | `build_corpus_from_audits.py` |
| DC precision / recall | **95.2% / 85.1%** | recall capped by the registry | `build_bombard_fixture.py` |
| Real tickets killed by the evidence gate | **0 / 84** | must stay 0 | `test_evidence.py` |
| Escape rate to a paid tier | **0%** — no tier implemented | keep near zero | `evaluate` |
| Tickets actually created | **0** — phase 1 drafts only | — | `emit` |

## Standing risks

1. **The evidence gate is the one place a real issue dies silently.** Every rejection is stored
   with its score and components. **Sample them weekly and tune the threshold against the
   sample, not against intuition.**
2. **The feedback loop can self-train into a rut.** Only *human-confirmed* labels may become
   exemplars. Model output must never promote itself.
3. **86.3% is a silver number.** Do not quote it externally until the human pass is done.
