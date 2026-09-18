# CLAUDE.md — decisions taken while building, and why

Working notes for this repo. Each entry is a decision a reader might otherwise reverse by
accident, or a measured fact that cost real time to establish.

## Repo shape

- **`backend/` is not an installable package.** Scripts reach the app with
  `ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))`. There is no
  `pyproject.toml` and no console-scripts mechanism.
- **Prod is Python 3.12** (`Dockerfile`), the dev venv is **3.14.6**. Nothing pins a version, so
  3.13-only syntax passes locally and breaks the Render build.
- **Verification is `backend/scripts/check_*.py` harnesses registered in `check_all.py`** — "a
  harness that is not in this list does not exist". There is no pytest suite and no test
  dependency; the one that existed belonged to the retired Python intake and went with it.
- There is **no linter and no CI**, but the code is written as if ruff were watching. New code
  matches that.

## The Slack intake lives in `appscript/`, not here

The Slack→ticket pipeline was rebuilt in Google Apps Script and the Python implementation was
deleted (`git log -- backend/app/intake` to read it). It was not a preference — every piece of
infrastructure under the Python version failed: Turso reads hit a quota wall so nothing
persisted, Render's free tier spun down so polling stopped, and SMTP 587 timed out from Render's
egress. One Google account now owns storage, scheduling, email and the UI.

Consequences for anyone working in this repo:

- **Nothing in `backend/` imports anything intake.** `main.py` mounts no intake router and starts
  no poller. The `agent` role is gone from `auth/store.py` — it existed only to work the
  register.
- **`appscript/` depends on no Python.** That was the point of the break. The one script there,
  `tools/build_seed.py`, imports nothing from `backend/`.
- **`backend/data/intake/` still exists on disk and is gitignored.** The code that wrote it is
  gone, but `corpus/exemplars.json` is the source `build_seed.py` rebuilds the Sheet's exemplar
  tab from. Do not delete it.
- Port hazards, golden vectors and the capacity arithmetic are in `appscript/README.md`.

## Cost and models

- **`models.yaml` has `provider: claude`** (`fast: claude-sonnet-5`, `deep: claude-opus-5`). The
  README and `.env.example` still say OpenAI — they are stale.
- **`app/llm/meter.py` halts before a call** with `BudgetExhausted`, against `LLM_BUDGET_USD`
  (default 45.0). But **every batch call site catches broad `Exception`** and continues, so it
  degrades instead of halting.
- **The canonical Haiku id is `claude-haiku-4-5`**, and Haiku 4.5 **rejects
  `output_config.effort`**.

## Data facts worth not re-deriving

These are about the databases, not about any one feature, and all of them still hold.

- Kapture ticket ids are **12–13 digits** (confirmed: 116,528 × 13, 24,347 × 12).
- `tickets.db` `folder_l1` is the **transport**, and `sub_type` is empty on **98.8%** of rows —
  it cannot seed an intent taxonomy. `governance_framework.json` self-identifies as a
  placeholder.
- `losses.location` and `qc_fail.hub_location` are **lowercase**; `tickets.hub_code` and
  `attribution.entity_id` are **uppercase**. Always `UPPER()` before a union.
- `losses.location` is **mixed** — it also holds 11-digit partner IDs and the literals `PUN-DC`
  and `meesho`. `LENGTH = 3` is what keeps PII out of a hub registry.
- `attribution.metadata_party_name` looks like a hub-name column and is exactly
  `lower(entity_id)` in 100% of rows.
- **The repo holds two opposing positions on whether a DC code is PII**: `dataplane.py` says no
  ("a hub is a facility, not a person"); `build_valmo_db.py`'s `_is_pii_col()` says yes for
  `entity_id`. The data plane is the one to follow.

## Lessons that outlived the code that taught them

Kept because they are about how this repo fails, not about the intake pipeline specifically.

- **A store path must resolve at call time, not as a default argument.** A default bound at
  import cannot be redirected, which once let a test write into the real store.
- **Envelope fields are not content.** A timestamp that changes on every pull will report every
  record as a conflict and bury the real edits.
- **A per-record loop at module top level needs `continue`, never `return`.** A `return` exits
  the whole module: no summary, no errors, exit 0, and one bad record silently passes the entire
  corpus. That bug shipped once.
- **Identity must be derived from the source, never counted.** `len(rows) + 1` collides under
  concurrency and "one past the highest" reuses ids after a delete. Both shipped before the
  current source-derived hash.
- **Measured beats stated.** Several numbers in the original brief were wrong, and so was a
  capacity estimate in this repo's own plan. When a measurement disagrees with a document, the
  measurement wins and the document gets corrected.

## Still open

- Hub names and cities from ops, to make the DC registry readable.
- **A semantic tier for Devanagari.** The Apps Script classifier tokenises on `[a-z0-9]+`, so a
  message in Devanagari yields no tokens, scores 0.00 against every disposition and always
  routes to a human. Measured, not accidental — BM25 is lexical and cannot match across scripts.
  What the prior art said, so it does not have to be re-researched: the datasets exist
  (Hinglish-TOP, MASSIVE, AI4Bharat) but nothing solves this use case with rules; every serious
  implementation is a trained model. Rule-based transliteration fails outright — `पेमेंट` becomes
  `pememta`, a spelling nobody types. A multilingual embedding model scored 0.937 cosine on the
  exact message BM25 scores 0.00 on, and needs TWO similarity floors rather than one, because
  cross-lingual pairs sit systematically lower (0.663 vs 0.759).
- Whether to merge the five money dispositions — measured at **+17.9 precision points for zero
  coverage cost**, and shipped as a config switch that is currently off.
