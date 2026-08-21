"""The WaybillTrackingResponse shape, and the ONE place epoch-millis becomes a datetime.

SOURCE
`log10-backend-valmo-develop :: TrackingService/src/main/java/com/loadshare/tracking/entities/`
— `response/WaybillTrackingResponse.java`, `dto/WaybillTrackingDTO.java`,
`response/ConsignmentTrackingResponseDTO.java`, `dto/DRSDTO.java`, and the shared
`Log10Core .../dto/{LocationDTO,PartnerDTO,UserTrackingDTO}.java`.

    GET /tracking/consignment/v1/{waybillNo}  ->  ServiceResponse<WaybillTrackingResponse>
    { status: {code, message, customErrorCode, secondaryMessage},
      response: { tracking: [WaybillTrackingDTO], consignment: {...}, drses: [DRSDTO] } }

THE ONE BOUNDARY
`eventTime` is a `java.util.Date`, which Jackson writes as an epoch-MILLISECONDS integer. Every
conversion between that and a `datetime` happens in this module and nowhere else. The reason is
not tidiness: a predicate that does its own `/1000` is a predicate that can get the unit wrong
by three orders of magnitude, and a date rule off by 1000× reads as "the connection happened
46 years late" — which is a wrong money decision dressed as arithmetic.

THINGS THAT LOOK LIKE TYPOS AND ARE NOT
  · `totalChargableWeight` — one `e`. It is the wire name.
  · `podImpLink` — "Imp", not "Img". `TrackingService/schemas/openapi.yml` also omits `pdfLink`
    from PODImageDTO, but the Java class emits it, and Java wins.
  · `consignmentPODBO` is the JSON key while the TYPE is `List<PODImageDTO>`. There is a real
    `ConsignmentPODBO` class upstream; it is unrelated to this field and is not mirrored.
  · `shipper.primaryNumber` / `secondaryNumber` are hardcoded to the literal `"0000000000"` by
    the real mapper (`mapContact`), regardless of what the DB holds. A fixture with a
    real-looking number would be LESS faithful, not more.

ERROR SEMANTICS, which are unusual and matter
The service returns **HTTP 200 for handled errors** and puts the failure in `status.code`; only
an authentication failure escapes as a real 401. So a caller that only checks the HTTP status
sees success on a 500. `parse_service_response` reads `status.code`, which is the only correct
place to look.

`data` on each event is declared `private Object data` upstream — genuinely polymorphic, with a
17-entry class-name registry deciding its shape per event type. It is modelled as an untyped
dict and **nothing in PSP reads its contents**. Only `eventType` and `eventTime` are safely
typed, so only those are depended on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .event_types import (FACILITY_INSCAN as FACILITY_INSCAN_FOR_LEGACY,
                          TrackingEventType, from_string)

# Keys the real response carries. Kept as data so the harness can assert a fixture against the
# contract without a schema library.
CONSIGNMENT_FIELDS = (
    "id", "waybillNo", "description", "totalShipmentCount", "totalWeight",
    "totalChargableWeight", "consignmentAmount", "payableAmount", "expectedDeliveryDate",
    "location", "partner", "partnerId", "customerPickupLoc", "bookingDate", "bookingOfficeLoc",
    "consignmentStatus", "flowType", "updatedAt", "customerPromiseDate", "attempts", "shipper",
    "consignee", "shipperPincode", "consigneePincode", "customerId", "customerName",
    "customerPhoneNumber", "customerEmailId", "consignmentPODBO",
)
TRACKING_FIELDS = ("eventTime", "eventType", "user", "data")
DRS_FIELDS = ("id", "drsCode", "drsUser")

#: What `mapContact` hardcodes. Asserted in the harness so a fixture cannot drift into carrying
#: a plausible-looking phone number that the real API would never return.
MASKED_NUMBER = "0000000000"


# ── the epoch-millis boundary ────────────────────────────────────────────────────────────────

def to_datetime(ms: Any) -> datetime | None:
    """Epoch milliseconds → aware UTC datetime. None when unreadable.

    Returns None rather than a sentinel date so a missing timestamp propagates as UNKNOWN
    through the predicates instead of silently becoming 1970, which would make every "within N
    days" test resolve to a definite NO.
    """
    if ms is None or isinstance(ms, bool):
        return None
    if isinstance(ms, datetime):
        return ms if ms.tzinfo else ms.replace(tzinfo=timezone.utc)
    try:
        v = float(ms)
    except (TypeError, ValueError):
        return None
    # A value small enough to be SECONDS is a bug upstream or a hand-edited fixture, not a date
    # in 1970. Refuse it rather than silently reading it 1000× wrong: 1.7e9 ms is Jan 1970,
    # which would make every date rule fire on a 55-year-old timestamp.
    if abs(v) < 1e11:
        return None
    try:
        return datetime.fromtimestamp(v / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def to_millis(dt: datetime | None) -> int | None:
    """Aware datetime → epoch milliseconds, for writing a fixture in the wire format."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# ── the parsed shapes ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Event:
    """One tracking event, with only the two safely-typed fields promoted."""

    event_type: TrackingEventType | None
    at: datetime | None
    #: The raw `eventType` string, kept when `from_string` could not resolve it — an unknown
    #: event type is a data-quality signal, and discarding it makes it unobservable.
    raw_type: str = ""
    #: `Object` upstream. Present so the trace can carry it; never read for a decision.
    data: dict = field(default_factory=dict)
    user: dict | None = None

    @property
    def known(self) -> bool:
        return self.event_type is not None


