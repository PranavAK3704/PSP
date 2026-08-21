"""Write mode — whether a money action is actually written anywhere. It is not.

THE FACT THIS MODULE EXISTS TO STATE
`tools._act()` returned `applied: True` and performed no I/O of any kind — no DB write, no
HTTP call, no queue publish. It was a string formatter. The trace said "ACT — idempotent
write". Nothing was ever written.

And it could not have been. Reading the LossManagementService source: **there is no HTTP
endpoint for creating a loss adjustment or a reversal.** No `/loss_adjustment/create`, no route
registration of any kind. Reversals are effected by **Kafka consumers and scheduler crons** —
`LossAttributionMessage` on a topic, with ADR-0015 recording that payouts are emitted from a
scheduler cron rather than a worker on state change. The five write endpoints that do exist are
a COD-pendency CSV upload, two shipment-scan admin routes, a BTS cron trigger, and an
auto-payout backfill flag. None of them move a rupee for a captain.

So PSP is not missing an integration. There is nothing to call. The honest end state is a Kafka
producer, and until that exists every "reversal" this engine reaches is a RECOMMENDATION.

WHY `live` IS ACCEPTED AND THEN REFUSES
A mode that silently behaves like `simulated` is the exact failure this file prevents: someone
sets `WRITE_MODE=live` at deploy time, believes money now moves, and nothing tells them
otherwise. So `live` is a legal value that raises with the reason, in the same spirit as
`Log10Connector._live()`'s `NotImplementedError("Wire … at go-live")`. The error names the real
mechanism so whoever reads it knows what to build, not just that something is missing.
"""
from __future__ import annotations

import os

SIMULATED = "simulated"
LIVE = "live"
_VALID = (SIMULATED, LIVE)

# Why there is no write path, phrased for whoever hits the exception. Kept as one constant so
# the API error, the log line and the trace all say exactly the same thing.
NO_WRITE_PATH = (
    "WRITE_MODE=live is not implementable: LossManagementService exposes NO HTTP endpoint for "
    "creating a loss adjustment or reversal. Reversal is a LossAttributionMessage on a Kafka "
    "topic, consumed by LMS's own scheduler cron (ADR-0015). PSP has no producer, no topic "
    "credentials, and no authority to publish one. Until that exists, a money decision here is "
    "a RECOMMENDATION to L2 — set WRITE_MODE=simulated (the default)."
)


class WriteNotAvailable(RuntimeError):
    """Raised when a write is requested in a mode that cannot write.

    A RuntimeError subclass so `tools.dispatch`'s existing broad handler in
    conversation.py turns it into a tool-level error rather than a crashed turn.
    """


def mode() -> str:
    """The active write mode. Unknown values fall back to `simulated`, never to `live` —
    a typo must not be the thing that decides money moves."""
    raw = (os.environ.get("WRITE_MODE") or "").strip().lower()
    return raw if raw in _VALID else SIMULATED


def is_simulated() -> bool:
    return mode() == SIMULATED


def assert_writable() -> None:
    """Call before anything that claims to have written. Always raises today."""
    if mode() == LIVE:
        raise WriteNotAvailable(NO_WRITE_PATH)
