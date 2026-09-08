# Slack-to-ticket intake — phase 1

**A ticket per conversation, derived from the flow of the conversation.** A batch pipeline over
exported Slack NDJSON: it reads `#valmo-firefighters` and `#valmo-lm-ams`, works out where one
issue ends and the next begins, and drafts a ticket for each — with the raiser, the DC code, the
identifiers, the first-response time, and the duplicates already collapsed.

That is the deliverable. **Where the tickets are finally raised is a replaceable decision** —
`TicketSink` in `emit.py` is the whole contract, and PSP is one candidate implementation of it,
not an assumption baked into the pipeline. See
[`docs/psp-ticket-contract.md`](../../../docs/psp-ticket-contract.md).

**Phase 1 creates no tickets, writes nothing to Slack, and has no live ingest.** Not one code
path: `emit.py` imports no HTTP client and a test asserts it. Nothing here runs in the deployed
app.

On the committed fixtures: **43 messages → 23 issues → 20 tickets it would raise, 3 held back**
(two weather callouts, one side of a cross-channel duplicate), for **$0**.

## Run it

```bash
cd backend
. .venv/bin/activate
pip install -r requirements-dev.txt          # pytest, for the tests only

# every implemented stage, over the committed synthetic fixtures
python -m app.intake.cli run-all --raw data/intake/fixtures --out /tmp/intake.xlsx

# over the real export, once you drop it in (one file per day: 2026-08-28.ndjson)
python -m app.intake.cli run-all --raw data/intake/raw --out /tmp/intake.xlsx
```

Every stage is separately runnable against the same store, and re-running one rewrites only its
own rows:

```bash
python -m app.intake.cli extract  --run-id demo    # change a regex, re-run, zero API calls
python -m app.intake.cli group    --run-id demo --join-weak
python -m app.intake.cli evaluate --run-id demo
```

| command | stage | what it does |
|---|---|---|
| `load` | 1 | reads NDJSON, gates it on `tools/validate.js`, writes SQLite. Immutable, idempotent |
| `qualify` | 2 | scores every channel; excludes one **with the number attached** |
| `gate` | 3 | noise gate + informational (weather) classifier. Marks, never deletes |
| `extract` | 4 | mobiles, Kapture ids, waybills, pilot ids, emails, and DC codes in two tiers |
| `group` | 5 | thread → entity join → unassigned, biased toward over-splitting |
| `adjudicate` | 6 | **not implemented** — the only stage that calls a model. See below |
| `register` | 7 | one row per issue, with a state machine |
| `dedupe` | 8 | cross-channel duplicates. Links, never merges |
| `emit` | 10 | **drafts a ticket per issue.** Creates nothing — dry-run sink only |
| `report` | 9 | the xlsx |
| `evaluate` | — | scores against `data/intake/golden/labels.csv` |

## Read the report

Six sheets. Read them in this order:

0. **`tickets`** — the product. One row per issue: `would_raise` yes/no, the title, and
   `held_back_because` for the ones it stopped. This is the sheet to show someone who asks what
   the thing does.

1. **`summary`** — issue counts by intent, % with no closure signal, median and p90 first
   response, repeat issues per DC, cross-channel duplicates, gated counts by rule.
2. **`qualification`** — every channel with its identifier rate and the reason it is in or out.
   This is the sheet to check when someone asks to add a channel.
3. **`register`** — one row per issue. `latency_excluded_reason` is the column to read before
   trusting any latency figure: a blank `first_response_s` with a reason is honest, and a
   number with no reason is a real measurement.
4. **`gated`** — what the filter threw away, and which rule caught it. Read this after any
   change to `config/noise.yaml`.
5. **`informational`** — weather and operational callouts, with **BORDERLINE** flagged. The
   share is deliberately reported as a range, not a number.

## Real vs synthetic data

**Everything in `data/intake/fixtures/` is synthetic.** Invented names, invented emails,
mobiles that start `99000` so a real number can never be mistaken for one. The DC codes *are*
the real ones observed in the channels, because the point of the fixture is to assert the exact
token set they produce.

`data/intake/golden/labels.csv` is generated from those fixtures and every row is marked
`source=synthetic`. **A score against it means the pipeline agrees with the fixture author** —
that is a test of the metric code, not evidence about the channels. `evaluate` prints a
`CAVEAT` saying so. Hand-labelled real rows append to the same file with `source=real` and are
reported separately.

