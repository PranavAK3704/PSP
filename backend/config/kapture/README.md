# `config/kapture/` — placeholder

## What this is

Where Kapture API configuration will live when there is a Kapture API to configure.

**There is none today.** This repo has no Kapture client, no base URL, no token and no
credential — verified by grep across the whole tree. The eight `/api/kapture/*` audit routes
that once existed on `main` have been deleted from the working tree. The only `kapturecrm.com`
string left in the codebase is inside a Hinglish reply telling a partner to use the self-serve
portal themselves.

`backend/data/tickets.db` is **not** an integration. It is a 140,875-row offline dump of a
Kapture xlsx export, built on a laptop by `scripts/build_tickets_db.py` and opened read-only.

## What production needs

1. **An API token**, stored the way every other secret here is — a gitignored file under
   `backend/data/` (mode 600) or an env var, never committed. In production these move to the
   platform secret store (Vault), per `PRODUCTION_DELTA.md`.
2. **Prod-vs-demo environment handling.** A `kapture.yaml` in this directory with a `base_url`
   per environment and an explicit `environment:` key, so a demo run can never be pointed at
   the production CRM by forgetting a flag. Follow `config/models.yaml`: env vars override the
   file, so a swap is a deploy change rather than a file edit.
3. **A read scope only, for now.** Stage 8 asks "does a ticket already exist for this issue?".
   It does not create tickets, and phase 1 must not be able to.

## How the demo substitutes

The retired Slack intake defined the seam this way (kept as the shape any future
integration should follow):

```python
class KaptureDedupeSource(Protocol):
    def known_ticket_ids(self, issue: dict) -> set[str]: ...
```

The shipped implementation is `TextScrapedKaptureSource`, which reads the Kapture ticket ids
people typed into the message themselves. That is useful rather than a stub: **around half of
Slack posts already carry a ticket number**, and the format is confirmed against real data —
12–13 digits, from 140,875 rows in `tickets.db`.

The real client implements the same protocol and is passed to `dedupe.run(source=...)`. Stage 7
does not change.

## Why a token is not enough on its own

`tickets.db` keys on a 13-digit `ticket_no`; the Concern Log keys on `CNC-<uuid8>`. **There is
no cross-reference column in either direction.** So even with a live Kapture read, linking an
intake issue to the historical ticket that motivated it needs a natural key on the PSP side
too.
