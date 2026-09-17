"""SQLite store for the Slack intake pipeline. WRITABLE — the first such store in this repo.

Every other database here is read-only at runtime (`app/substrate/tickets_db.py` and
`loss_db.py` both open `file:...?mode=ro`, deliberately). This one is written, because the
pipeline's whole point is to accumulate state across stages. Three consequences follow, and
each is load-bearing:

── 1. IT NEVER RUNS IN THE DEPLOYED APP ──────────────────────────────────────────────────────
`*.db` is gitignored (.gitignore:12) and `backend/data/*.db` is .dockerignore'd, so this file
is neither committed nor baked into the image. That is correct for a batch analytics module,
not an oversight: intake is offline tooling in the shape of `scripts/build_tickets_db.py`, run
on a laptop against an export. Nothing in FastAPI imports it. If that ever changes, the row
data has to reach Turso first — and `scripts/push_to_turso.py` recreates tables as all-TEXT
with no constraints, which would silently drop the composite primary key below.

── 2. THE PATH IS ENV-OVERRIDABLE, AND THAT IS A CONTAINMENT REQUIREMENT ─────────────────────
`scripts/_contain.py` seeds a harness tmpdir from `backend/data/` by COPYING `*.json` but
SYMLINKING directories, `*.db` and `*.txt` (_contain.py:127) — on the stated assumption that
`.db` files are static and read-only. A writable `.db` breaks that assumption, and a
subdirectory does not help: the directory itself gets symlinked, so a harness would write
straight through to the real file. That is exactly the contamination `_contain.py` was written
to stop, after 702 junk rows landed in the real concern log.

So the path comes from `$INTAKE_DB`, and `scripts/check_intake.py` points it at a tmpdir —
mirroring what `PSP_STATE_DIR` does for the JSON stores. `app/state_paths.py` cannot be reused:
its own docstring says `*.db` is NOT routed through it.

── 3. RE-RUNNABILITY IS A SCHEMA PROPERTY, NOT A CODE PROPERTY ───────────────────────────────
`messages` is immutable — inserted once, never UPDATEd. Every derived table carries a `run_id`,
so re-running a stage is `DELETE FROM <t> WHERE run_id = ?` followed by fresh inserts. That is
what makes "change one regex, re-run from stage 4" true.

`llm_cache` is deliberately NOT scoped to `run_id`: it is keyed on sha256(model + prompt), so a
re-run finds every prior response and issues zero API calls. That only holds if prompts are
built deterministically — see `app/intake/adjudicate.py`, which is a pure function of stored
rows for this reason.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

#: Default location. Override with $INTAKE_DB (harnesses and tests MUST do so — see above).
_DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "intake" / "intake.db"

SCHEMA_VERSION = 1

#: Derived tables, in dependency order. A stage re-run deletes its own rows by run_id;
#: `reset_stage()` is the only sanctioned way to do that, so the list lives in one place.
STAGE_TABLES = {
    "qualify":    "channel_qualification",
    "gate":       "message_flags",
    "evidence":   "evidence",
    "extract":    "entities",
    "group":      "assignments",
    "adjudicate": "adjudications",
    "register":   "issues",
    "dedupe":     "duplicates",
    "emit":       "ticket_drafts",
}

DDL = """
-- ── stage 1: load ───────────────────────────────────────────────────────────────────────────
-- Immutable. The composite PK is (channel_id, message_id), NOT message_id alone: two channels
-- can carry the same Slack ts, and cross-posting between #valmo-firefighters and #valmo-lm-ams
-- is confirmed real (the MX1 captain-panel issue, posted twice 47 seconds apart).
CREATE TABLE IF NOT EXISTS messages (
  channel_id            TEXT NOT NULL,
  message_id            TEXT NOT NULL,
  schema_version        TEXT,
  -- WHICH SURFACE this came from: slack | whatsapp | email. Half the idempotency key, so a
  -- WhatsApp message and a Slack message that happen to share a container and message id are
  -- still two different tickets. v1.1 records predate the field and load as 'slack'.
  source_system         TEXT NOT NULL DEFAULT 'slack',
  source                TEXT,
  workspace_id          TEXT,
  channel_name          TEXT,
  ts_epoch              REAL NOT NULL,
  ts_iso                TEXT NOT NULL,
  author_id             TEXT,
  author_name           TEXT,
  author_email          TEXT,
  author_is_bot         INTEGER,
  text                  TEXT NOT NULL,
  subtype               TEXT,
  thread_ref            TEXT,
  is_thread_parent      INTEGER,
  reply_count           INTEGER,
  mentions_json         TEXT,
  channel_mention       INTEGER,
  subteam_mentions_json TEXT,
  attachments_json      TEXT,
  has_media             INTEGER,
  reactions_json        TEXT,
  permalink             TEXT,
  edited_ts             TEXT,
  fetched_at            TEXT,
  src_file              TEXT,
  loaded_at             TEXT,
  PRIMARY KEY (channel_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_messages_thread ON messages(channel_id, thread_ref);
CREATE INDEX IF NOT EXISTS ix_messages_ts     ON messages(ts_epoch);
CREATE INDEX IF NOT EXISTS ix_messages_author ON messages(author_id);

-- ── stage 2: qualify ────────────────────────────────────────────────────────────────────────
-- One row per channel per run. `in_scope` is always accompanied by `reason` and the numbers
-- that produced it — a channel is never excluded without the figure attached.
CREATE TABLE IF NOT EXISTS channel_qualification (
  run_id               TEXT NOT NULL,
  channel_id           TEXT NOT NULL,
  channel_name         TEXT,
  total_records        INTEGER,
  substantive_toplevel INTEGER,
  identifier_rate      REAL,
  weather_share        REAL,
  reply_count_p50      REAL,
  reply_count_p90      REAL,
  top_authors_json     TEXT,
  sampled_issue_share  REAL,
  sample_n             INTEGER,
  threshold            REAL,
  in_scope             INTEGER,
  reason               TEXT,
  computed_at          TEXT,
  PRIMARY KEY (run_id, channel_id)
);

-- ── stage 3: noise gate + informational classifier ──────────────────────────────────────────
-- MARK, never delete: the filtered set has to be reportable. `informational` is a separate axis
-- from `gated` on purpose — weather callouts are deliberate operational comms, not noise, and
-- they are 20-39% of top-level messages in lm-ams.
CREATE TABLE IF NOT EXISTS message_flags (
  run_id                   TEXT NOT NULL,
  channel_id               TEXT NOT NULL,
  message_id               TEXT NOT NULL,
  gated                    INTEGER,
  gate_rule                TEXT,
  informational            INTEGER,
  informational_rule       TEXT,
  informational_borderline INTEGER,
  PRIMARY KEY (run_id, channel_id, message_id)
);

-- ── positive evidence: "is this a partner/ops issue at all?" ────────────────────────────────
-- THE tier where a real issue can die silently, so EVERY decision is stored — including the
-- rejections — with its score and the components that fired. That is what makes the threshold
-- auditable instead of a number somebody once felt was about right.
CREATE TABLE IF NOT EXISTS evidence (
  run_id       TEXT NOT NULL,
  channel_id   TEXT NOT NULL,
  message_id   TEXT NOT NULL,
  decision     TEXT,      -- issue | weak | orphan | not_an_issue | reply
  score        REAL,
  reasons      TEXT,      -- every component that fired, human-readable
  ops_hits     TEXT,
  problem_hits TEXT,
  PRIMARY KEY (run_id, channel_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_evidence_decision ON evidence(run_id, decision);

-- ── stage 4: entity extraction ──────────────────────────────────────────────────────────────
-- One row per (message, kind, value). `tier` is 'A' for label-anchored DC codes and 'B' for
-- registry-resolved bare tokens; `matched_by` records the rule that fired, so a false positive
-- can be traced to its pattern instead of being argued about.
CREATE TABLE IF NOT EXISTS entities (
  run_id     TEXT NOT NULL,
  channel_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  kind       TEXT NOT NULL,
  value      TEXT NOT NULL,
  tier       TEXT,
  matched_by TEXT,
  PRIMARY KEY (run_id, channel_id, message_id, kind, value)
);
CREATE INDEX IF NOT EXISTS ix_entities_value ON entities(kind, value);
CREATE INDEX IF NOT EXISTS ix_entities_run   ON entities(run_id);

-- ── stage 5: grouping ───────────────────────────────────────────────────────────────────────
-- `rule` and `confidence` are recorded for EVERY assignment, including 'unassigned'. Grouping
-- biases toward over-splitting: a wrong merge sends the wrong DC the wrong answer, and a human
-- can recover a split but not a contamination.
CREATE TABLE IF NOT EXISTS assignments (
  run_id      TEXT NOT NULL,
  channel_id  TEXT NOT NULL,
  message_id  TEXT NOT NULL,
  issue_id    TEXT,
  rule        TEXT,
  confidence  REAL,
  reason      TEXT,
  assigned_at TEXT,
  PRIMARY KEY (run_id, channel_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_assignments_issue ON assignments(run_id, issue_id);

-- ── stage 6: adjudication ───────────────────────────────────────────────────────────────────
-- NOT scoped to run_id: this is what makes a second run cost nothing. Keyed on the exact
-- prompt so a changed prompt is a different key rather than a stale hit.
CREATE TABLE IF NOT EXISTS llm_cache (
  cache_key     TEXT PRIMARY KEY,
  model         TEXT,
  prompt        TEXT,
  response      TEXT,
  input_tokens  INTEGER,
  output_tokens INTEGER,
  cost_usd      REAL,
  created_at    TEXT
);
CREATE TABLE IF NOT EXISTS adjudications (
  run_id      TEXT NOT NULL,
  channel_id  TEXT NOT NULL,
  message_id  TEXT NOT NULL,
  task        TEXT NOT NULL,
  result_json TEXT,
  cache_key   TEXT,
  model       TEXT,
  PRIMARY KEY (run_id, channel_id, message_id, task)
);

-- ── stage 7: register ───────────────────────────────────────────────────────────────────────
-- `latency_excluded_reason` exists because closure detection has THREE outcomes, not two. A
-- reply that changes no state ("Just a follow up of previous messages", threaded onto a
-- broadcast six days later) must not enter the latency distribution as a 6-day response.
CREATE TABLE IF NOT EXISTS issues (
  run_id                   TEXT NOT NULL,
  issue_id                 TEXT NOT NULL,
  anchor_channel_id        TEXT,
  anchor_message_id        TEXT,
  permalink                TEXT,
  raiser_id                TEXT,
  raiser_name              TEXT,
  dc_code                  TEXT,
  entity_tokens_json       TEXT,
  intent                   TEXT,
  intent_source            TEXT,
  informational            INTEGER,
  kapture_ticket_ids_json  TEXT,
  first_response_latency_s REAL,
  latency_excluded_reason  TEXT,
  reply_count              INTEGER,
  closure_signal           TEXT,
  closure_detected_by      TEXT,
  closure_ts               TEXT,
  days_open                REAL,
  state                    TEXT,
  duplicate_of             TEXT,
  related_to_json          TEXT,
  flags_json               TEXT,
  PRIMARY KEY (run_id, issue_id)
);
CREATE INDEX IF NOT EXISTS ix_issues_dc     ON issues(run_id, dc_code);
CREATE INDEX IF NOT EXISTS ix_issues_intent ON issues(run_id, intent);

-- ── stage 8: dedupe ─────────────────────────────────────────────────────────────────────────
-- LINK, never merge. Both issues stay in the register with duplicate_of pointing one at the
-- other, which is how stage 5's over-splitting bias and cross-channel dedupe coexist without
-- contradicting each other.
CREATE TABLE IF NOT EXISTS duplicates (
  run_id       TEXT NOT NULL,
  issue_id     TEXT NOT NULL,
  duplicate_of TEXT NOT NULL,
  method       TEXT,
  confidence   REAL,
  evidence     TEXT,
  PRIMARY KEY (run_id, issue_id, duplicate_of)
);

-- ── provenance ──────────────────────────────────────────────────────────────────────────────
-- `config_hash` is what lets a reader tell whether two runs of the same stage were actually
-- comparable, or whether a yaml changed underneath them.
CREATE TABLE IF NOT EXISTS stage_runs (
  run_id      TEXT NOT NULL,
  stage       TEXT NOT NULL,
  started_at  TEXT,
  finished_at TEXT,
  rows_in     INTEGER,
  rows_out    INTEGER,
  llm_calls   INTEGER,
  cost_usd    REAL,
  config_hash TEXT,
  status      TEXT,
  PRIMARY KEY (run_id, stage)
);

-- ── stage 10: ticket drafts ─────────────────────────────────────────────────────────────────
-- THE PRODUCT. One row per ticket that would be created from the flow of conversation.
--
-- `idempotency_key` is derived from the SOURCE MESSAGE, never minted by a sink: the same Slack
-- message produces the same key on every run, on every machine, through every sink. That is
-- what makes "a retry cannot double-create" true independently of where tickets eventually
-- land -- and it matters because the obvious first sink, this repo's own Concern Log, has no
-- idempotency of any kind (concern_log.append is an unconditional list append).
--
-- A draft is written for EVERY issue, including the ones that must NOT become tickets, with
-- `suppressed` and `suppressed_reason` saying why. Same discipline as the noise gate: "what
-- would we have raised, and what did we hold back" has to be answerable.
CREATE TABLE IF NOT EXISTS ticket_drafts (
  run_id            TEXT NOT NULL,
  issue_id          TEXT NOT NULL,
  idempotency_key   TEXT NOT NULL,
  source_system     TEXT,
  source_id         TEXT,
  source_permalink  TEXT,
  title             TEXT,
  description       TEXT,
  raiser            TEXT,
  dc_code           TEXT,
  intent            TEXT,
  entity_tokens_json TEXT,
  kapture_ticket_ids_json TEXT,
  reply_count       INTEGER,
  first_response_latency_s REAL,
  state             TEXT,
  duplicate_of      TEXT,
  suppressed        INTEGER,
  suppressed_reason TEXT,
  -- RECURRENCE. The motivating story is a hub that raised seven tickets over five months for
  -- one unpaid field executive and got the same canned reply each time. The VALUE is the
  -- number seven -- suppressing occurrences 2..7 destroys exactly the signal the project
  -- exists to capture. So the canonical ticket carries the count and every occurrence's
  -- source, and the later raisings are marked `counted_into` rather than `duplicate`.
  occurrence_count  INTEGER,
  occurrences_json  TEXT,
  first_raised_at   TEXT,
  last_raised_at    TEXT,
  -- Every failed validation check, as a flag. A ticket should never claim more than it can
  -- prove, and a check that fails silently is worse than one that never ran.
  flags_json        TEXT,
  sink              TEXT,
  external_ref      TEXT,
  emitted_at        TEXT,
  PRIMARY KEY (run_id, issue_id)
);
CREATE INDEX IF NOT EXISTS ix_drafts_key ON ticket_drafts(idempotency_key);

-- One row per acknowledgement actually sent. NOT scoped to a run: the whole point is that a
-- re-run must not send again. The live dashboard polls every few seconds, so without this the
-- same partner would be emailed every few seconds — the exact "seven canned replies" failure
-- this project exists to fix, rebuilt by accident and aimed at real people.
--
-- The PK is (idempotency_key, channel), so a ticket can be acknowledged once by email and once
-- by DM, but never twice by either.
CREATE TABLE IF NOT EXISTS notifications (
  idempotency_key   TEXT NOT NULL,
  channel           TEXT NOT NULL,       -- email | slack_dm
  recipient         TEXT,
  status_url        TEXT,
  sent_at           TEXT NOT NULL,
  ok                INTEGER NOT NULL,
  error             TEXT,
  redirected_from   TEXT,                -- set when --notify-to overrode the real recipient
  PRIMARY KEY (idempotency_key, channel)
);

CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT
);
"""


def db_path() -> Path:
    """Resolved store path. `$INTAKE_DB` wins; harnesses and tests MUST set it."""
    return Path(os.environ.get("INTAKE_DB") or _DEFAULT_DB)


def connect(*, readonly: bool = False) -> sqlite3.Connection:
    """A connection to the store, schema already applied.

    `readonly=True` opens `mode=ro` for the reporting paths, matching the house style in
    `substrate/tickets_db.py`. It does NOT create the file — a missing store is an error a
    caller should see, not one to paper over with an empty database.
    """
    p = db_path()
    if readonly:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, check_same_thread=False)
    con.row_factory = sqlite3.Row
    # WAL is the first journal_mode setting in this repo. It is here because this is the first
    # writable store: without it a reader (the report) blocks a writer (a stage) and vice versa.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    con.executescript(DDL)
    con.execute("INSERT OR IGNORE INTO meta (k, v) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),))
    con.commit()
    return con


def reset_stage(con: sqlite3.Connection, stage: str, run_id: str) -> int:
    """Drop one stage's rows for one run so it can be re-run. Returns rows removed.

    This is the mechanism behind "re-run from stage 4 onward with zero API calls": stage 4 and 5
    are deleted and recomputed, while `llm_cache` — which is not run-scoped — is untouched, so
    stage 6 replays from cache instead of calling the API.
    """
    table = STAGE_TABLES.get(stage)
    if table is None:
        raise KeyError(f"unknown stage {stage!r}; known: {', '.join(sorted(STAGE_TABLES))}")
    n = con.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,)).rowcount
    con.execute("DELETE FROM stage_runs WHERE run_id = ? AND stage = ?", (run_id, stage))
    con.commit()
    return n
