# Valmo Advocate — Parked & Backlog (single source of truth)

Everything deferred/blocked as of 2026-07-10, with enough specificity to pick each up cold.
Grouped by what's holding it. "Mechanism built" = the plumbing exists; only the item below blocks it.

---

## A. Blocked on external input / data

### A1. Metabase → app-DB data sync  ⛔ blocked on Metabase access + one saved-query
**Goal:** automated extract-on-a-schedule (every ~3–4h, matching the lake's own refresh) from Metabase
into the app DB (Turso/SQLite). Runtime never queries Metabase/Presto live — it reads the fast local
copy (ms). The 1–3 min Metabase latency is quarantined into the background batch.
- **Decided architecture:** runtime = Turso copy (already how the engine reads); sync source = Metabase
  saved-query API now (fast enough for a batch), direct Presto later (faster, no saved-question drift).
  Runner = GCP Cloud Scheduler → Cloud Run Job. No n8n (it's just a scheduled HTTP pull we write as code).
- **Source identified:** org data is on **Presto/Trino** (data lake) behind Metabase — cxns "Presto Prod
  GPM" (id 26), "Presto Prod [SB]" (id 2), "Deactivation card DB" (id 22, likely FE-ID). Also postgres/mysql/mongo.
- **Auth:** an API key is admin-gated; use the `metabase.SESSION` browser cookie (works with Google SSO,
  no admin) to bootstrap. Probe is BUILT: `backend/scripts/metabase_probe.py` (X-Api-Key → X-Metabase-Session
  → /api/session fallback; reads creds from `backend/data/metabase_*.txt`; never prints secrets).
- **UNBLOCK:** paste one saved question's `/api/card/:id` `dataset_query.native.query` (the SQL reveals the
  lake's catalog.schema.table names) + get a Metabase API key or session cookie. Then build the sync harness
  (source-agnostic: direct-Presto | Metabase-API flag) reusing `build_valmo_db.py` normalize logic.
- Ask doc for the data team: `docs/metabase-data-sync-ask.md`.

### A2. Supreme issue-mapping — Payments + FE-ID Blueprints (the "#2" ask)  ⛔ blocked on walkthroughs
**Goal:** per-domain Resolution Blueprints so mapping is exhaustive and the engine asks only the true gap
(slot-filling, never redundant questions).
- **Mechanism BUILT:** the Authoring Studio compiles a raw walkthrough → structured Blueprint (signals →
  derivations → lookups → decision → ask_if_missing); inline gap detection; approve → `store.reload` →
  engine read-path injects the approved brain into the turn. **Losses is seeded + approved + steering the engine.**
- **UNBLOCK:** the domain owner's Payments and FE-ID walkthroughs (losses-style: signals hunted → how they
  map → reverse/inform/escalate). Paste into the Authoring Studio (Domain Brain mode) → machine structures →
  review/gap-check → approve. Other domains (cod_cash, consumables, orders) follow the same mold.

### A3. Payments / FE-ID data resolvers + ticket-dump eval harness  ⛔ needs real data (A1)
**Goal:** domain-specific deterministic resolvers (like the losses resolver in `policy_exec.py`/`loss_db.py`)
for payments + FE-ID, plus an eval harness that replays a real ticket dump and measures solve rate per domain.
Depends on the Metabase sync (A1) landing the payments/FE-ID tables.

---

## B. Deferred by sequencing (buildable; chosen to wait)

### B1. Metrics Studio + auto-RCA  (waits on A1 for full power)
Third "studio" (same pattern as Authoring/Auditing). A metric = {name, formula, data source}. Two source
modes: **manual** (formula + point at the data, cheap/deterministic) or **LLM-assisted** (describe it → LLM
finds how to compute it, costs more). Snapshots per period → **week-on-week comparison** → **auto-RCA**
(LLM breaks a moved metric down by domain/disposition/hub and explains drivers). Subsumes the hygiene
solve-rate dashboard. Concern-log-derived metrics (solve rate, escalation rate, CSAT, turns, audit score)
are buildable NOW; deep operational metrics bloom once A1 lands.

### B2. RBAC — author / auditor / ops / viewer
Now warranted: three studios can each change system behavior (edit blueprint, audit weights, metric defs,
approve nuances). Light roles gating who can edit/approve what. Fold into the studios rather than bolt on later.

### B3. Authored-config durability → externalize to Turso
All authored config + logs live in ephemeral JSON files (`data/blueprints.json`, `kt_queue.json`,
`audit_rubric.json`, `audits.json`, `traces.json`, `concern_log.json`, `cpd_log.json`). These reset on a
container redeploy. Externalize to Turso (same client already used for loss data) so authored brains, KT,
rubric, audit history and traces survive deploys. (User flagged this earlier re: "the KT is stored somewhere right?")

### B4. GCP production deploy
Non-Docker run path exists (`run.sh` + `scripts/load_env.sh` + uvicorn). Still to do: Cloud Scheduler +
Cloud Run Job for the A1 sync; **GCP Secret Manager** for creds instead of baked `data/*.txt`; serve the
built frontend. (Hackathon image is live at pranav-akella.buildathon.ltl.sh; production is GCP.)

### B5. Proactive monitoring end-state
Each Blueprint has a `proactive` field ("warn the captain on the panel before the debit posts"). The Monitor
state + shadow-first nudge exist; the full loop — proactively surfacing a risk to the captain tied to the
blueprint's proactive rule — is aspirational.

---

## C. Minor / hygiene leftovers
- `backend/data/knowledge/losses_sla.json` — now unread (only the deleted `sla_matrix()` used it). Delete on request.
- `/api/constitution`, `/api/dispositions`, `/api/policies` — no frontend consumer after cleanup; kept as intentional API surface.
- CSV export: `conversation_id` column blank for money/escalate-path concern records (existing record shape, not the export).
- `favicon.ico` 404 (cosmetic).
- Reviewer override / feedback loop — partially covered by CPD signals + audit rationales; a first-class "override this decision" action is not built.

---

## Already DONE (so the backlog stays honest)
- Query crash (object-rendered-as-React-child) + ErrorBoundary + WebGL leak hardening.
- Full repo cleanup (dead code, HERO data gated behind `--seed-demo`, docs corrected, no-op buttons removed, stubs → experimental, anthropic dropped).
- Composer multiline auto-grow + slim My Cases bar.
- Authoring Studio (machine-structures SOP/brain → editable view + inline gaps + queue/approve; engine read-path; Losses seeded).
- Foundation: persisted resolution trace, expandable + exportable Concern Log.
- Auditing Studio: editable weighted rubric (partner-supportedness 0.25) + LLM-judge 0–100 scoring + versioned + trends.
- **Explainability** (the earlier hygiene item) — delivered via the expandable per-concern trace in the Concern Log.