`data/intake/raw/` is empty and waits for the real export.

## Known gaps

- **The real corpus is not here.** The 490-record export does not exist on this machine. Every
  number below stage 8 is measured on fixtures until it lands.
- **`docs/message-schema-v1.md` is missing.** `docs/field-map.md` and `tools/schema.json` cover
  field semantics and key order; that one doc is the only unread part of the contract.
- **`attachments` and `reactions` are structurally empty in the 467-record portion**, and
  `edited_ts` is always null, because the tool that produced it could not read them. **The
  contract validator cannot detect this** — `attachments: []` with `has_media: false` satisfies
  its agreement check, so a lossy export passes green. `load()` therefore reports attachment
  coverage as a number. Never treat empty `attachments` as meaning no files exist.
- **The DC registry is a seed, not the ops master.** `config/dc_codes.txt` holds 11,723 codes
  derived from `valmo.db` and `tickets.db` — all *observed in transactional data*, so a hub
  with no history is absent, and 5 of the 25 codes seen in Slack (`L9D`, `T5X`, `RX3`, `R2F`,
  `AML`) are missing. `evaluate` reports registry coverage separately so that never reads as
  poor regex recall. **Ops still owes names and cities** — no code→name mapping exists anywhere
  in the repo.
- **No sink is implemented.** `emit` drafts; nothing raises. That is phase 1 working as
  specified, not a gap — the payload and the key are the parts that are painful to retrofit,
  and they are done.
- **Stage 6 is not implemented**, so nothing reaches `RESOLVED` or `CLOSED` and intent is
  `(unadjudicated)`. That is deliberate: run `evaluate` and read
  `THE_NUMBER_grouping_without_stage_6` first. If deterministic grouping is good enough, stage
  6 is optional and this project gets substantially smaller.
- **The 90-day backfill needs a Slack token** — a user token (`xoxp`) held by a channel member,
  with `groups:history`, `users:read` and `users:read.email`. A bot token will not work for
  thread replies.

## Lifting this out of PSP

It is close to standalone, and deliberately so.

- **No network and no PSP database at runtime.** Every stage reads its own SQLite and its own
  `config/*.yaml`.
- **Exactly one cross-package import**: `app/intake/extract.py` → `app.engine.algo.entities`.
  Take that one file and the module runs anywhere. It is the shared home for hub-code
  extraction and the measured `_NOT_AN_ID` stoplist, which is why it is shared rather than
  copied.
- **Two build-time imports**, both in `scripts/build_dc_registry.py`
  (`entities._NOT_AN_ID` and `followups.GLOSSARY`). Their output is a plain text file, so once
  `config/dc_codes.txt` and `config/dc_denylist.txt` are generated, nothing needs them again.
- **The DC registry is seeded from `valmo.db` / `tickets.db`** — but only by that script, once.
  Elsewhere, point it at whatever hub master exists, or paste the ops list in.

So the extraction is: `app/intake/`, `config/*.yaml`, `config/dc_*.txt`, `tools/validate.js`,
`tests/intake/`, and `app/engine/algo/entities.py`.

## Two things that will bite you

**The store path is `$INTAKE_DB`.** `scripts/_contain.py` copies `*.json` into the harness
tmpdir but **symlinks directories and `*.db`**, on the assumption that `.db` files are static.
This is the repo's first writable database, so a harness or test that does not override
`$INTAKE_DB` writes straight through to the real store. `scripts/check_intake.py` sets it.

**`data/raw/` must be day-partitioned.** `tools/validate.js` derives each record's IST day from
its `message_id` and checks it against a `YYYY-MM-DD` in the filename. It also runs a
corpus-level orphan check, so the loader validates the **whole directory at once** — never file
by file, or every reply threaded across midnight fails.

## What phase 2 needs

See [`docs/psp-ticket-contract.md`](../../../docs/psp-ticket-contract.md). Short version: there
is no ticket-create endpoint in this repo, no idempotency key anywhere, and `tickets.db` and the
Concern Log do not join.

## Why a service and not a skill

See [`docs/why-not-a-skill.md`](../../../docs/why-not-a-skill.md).
