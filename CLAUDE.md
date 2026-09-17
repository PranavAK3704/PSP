# CLAUDE.md — decisions taken while building, and why

Working notes for the Slack intake module (`backend/app/intake/`). Each entry is a decision
that a reader might otherwise reverse by accident.

## Repo shape

- **`backend/` is not an installable package.** Scripts reach the app with
  `ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))`. There is no
  `pyproject.toml` and no console-scripts mechanism.
- **Prod is Python 3.12** (`Dockerfile`), the dev venv is **3.14.6**. Nothing pins a version, so
  3.13-only syntax passes locally and breaks the Render build.
- **The repo had no tests before this module.** Verification is `backend/scripts/check_*.py`
  harnesses registered in `check_all.py` — "a harness that is not in this list does not exist".
  Intake ships **both**: pytest under `backend/tests/intake/` (names the failing assertion) and
  `scripts/check_intake.py` (no third-party dependency, registered in `check_all.py`).
- **pytest is pinned in a new `requirements-dev.txt`, not `requirements.txt`**, because the
  Dockerfile installs the latter and a test framework does not belong in the image.
- There is **no linter and no CI**, but the code is written as if ruff were watching (145
  `# noqa` comments). New code matches that.

## Storage

- **`intake.db` is local and build-time only.** `*.db` is gitignored and dockerignored, and
  `scripts/push_to_turso.py` recreates tables as all-TEXT with no constraints — which would
  drop the composite primary key. **The pipeline's store is not reachable from the API:**
  `main.py` mounts `intake_api`, but that router reads `durable_state` only — it never
  touches `intake/store.py`, `intake/labels.py` or `intake/notify.py`. So `INTAKE_DB`,
  `INTAKE_LABELS` and the `SMTP_*` vars are build-time only and are deliberately absent
  from `render.yaml`.
- **The path comes from `$INTAKE_DB`.** `_contain.py` symlinks directories and `*.db` into the
  harness tmpdir, so a subdirectory does not help; without the override a harness writes
  through to the real store. This is the repo's first writable database.
- **`durable_state.py` is not usable here.** It stores one opaque JSON blob per filename key,
  whole-file read/write. `state_paths.py` explicitly excludes `*.db`.
- **The primary key is `(channel_id, message_id)`.** Not `message_id` alone: two channels carry
  the same Slack ts and cross-posting is confirmed real.
- **`llm_cache` is not scoped to `run_id`** — that is what makes a re-run cost nothing.

## Extraction

- **`entities.py` was EXTENDED, not duplicated.** `_ok_id()` gained `label_anchored`, defaulting
  to the existing behaviour, so the live conversation path is byte-identical (verified across 20
  cases; `check_dataplane`, `check_router` and `check_phase1` still pass).
- **The digit requirement is corpus-specific.** It was measured on partner Hinglish chat where
  it holds. Internal ops Slack is a different register: most real DC codes there are purely
  alphabetic, so POSITION (an adjacent `DC`/`Hub` label) replaces shape.
- **The token guard is `(?<![A-Za-z0-9])`, not `\b`.** `_` is a Slack italics marker *and* a
  regex word character, so `\b[A-Z0-9]{3}\b` can never match inside `_UB1_`. The brief requires
  both `_` as a separator and standalone-word guarding; under `\b` those are incompatible.
- **The leading separator in the forward scan is optional.** `_DC_LABEL` ends with
  `\s*(?:Code)?s?\b` and consumes the space, so requiring one made the forward direction extract
  nothing while backward worked perfectly.
- **`:` is a separator** even though the brief's list omits it — its own canonical example is
  `"DC Code: NXG"`.
- **Registry membership is not sufficient evidence.** `config/dc_codes.txt` contains `SIR`,
  `RVP`, `TID`, `FMH`, `SOP`, `ALL` and `OLD` as genuine hub codes. Tier B needs the registry
  AND `config/dc_denylist.txt` AND case-sensitivity.
- **`INV` is deliberately NOT denied.** It is in the registry, it is the obvious abbreviation
  for "invoice", and the brief records it as a real bare code. An observed code outranks an
  acronym guess.
- **Numeric identifiers use alphanumeric lookarounds** because they are distinguished by exact
  length: an unguarded `[6-9]\d{9}` finds a "mobile" inside a 13-digit Kapture id.

## Grouping

- **`dc_code` is NOT in `join_on` by default.** A DC code identifies a PLACE, not an incident;
  joining on it merges every issue a hub raised in the window into whichever came first. A
  fixture prices it: enabling it drops lm-ams grouping accuracy from 1.00 to 0.93.
- **Stage 5 never merges across channels.** Stage 8 links with `duplicate_of` so both forked
  threads survive.
- **The qualification gate is enforced in `group.py`, not just reported.** It was excluding
  `pilot_support_ams` and letting its messages through anyway.

## Ticket emission (stage 10)

- **The product is a ticket per conversation; the sink is replaceable.** PSP may not be the
  system this ends up feeding, so the pipeline commits to `TicketDraft` + `TicketSink` and
  nothing else. Phase 1 ships only `DryRunTicketSink`.