@dataclass(frozen=True)
class Tracking:
    """A parsed waybill tracking response."""

    waybill_no: str
    events: tuple[Event, ...]
    consignment: dict
    drses: tuple[dict, ...]
    #: Set when the envelope reported a failure — `status.code != 200` on an HTTP 200.
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def status(self) -> str:
        return str(self.consignment.get("consignmentStatus") or "")

    @property
    def flow_type(self) -> str:
        return str(self.consignment.get("flowType") or "")

    @property
    def attempts(self) -> int:
        """`attempts` has a field initialiser of 0L upstream, so it is always present."""
        try:
            return int(self.consignment.get("attempts") or 0)
        except (TypeError, ValueError):
            return 0

    def of_types(self, types) -> tuple[Event, ...]:
        want = frozenset(types)
        return tuple(e for e in self.events if e.event_type in want)

    def first_of(self, types) -> Event | None:
        got = self.of_types(types)
        return got[0] if got else None

    def last_of(self, types) -> Event | None:
        got = self.of_types(types)
        return got[-1] if got else None


def parse_service_response(payload: dict | None, waybill_no: str = "") -> Tracking:
    """`ServiceResponse<WaybillTrackingResponse>` → Tracking. Never raises.

    Accepts the envelope OR a bare `WaybillTrackingResponse`, because a fixture is easier to
    read without the wrapper and the wrapper carries no information a caller here needs beyond
    `status.code`.
    """
    if not isinstance(payload, dict):
        return Tracking(waybill_no=waybill_no, events=(), consignment={}, drses=(),
                        error="no payload")

    # The envelope's own error channel. HTTP was 200; the failure is in here.
    if "status" in payload and isinstance(payload.get("status"), dict):
        st = payload["status"]
        code = st.get("code")
        body = payload.get("response")
        if code is not None and int(code) != 200:
            return Tracking(waybill_no=waybill_no, events=(), consignment={}, drses=(),
                            error=f"status.code={code}: {st.get('message') or 'upstream error'}")
        payload = body if isinstance(body, dict) else {}

    raw_events = payload.get("tracking")
    events = _parse_events(raw_events if isinstance(raw_events, list) else [])
    consignment = payload.get("consignment") if isinstance(payload.get("consignment"), dict) else {}
    drses = tuple(d for d in (payload.get("drses") or []) if isinstance(d, dict))
    return Tracking(
        waybill_no=str(consignment.get("waybillNo") or waybill_no or ""),
        events=events, consignment=consignment, drses=drses)


def _parse_events(raw: list) -> tuple[Event, ...]:
    """Parse and SORT ascending by time.

    The real service sorts before responding, so re-sorting is redundant against a live call —
    and exactly right against a fixture someone edited by hand on stage. Undated events keep
    their relative position at the end rather than being dropped: their existence is a fact even
    when their timing is not.
    """
    parsed = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            continue
        raw_type = str(r.get("eventType") or "")
        parsed.append((i, Event(
            event_type=from_string(raw_type),
            at=to_datetime(r.get("eventTime")),
            raw_type=raw_type,
            data=r.get("data") if isinstance(r.get("data"), dict) else {},
            user=r.get("user") if isinstance(r.get("user"), dict) else None,
        )))
    # (has_time, time, original_index) — undated events sort last, stably.
    parsed.sort(key=lambda t: (t[1].at is not None, t[1].at or datetime.min.replace(
        tzinfo=timezone.utc), t[0]) if t[1].at else (False, datetime.min.replace(
        tzinfo=timezone.utc), t[0]))
    return tuple(e for _i, e in parsed)


