# The PSP ticket contract — what phase 2 would post to

**Investigated 8 Sep 2026 against `phase2-router`. Documentation only; phase 1 writes no
ticket-creation code.**

## The answer

**There is no ticket-create entry point in this repo.** Nothing a pipeline can POST to will
produce a case. The word "ticket" here means one of two things and neither is writable.

---

## What exists

### 1. `backend/data/tickets.db` — read-only analytics, not a ticket system

- 140,875 rows, one `tickets` table, 17 columns, **no primary key and no id generator**.
- Produced **offline** by `backend/scripts/build_tickets_db.py` from a Kapture xlsx export on
  someone's laptop. Rebuilt when a new export lands.
- Opened by `app/substrate/tickets_db.py` as `file:...?mode=ro`. Its own docstring says the
  point is that it *cannot* write, so wiring it in adds no failure surface.
- Its only routes are `GET` aggregates.
- Useful facts confirmed here: `ticket_no` is **13 digits (116,528) or 12 digits (24,347)**,
  which is where the intake regex gets its shape; `hub_code` holds 3,959 distinct DC codes;
  `source` is the transport (WhatsApp 114,967 / Web Form 14,742 / Inbox 6,370), and `sub_type`
  is **empty on 98.8% of rows**, which is why it cannot seed an intent taxonomy.

### 2. The L3 escalation platform — an inbox and a resolve, but no open

`app/l3/platform.py` is a **projection over the append-only Concern Log**, not a store.

- An item "enters" L3 only when some writer appends a Concern row with `outcome == "escalated"`.
- Routes: `GET /api/l3/inbox`, and `POST /api/l3/resolve`, which closes an existing case and
  returns 400 `concern not found` for an unknown id.
- **There is no create route.** No `POST /api/l3/escalate`, no `POST /api/tickets`, no
  `POST /api/concern`. Every one of the app's 25 mutating routes is a `POST` and none of them
  opens a case directly.

### 3. The only creation mechanism is a side effect of an LLM turn

To file a case today a caller must `POST /api/chat` with a natural-language `message` and hope
the model chooses to call `escalate_case`. That is:

- non-deterministic — the same input may or may not file,
- **SSE-only** — the case id comes back inside a trace event, not in a JSON body,
- a paid model call per attempt,
- and gives the caller no way to set team, severity or SLA.

It is not an API a pipeline should depend on.

---

## What phase 2 needs

| # | Requirement | Why, concretely |
|---|---|---|
| 1 | **A real create endpoint** — `POST /api/l3/case` taking a typed body and returning the case id in JSON | Nothing else lets a batch job file deterministically |
| 2 | **An idempotency key** | There is **no idempotency anywhere in the repo** — `grep` for `idempot` returns nothing. `concern_log.append()` is an unconditional list append with a server-minted `CNC-<uuid8>` id and no uniqueness check. A retried call — a timeout, a 502, an at-least-once queue — produces a second case with a different id and nothing downstream can collapse them |
| 3 | **A natural key on the record** — `source_system` + `source_id` | There is no `external_ref` field, so even with an idempotency key there is no way to ask "does a case already exist for Slack message `1788482771.760339`?" |
| 4 | **A `source` value for pipeline-filed rows** | `SOURCES` in `ledger/concern_log.py` is a **closed tuple** enforced at append; an unknown value silently downgrades to `unclassified`. Worse, `l3.is_workable()` drops rows whose source is `harness` or `monitor` — so a wrongly-labelled case can be filed, be invisible to the desk, and still tell the raiser it is "with the team" |
| 5 | **A machine identity** | Auth is user-shaped: email/password → a 12-hour HMAC token, no API key and no service account. A pipeline would need a seeded account and re-login every 12 hours. If `AUTH_SECRET` is unset the signing secret is random per process, so tokens die silently on every restart |
| 6 | **A durable store for the ledger** | The Concern Log is a single JSON blob rewritten in full on every append (already 758 KB / 1,033 rows) behind a process-local lock, mirrored to Turso KV. Machine-rate intake would rewrite the whole file per case, with O(n) write cost and no cross-process safety |
| 7 | **A join between Kapture and the Concern Log** | They do not join. `tickets.db` keys on a 13-digit `ticket_no`; the Concern Log keys on `CNC-<uuid8>`. There is no cross-reference column in either direction, so a new case cannot be linked to the historical ticket that motivated it |

## Kapture

There is **no Kapture API client, base URL, token or credential anywhere in the repo**. The
eight `/api/kapture/*` audit routes that existed on `main` have been deleted from the working
tree. The only `kapturecrm.com` string in the codebase is inside a Hinglish reply telling a
partner to use the self-serve portal themselves.

So intake's stage 8 matches on Kapture ticket ids **scraped from message text** — around half
of Slack posts already carry one — behind the `KaptureDedupeSource` protocol in
`app/intake/dedupe.py`. The real API drops into that seam without stage 7 changing.

## What intake does in the meantime

Nothing. Phase 1 creates no tickets and calls no support backend, by design. The register is
the artefact; `duplicate_of` records what stage 8 found; and the xlsx is a copy for humans to
read, not the way tickets travel.
