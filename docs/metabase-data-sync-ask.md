# Data access request — Valmo Advocate resolution engine

**From:** Pranav Akella · **Re:** replicating the tables behind Metabase into an app DB on a 3–4h refresh

## Goal (plain version)
The Valmo Advocate resolution engine needs to look up a captain's real financial/operational
records (losses/debits, payments, FE-ID status, COD/cash, consumables, orders) at conversation
time — by AWB, FE-ID, txn ID, or captain ID — in milliseconds.

Today I export CSVs from Metabase by hand and load them into an indexed app DB. I want to
**automate** that: a scheduled job that keeps the app DB in sync with the same data Metabase reads,
refreshing on the source's own 3–4h cadence. I'm **not** asking to touch production or the
warehouse directly at runtime — just to replicate a read-only copy of specific tables.

## What I need (either path works — pick whichever is easier to grant)

**Path A — Metabase API access** *(probably fastest)*
- A Metabase **API key** (or service account) + the **base URL**.
- The **saved-question / card IDs** for the queries powering each domain below.
- → I pull each question's results as JSON on a schedule. No warehouse creds needed.

**Path B — Direct read on the source DB behind Metabase** *(cleanest long-term)*
- **Which database** Metabase connects to for these tables (BigQuery / Snowflake / Redshift /
  Postgres replica / …?).
- **Read-only credentials** to it + how to reach it (VPN / IP allowlist / service account).
- → I sync directly and it removes Metabase's 1,000,000-row export cap (I'm hitting it today —
  only ~27% of our ticket AWBs fall inside the current export slice).

## Per-table details I need (for either path)
For each domain — **losses/debits, payments, FE-ID reactivation, COD/cash, consumables, orders** —
please point me at the table (or Metabase card) and confirm:
- The **primary key** (AWB / FE-ID / txn_id / captain_id).
- An **`updated_at` / load-timestamp** column, if one exists → lets me sync only changed rows
  (incremental) instead of re-pulling the whole table every 3–4h.
- Rough **row count + growth rate** (so I size the DB and pick full-refresh vs incremental).

## Refresh
- Confirm the **3–4h cadence** — is it a fixed schedule or event-driven, and is there a reliable
  "last updated" signal I can diff against?

## Governance (need an explicit yes)
These are financial + captain PII records. I need sign-off that **this data may be replicated into
the engine's app DB**, plus any rules on retention, masking, and who can access it. The app DB will
be access-controlled and secrets kept in GCP Secret Manager.

## Destination / hosting (FYI — my side)
The synced copy lands in the engine's own indexed DB (currently Turso / cloud SQLite; can be
Supabase/Postgres if the team standardizes there). The sync job runs on GCP (Cloud Scheduler →
Cloud Run Job). None of this changes anything upstream.

---
**TL;DR of the ask:** an API key **or** read-only DB creds, the list of tables/cards with their keys
+ timestamps, row counts, and a governance yes. With those, the automated 3–4h sync is a small job —
the rest of the pipeline already exists.