def contract_problems(payload: dict, *, strict_masking: bool = True) -> list[str]:
    """Contract violations in a raw response. Empty list = valid. For fixtures and the harness."""
    problems: list[str] = []
    body = payload.get("response") if isinstance(payload.get("response"), dict) else payload

    for key in ("tracking", "consignment", "drses"):
        if key not in body:
            # WaybillTrackingResponse carries NO @JsonInclude, so all three keys are always
            # emitted — possibly null, but always present.
            problems.append(f"missing '{key}' (the response type always emits it)")

    if not isinstance(body.get("tracking"), list):
        problems.append("tracking must be a list (empty -> [])")
    else:
        for i, ev in enumerate(body["tracking"]):
            if not isinstance(ev, dict):
                problems.append(f"tracking[{i}] is not an object"); continue
            if from_string(ev.get("eventType")) is None:
                problems.append(f"tracking[{i}].eventType={ev.get('eventType')!r} "
                                f"is not a TrackingEventType")
            t = ev.get("eventTime")
            if not isinstance(t, (int, float)) or isinstance(t, bool):
                problems.append(f"tracking[{i}].eventTime must be epoch millis, got {t!r}")
            elif abs(float(t)) < 1e11:
                problems.append(f"tracking[{i}].eventTime={t} looks like SECONDS, not millis")
            if "data" in ev and not isinstance(ev["data"], dict):
                problems.append(f"tracking[{i}].data must be an object ({{}} at minimum)")

    c = body.get("consignment")
    if not isinstance(c, dict):
        problems.append("consignment must be an object")
    else:
        if "attempts" not in c:
            problems.append("consignment.attempts is always present (field initialiser = 0L)")
        if not isinstance(c.get("consignmentPODBO"), list):
            problems.append("consignment.consignmentPODBO is a LIST of PODImageDTO "
                            "(never absent — mapPodImages returns [])")
        for pod in (c.get("consignmentPODBO") or []):
            if isinstance(pod, dict):
                unknown = set(pod) - {"podImpLink", "sigImgLink", "pdfLink"}
                if unknown:
                    problems.append(f"PODImageDTO has unexpected keys {sorted(unknown)} "
                                    f"(note: podImpLink is 'Imp', not 'Img')")
        if strict_masking:
            for who in ("shipper", "consignee"):
                contact = c.get(who)
                if isinstance(contact, dict):
                    for k in ("primaryNumber", "secondaryNumber"):
                        if contact.get(k) not in (None, MASKED_NUMBER):
                            problems.append(
                                f"consignment.{who}.{k}={contact.get(k)!r} — the real mapper "
                                f"hardcodes {MASKED_NUMBER!r}, so any other value is less "
                                f"faithful, not more")
    return problems


def from_legacy_scans(blob: dict | None, awb: str = "") -> Tracking | None:
    """Lift the OLD hand-written seed scan shape into a Tracking.

    Why bother: it means there is exactly ONE typed code path in `policy_exec`, and the three
    seed captains flow through the same predicates as a real fixture. That is also the parity
    proof — if the typed path reaches a different decision than the boolean path did on the same
    seed data, it is a regression, not a difference of representation.

    The legacy blob's `connected_within_tat` / `hardstop_sop_followed` booleans are DELIBERATELY
    ignored. They were precomputed answers written into a file; the point of this module is to
    derive them from the events instead.
    """
    if not isinstance(blob, dict):
        return None
    events = []
    for e in blob.get("events") or []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("scan") or "").strip()
        at = None
        raw_at = str(e.get("at") or "").strip()
        if raw_at:
            try:
                at = datetime.fromisoformat(raw_at)
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
            except ValueError:
                at = None
        events.append(Event(event_type=from_string(name), at=at, raw_type=name,
                            data={}, user={"name": e.get("node") or ""}))
    # The legacy shape has no in-scan EVENT when it only carries `inscan_date`, so synthesise one
    # from that field — it is the same fact in a different place, not new information.
    if blob.get("inscan_date") and not any(
            e.event_type in FACILITY_INSCAN_FOR_LEGACY for e in events):
        try:
            at = datetime.fromisoformat(str(blob["inscan_date"])).replace(tzinfo=timezone.utc)
            events.insert(0, Event(event_type=from_string("INWARD_SCAN"), at=at,
                                   raw_type="INWARD_SCAN", data={},
                                   user={"name": blob.get("hub") or ""}))
        except ValueError:
            pass
    events.sort(key=lambda e: (e.at is not None, e.at or datetime.min.replace(tzinfo=timezone.utc)))
    return Tracking(
        waybill_no=str(blob.get("awb") or awb or ""),
        events=tuple(events),
        consignment={"waybillNo": blob.get("awb") or awb,
                     "consignmentStatus": blob.get("last_status") or "",
                     "flowType": "REVERSE" if str(blob.get("direction", "")).lower().startswith("rev")
                                 else "FORWARD",
                     "attempts": sum(1 for e in events
                                     if e.raw_type in ("DRS_SCAN", "REATTEMPT", "OUT_FOR_DELIVERY")),
                     "location": {"name": blob.get("hub") or ""},
                     "consignmentPODBO": []},
        drses=())
