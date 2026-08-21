"""Log10Connector — the waybill tracking endpoint, served from fixtures.

    GET /tracking/consignment/v1/{waybillNo}   ->  ServiceResponse<WaybillTrackingResponse>

Seam, matching the `PSP_DATA_PROVIDER` / `PSP_GROWTH_SOURCE` convention:

    PSP_LOG10_SOURCE=fixture   (default) data/log10_fixtures/<AWB>.json
    PSP_LOG10_SOURCE=seed      the three hand-written demo captains (backwards compatible)
    PSP_LOG10_SOURCE=live      raise, naming the endpoint and its auth requirement

`source` reports **"log10-fixture"**, never "log10". That label reaches the captain-facing
sentence for free: `captain_context` puts it in `_sources`, and the Phase-1 composers print
`_sources` into every composed answer.

WHAT `get_shipments` CAN AND CANNOT DO — stated rather than papered over
The GET is per-waybill only. The batch `POST /tracking/consignment/v1` takes
`{waybills[], manifestCodes[]}` and returns a strictly smaller projection (12 of 29 fields
populated). **There is no captain → shipments listing in this API at all.** So under `fixture`
this returns `[]`, and the Phase-1 composer already words that honestly. Inventing a listing
endpoint would be the one dishonest thing in this module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ...seed import SEED
from .dto import Tracking, parse_service_response

_FIXTURES = Path(__file__).resolve().parents[4] / "data" / "log10_fixtures"

FIXTURE, SEED_MODE, LIVE = "fixture", "seed", "live"

TRACKING_ENDPOINT = {
    "url": "/tracking/consignment/v1/{waybillNo}",
    "method": "GET",
    "waybill_pattern": r"^[A-Z0-9](?:[A-Z0-9_]{3,38})[A-Z0-9]$",
    "permission": "FETCH_WAYBILL_TRACKING_BY_ID",
    "auth": "Authorization: Bearer <JWT>",
    # The unusual bit, recorded where a wiring engineer will meet it.
    "note": ("HTTP is ALWAYS 200 for handled errors — the failure is in status.code. Only an "
             "authentication failure escapes as a real 401."),
}


class Log10Connector:
    source = "log10-fixture"

    def __init__(self):
        raw = (os.environ.get("PSP_LOG10_SOURCE") or "").strip().lower()
        # Legacy compatibility: LOG10_API_BASE + LOG10_API_KEY used to be the only signal, and
        # `_live()` raised when both were set. Honour that so an existing deploy behaves the
        # same way rather than silently switching to fixtures.
        if raw not in (FIXTURE, SEED_MODE, LIVE):
            raw = LIVE if (os.environ.get("LOG10_API_BASE") and
                           os.environ.get("LOG10_API_KEY")) else FIXTURE
        self.mode = raw
        self.base = os.environ.get("LOG10_API_BASE", "")
        self.key = os.environ.get("LOG10_API_KEY", "")
        self.source = {FIXTURE: "log10-fixture", SEED_MODE: "log10-seed", LIVE: "log10"}[self.mode]

    # ── health / diagnostics ──
    def status(self) -> dict:
        awbs = self.known_awbs()
        return {"provider": "log10", "mode": self.mode, "source": self.source,
                "ok": bool(awbs) or self.mode != FIXTURE, "fixtures": len(awbs),
                "endpoint": TRACKING_ENDPOINT["url"],
                "detail": "" if awbs or self.mode != FIXTURE else f"no fixtures in {_FIXTURES}"}

    def known_awbs(self) -> list[str]:
        if self.mode != FIXTURE or not _FIXTURES.is_dir():
            return []
        return sorted(p.stem for p in _FIXTURES.glob("*.json"))

    # ── the endpoint ──
    def get_tracking(self, awb: str) -> Tracking | None:
        """The parsed waybill timeline. None when this AWB has no tracking here."""
        raw = self.get_tracking_raw(awb)
        if raw is None:
            return None
        return parse_service_response(raw, waybill_no=(awb or "").strip().upper())

    def get_tracking_raw(self, awb: str) -> dict | None:
        """The response EXACTLY as the wire would carry it — envelope, epoch millis and all.

        Kept separate from `get_tracking` so the harness can assert wire fidelity against the
        contract, and so switching to live means replacing this one method.
        """
        if self.mode == LIVE:
            raise NotImplementedError(
                f"PSP_LOG10_SOURCE=live is not wired. At go-live, call "
                f"GET {self.base or '<LOG10_API_BASE>'}"
                f"{TRACKING_ENDPOINT['url'].format(waybillNo=(awb or '').strip().upper())} "
                f"with {TRACKING_ENDPOINT['auth']} and permission "
                f"{TRACKING_ENDPOINT['permission']}. {TRACKING_ENDPOINT['note']} "
                f"Set PSP_LOG10_SOURCE=fixture to run from files.")
        if self.mode == SEED_MODE:
            return None          # the seed store holds the legacy shape, not the wire shape
        blob = self._load(awb)
        if not blob:
            return None
        # `_provenance` documents which fixture this is; it is stripped so what a consumer sees
        # is byte-identical to the endpoint response. A key the real API never sends must not
        # reach code that will one day read the real API.
        return {k: v for k, v in blob.items() if not k.startswith("_")}

    def provenance(self, awb: str) -> dict:
        """The fixture's `_provenance` block — for the trace and the panel, never for a decision."""
        return (self._load(awb) or {}).get("_provenance") or {}

    # ── the legacy contract, kept working ──
    def get_scans(self, captain_id: str, awb: str) -> dict | None:
        """The pre-existing dict shape, so `data_queries`' `scan_history` arm is unchanged.

        Under `fixture` it is DERIVED from the typed timeline rather than read from a file of
        precomputed booleans — which is the whole point of this module. Under `seed` it is the
        original hand-written blob, so the three demo captains behave exactly as before.
        """
        if self.mode == SEED_MODE:
            return SEED.get(captain_id, {}).get("scans", {}).get(awb)
        t = self.get_tracking(awb)
        if t is None or not t.ok:
            # Fall back to seed for a captain who has one — a fixture-mode deploy should not
            # lose the demo captains' existing scan data.
            return SEED.get(captain_id, {}).get("scans", {}).get(awb)
        from . import event_types as ET
        from . import predicates as P
        inscan = t.first_of(ET.FACILITY_INSCAN)
        connected, _row = P.forward_connection_within(t)
        from ....tri import Tri
        return {
            "awb": t.waybill_no,
            "leg": t.consignment.get("flowType") or "",
            "direction": "Reverse" if (t.flow_type or "").upper() in ("REVERSE", "RTO") else "Forward",
            "inscan_date": inscan.at.strftime("%Y-%m-%d") if inscan and inscan.at else "",
            "hub": (t.consignment.get("location") or {}).get("name") or "",
            "events": [{"scan": e.event_type.name if e.known else e.raw_type,
                        "at": e.at.strftime("%Y-%m-%dT%H:%M:%S") if e.at else "",
                        "node": (e.user or {}).get("name") or "",
                        "ok": e.known}
                       for e in t.events],
            # The legacy key kept its name, but it is now DERIVED, and `connected_within_tat_tri`
            # carries the distinction the boolean cannot: UNKNOWN is not False.
            "connected_within_tat": connected is Tri.YES,
            "connected_within_tat_tri": connected.value,
            "last_status": t.status,
            "_derived_from": "typed scan timeline",
        }

    def get_shipments(self, captain_id: str) -> list[dict]:
        """No captain → shipments listing exists in this API. See the module note."""
        if self.mode == SEED_MODE:
            return SEED.get(captain_id, {}).get("shipments", [])
        if self.mode == LIVE:
            raise NotImplementedError(
                "There is no captain->shipments listing in the tracking API. The GET is "
                "per-waybill; the batch POST /tracking/consignment/v1 takes explicit "
                "{waybills[], manifestCodes[]} and returns only 12 of 29 fields. A shipment "
                "list has to come from a different service (/v1/hubs/shipments on the captain "
                "panel).")
        return SEED.get(captain_id, {}).get("shipments", [])

    # ── internals ──
    def _load(self, awb: str) -> dict | None:
        a = (awb or "").strip().upper()
        if not a or "/" in a or "." in a or "\\" in a:      # no traversal via an AWB
            return None
        path = _FIXTURES / f"{a}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001 — a malformed fixture reads as "no data", never a crash
            return None
