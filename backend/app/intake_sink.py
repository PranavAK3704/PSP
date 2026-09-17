"""A ticket sink that writes straight into PSP's own store.

The CLI's `PspTicketSink` POSTs over HTTP because it runs on a laptop. Inside the deployed app
that would mean the process authenticating to itself and issuing a loopback request Render's
router has to serve — a second credential to manage and a network hop to debug, for data that is
already in reach.

Same contract as every other sink (`create(draft) -> external_ref`) and the same guarantee: the
key is derived from the source message, and `intake_api` returns the existing ticket rather than
creating a second one, so a re-run after a restart creates nothing.
"""
from __future__ import annotations

from dataclasses import asdict


class InProcessTicketSink:
    name = "psp_inprocess"

    def __init__(self, created_by: str = "intake-poller"):
        self.created_by = created_by
        self.created = 0
        self.already_existed = 0
        self.status_urls: dict[str, str] = {}

    def create(self, draft) -> str:
        from . import intake_api
        payload = asdict(draft) if hasattr(draft, "__dataclass_fields__") else dict(draft)
        r = intake_api.create_ticket_record(payload, created_by=self.created_by)
        if r["created"]:
            self.created += 1
        else:
            self.already_existed += 1
        self.status_urls[payload["idempotency_key"]] = f"/t/{r['status_token']}"
        return r["ref"]