- **`idempotency_key = sha256(source_system, channel_id, anchor_message_id)`** — derived from
  the source, never minted by a sink, so it is stable across runs, machines and sinks. This is
  the module supplying what PSP lacks: `concern_log.append()` has no idempotency and there is no
  `external_ref` field to dedupe on.
- **A draft is written for every issue, including suppressed ones**, with the reason. Same rule
  as the noise gate: "what would we have raised, and what did we hold back" must be answerable.
- **Informational and duplicate issues are suppressed by default; `require_identifier` is
  off.** Whether "no DC code, no mobile, no waybill" disqualifies a ticket depends on how the
  desk works, so it is a flag rather than a rule.
- **Titles and descriptions are deterministic — no model.** The raiser already wrote the
  summary; the DC code leads because that is what a triager asks first. This keeps the whole
  ticket path at $0.
- **`emit.py` imports no HTTP client, and a test asserts that.** The "no calls to any support
  backend" constraint is kept structurally, not by intention.

## Cost and models

- **`models.yaml` already has `provider: claude`** (`fast: claude-sonnet-5`,
  `deep: claude-opus-5`). The README and `.env.example` still say OpenAI — they are stale.
- **`app/llm/meter.py` already halts before a call** with `BudgetExhausted`, against
  `LLM_BUDGET_USD` (default 45.0). But **every batch call site catches broad `Exception`** and
  continues, so it degrades instead of halting.
- **Intake must NOT share `llm_spend.json`.** A backfill would eat the deployment's chat budget.
- **Measured cost of a full cold run: ~$1.51**, of which **93% is stage 6b/c** (per-message
  Sonnet calls). The Batch API halves it. The `$5` cap gives ~3× headroom, not 30×.
- **The canonical Haiku id is `claude-haiku-4-5`**, and Haiku 4.5 **rejects
  `output_config.effort`**.

## Data facts worth not re-deriving

- Kapture ticket ids are **12–13 digits** (confirmed: 116,528 × 13, 24,347 × 12).
- `tickets.db` `folder_l1` is the **transport**, and `sub_type` is empty on **98.8%** of rows —
  it cannot seed an intent taxonomy. `governance_framework.json` self-identifies as a
  placeholder.
- `losses.location` and `qc_fail.hub_location` are **lowercase**; `tickets.hub_code` and
  `attribution.entity_id` are **uppercase**. Always `UPPER()` before a union.
- `losses.location` is **mixed** — it also holds 11-digit partner IDs and the literals `PUN-DC`
  and `meesho`. `LENGTH = 3` is what keeps PII out of a "DC registry".
- `attribution.metadata_party_name` looks like a hub-name column and is exactly
  `lower(entity_id)` in 100% of rows.
- **The repo holds two opposing positions on whether a DC code is PII**: `dataplane.py` says no
  ("a hub is a facility, not a person"); `build_valmo_db.py`'s `_is_pii_col()` says yes for
  `entity_id`. Intake follows the data plane.

## Portability and the human loop (2026-09-16)

- **`source_system` is half the idempotency key**, so a schema v2 record must declare it and
  must never default. An unlabelled record from a new surface would mint a Slack-shaped key and
  the retry guarantee would stop holding with nothing failing.
- **Slack-only validator checks are kept FOR SLACK, not deleted.** Each caught a real bug, so
  v2 dispatches them on `source_system` in `SURFACES` rather than relaxing them corpus-wide. A
  surface with no rules is rejected, never measured against Slack's timestamp format.
- **`channel_id` means THE CONTAINER** — Slack channel, WhatsApp group, email thread. Not
  renamed: the portability problem was the hard-coded Slack *formats*, not the word.
- **`validate.js`'s per-record loop is at module top level, so `continue` — never `return`.** A
  `return` exits the whole module: no summary, no errors, exit 0, and one bad record silently
  passes the entire corpus.
- **Envelope fields are not content.** `fetched_at` changes on every pull, so comparing it made
  a re-pull report all 56 fixture messages as conflicts and buried real edits.
- **Human confirmations are the only data here nothing can regenerate.** They live in
  `data/intake/labels/`, outside every generated file, because `build_exemplars.py` overwrites
  its output and a label written there would vanish on the next rebuild with nothing erroring.
- **`gold_weight` is what makes the NOVEL queue more than theatre.** Matching sums the top-k per
  disposition, so one fresh human label loses on volume to a class holding 80 silver exemplars.
  With no confirmations it multiplies by 1.0 and cannot move today's numbers.
- **Every store path resolves at call time, not as a default argument.** A default bound at
  import cannot be redirected, which let a test write a real label into the real store.

## Still open

- The real 490-record export, and `docs/message-schema-v1.md`.
- Stage 6, the prompt cache and the run-scoped spend guard — gated on
  `THE_NUMBER_grouping_without_stage_6`.
- Hand-labelled real rows in `data/intake/golden/labels.csv` with `source=real`.
- Hub names/cities from ops.
