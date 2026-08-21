"""TrackingEventType — all 121 values, verbatim, in declaration order.

SOURCE
`log10-backend-valmo-develop :: ConsignmentModule/src/main/java/com/loadshare/consignment/
entity/enums/TrackingEventType.java`, cross-checked byte-for-byte against
`TrackingService/schemas/openapi.yml`. Read via `unzip -p`; no code copied, only the values.

It is a plain Java enum — no constructor args, no display strings, no `@JsonValue` — so the
wire value IS the identifier, and the DB column is `@Enumerated(EnumType.STRING)`. That is why
this can be a flat `str` enum with no mapping layer: there is nothing to map.

WHY ALL 121 AND NOT THE DOZEN WE USE
Two reasons, both learned the hard way elsewhere in this codebase. First, `from_string` has to
be able to tell "an event type we do not handle" from "an event type that does not exist" — a
subset enum collapses those into the same `None`, and the second one is a data-quality signal
worth seeing. Second, the AWB lexer shipped for months matching only one of the two real AWB
shapes, silently losing 25.6% of the ledger; a truncated vocabulary is the same bug waiting to
happen. Completeness is cheap here and the failure it prevents is not.

The declaration ORDER is preserved because the upstream enum's ordinal is persisted in some
tables. Nothing here depends on it today, but reordering would make this file a worse record of
the source than it is now.
"""
from __future__ import annotations

from enum import Enum


class TrackingEventType(str, Enum):
    """A `str` subclass so an event type serialises into a trace event as its own name."""

    SYNC_REQUEST = "SYNC_REQUEST"
    SYNC_RESPONSE = "SYNC_RESPONSE"
    BOOKING_RECEIVED = "BOOKING_RECEIVED"
    BOOKING = "BOOKING"
    CREATE_EXCEPTION = "CREATE_EXCEPTION"
    EXCEPTION = "EXCEPTION"
    DELETED = "DELETED"
    UPDATED = "UPDATED"
    UPDATED_STATUS = "UPDATED_STATUS"
    UPDATED_SHIPMENT = "UPDATED_SHIPMENT"
    INSCAN = "INSCAN"
    INWARD_SCAN = "INWARD_SCAN"
    OUTSCAN = "OUTSCAN"
    DRS_SCAN = "DRS_SCAN"
    ARRIVED_AT_LOCATION = "ARRIVED_AT_LOCATION"
    DRS_SCAN_DELINK = "DRS_SCAN_DELINK"
    MANIFEST_SCAN = "MANIFEST_SCAN"
    MANIFEST_SCAN_DELINK = "MANIFEST_SCAN_DELINK"
    THC_SCAN_DELINK = "THC_SCAN_DELINK"
    THC_SCAN = "THC_SCAN"
    CUSTOMER_ALTERNATE_INSTRUCTIONS = "CUSTOMER_ALTERNATE_INSTRUCTIONS"
    DELIVERED = "DELIVERED"
    UNDELIVERED = "UNDELIVERED"
    BAG_SCAN = "BAG_SCAN"
    BAG_SCAN_DELINK = "BAG_SCAN_DELINK"
    MANIFESTB2C_SCAN = "MANIFESTB2C_SCAN"
    MANIFESTB2C_SCAN_DELINK = "MANIFESTB2C_SCAN_DELINK"
    CALL = "CALL"
    RTO_DRS_SCAN = "RTO_DRS_SCAN"
    RTO_DRS_SCAN_DELINK = "RTO_DRS_SCAN_DELINK"
    RTO_BAG_SCAN = "RTO_BAG_SCAN"
    RTO_BAG_SCAN_DELINK = "RTO_BAG_SCAN_DELINK"
    RTO_MANIFEST_SCAN = "RTO_MANIFEST_SCAN"
    RTO_MANIFEST_SCAN_DELINK = "RTO_MANIFEST_SCAN_DELINK"
    RTO_DELIVERED = "RTO_DELIVERED"
    RTO_UNDELIVERED = "RTO_UNDELIVERED"
    RTS = "RTS"
    RTO_IN = "RTO_IN"
    RTO = "RTO"
    THIRD_PARTY_SYNC_EVENT = "THIRD_PARTY_SYNC_EVENT"
    LEAD_STATUS_UPDATE = "LEAD_STATUS_UPDATE"
    THC_STATUS_UPDATE = "THC_STATUS_UPDATE"
    RTS_DELIVERED = "RTS_DELIVERED"
    RTS_DRS_SCAN = "RTS_DRS_SCAN"
    RTS_DRS_SCAN_DELINK = "RTS_DRS_SCAN_DELINK"
    POD_CORRECTION_RTO_TO_UNDEL = "POD_CORRECTION_RTO_TO_UNDEL"
    REQUEST_FOR_APPROVAL = "REQUEST_FOR_APPROVAL"
    PARTIAL_APPROVE = "PARTIAL_APPROVE"
    PICKED_UP = "PICKED_UP"
    PICKUP_REJECTED = "PICKUP_REJECTED"
    APPROVED = "APPROVED"
    REJECT = "REJECT"
    CALL_RECORD = "CALL_RECORD"
    CANCELLED = "CANCELLED"
    PICKUP_CANCELLED = "PICKUP_CANCELLED"
    PICKUP_ASSIGNED = "PICKUP_ASSIGNED"
    UPDATED_ETA = "UPDATED_ETA"
    POD_CORRECTION = "POD_CORRECTION"
    POD_IMAGE_UPDATED = "POD_IMAGE_UPDATED"
    POD_CLEAN = "POD_CLEAN"
    POD_UNCLEAN = "POD_UNCLEAN"
    UPDATE_RTO_RTS = "UPDATE_RTO_RTS"
    POD_STREAM = "POD_STREAM"
    RTS_UNDELIVERED = "RTS_UNDELIVERED"
    THC_POD = "THC_POD"
    MANIFEST_POD = "MANIFEST_POD"
    DUPLICATE_BOOKING = "DUPLICATE_BOOKING"
    RTS_INSCAN = "RTS_INSCAN"
    RTS_INWARD_SCAN = "RTS_INWARD_SCAN"
    RTO_INWARD_SCAN = "RTO_INWARD_SCAN"
    OVERAGE_ADDED = "OVERAGE_ADDED"
    CSAT_UPLOAD_EVENT = "CSAT_UPLOAD_EVENT"
    OVERAGE_IGNORED = "OVERAGE_IGNORED"
    OUT_FOR_PICKUP = "OUT_FOR_PICKUP"
    PICKUP_CREATED = "PICKUP_CREATED"
    STATUS_CORRECTION = "STATUS_CORRECTION"
    RTODEL = "RTODEL"
    RTS_NOT_PICKEDUP = "RTS_NOT_PICKEDUP"
    BOOKING_CANCELLED = "BOOKING_CANCELLED"
    PICKUP_PENDING = "PICKUP_PENDING"
    POD_SCAN = "POD_SCAN"
    RTO_HANDOVER = "RTO_HANDOVER"
    HANDED_OVER = "HANDED_OVER"
    HANDOVER_TO_COLOADER = "HANDOVER_TO_COLOADER"
    ROUTE_CONTROLLER_GET_ALL_VIEW = "ROUTE_CONTROLLER_GET_ALL_VIEW"
    CONSIGNMENT_MARKED_LOST = "CONSIGNMENT_MARKED_LOST"
    CONSIGNMENT_MARKED_FOUND = "CONSIGNMENT_MARKED_FOUND"
    UNDELIVERED_B2B = "UNDELIVERED_B2B"
    RETURN_B2B = "RETURN_B2B"
    IN_SCAN_SUPPORT = "IN_SCAN_SUPPORT"
    B2B_TO_B2C_INSCAN = "B2B_TO_B2C_INSCAN"
    B2B_TO_B2C_INWARDSCAN = "B2B_TO_B2C_INWARDSCAN"
    B2B_TO_B2C_TRIP_START = "B2B_TO_B2C_TRIP_START"
    B2B_TO_B2C_MARK_DEL = "B2B_TO_B2C_MARK_DEL"
    HANDOVER = "HANDOVER"
    MANIFEST_RECEIVED = "MANIFEST_RECEIVED"
    OVERAGE_SCAN = "OVERAGE_SCAN"
    MISROUTE = "MISROUTE"
    VEHICLE_ARRIVED = "VEHICLE_ARRIVED"
    SHORTAGE_SCAN = "SHORTAGE_SCAN"
    SHIPMENT_INFORM_PREV_LOC = "SHIPMENT_INFORM_PREV_LOC"
    WRONG_FACILITY_SCAN = "WRONG_FACILITY_SCAN"
    OFD_SCAN = "OFD_SCAN"
    LABEL_PRINT = "LABEL_PRINT"
    QC_FAILED = "QC_FAILED"
    SEC_QC_FAILED = "SEC_QC_FAILED"
    QC_PASS = "QC_PASS"
    SEC_QC_PASS = "SEC_QC_PASS"
    REATTEMPT = "REATTEMPT"
    DEL_OTP_VIEWED = "DEL_OTP_VIEWED"
    MIGRATED_SCAN = "MIGRATED_SCAN"
    QC_CORRECTION = "QC_CORRECTION"
    MISROUTE_SCAN = "MISROUTE_SCAN"
    AUDIT_SCAN = "AUDIT_SCAN"
    CONSIGNEE_DETAILS_UPDATE = "CONSIGNEE_DETAILS_UPDATE"
    PICKUP_HOLD = "PICKUP_HOLD"
    PARTIAL_DELIVERED = "PARTIAL_DELIVERED"
    MANIFEST_SHORT = "MANIFEST_SHORT"
    MANIFEST_MISROUTE = "MANIFEST_MISROUTE"
    RTO_RETURN_TO_ORIGIN = "RTO_RETURN_TO_ORIGIN"
    TAMPERED = "TAMPERED"

# ── the count is asserted at import, not documented ──────────────────────────────────────────
# A dropped line during an edit is invisible in a 121-member enum. This makes it a startup
# failure instead of a silently narrower vocabulary.
EXPECTED_COUNT = 121
assert len(TrackingEventType) == EXPECTED_COUNT, (
    f"TrackingEventType has {len(TrackingEventType)} members, expected {EXPECTED_COUNT} — "
    f"a value was lost or added relative to the log10 source")

# Declaration order, for the harness to diff against the source listing.
DECLARATION_ORDER: tuple[str, ...] = tuple(e.name for e in TrackingEventType)


def from_string(raw: str | None) -> "TrackingEventType | None":
    """Case-insensitive name lookup. Unknown or empty → None, mirroring the Java helper.

    Returning None rather than raising is deliberate and matches upstream: an unrecognised
    event type is a data-quality observation, not a reason to fail a captain's turn.
    """
    if not raw:
        return None
    key = str(raw).strip().upper()
    try:
        return TrackingEventType[key]
    except KeyError:
        return None


def is_rto_event(e: "TrackingEventType | None") -> bool:
    """Mirrors `TrackingEventType.isRTOEvent` — note UPDATED_STATUS is included upstream even
    though its name carries no RTO prefix."""
    if e is None:
        return False
    return e.name.startswith("RTO_") or e is TrackingEventType.UPDATED_STATUS


_LAT_LONG = frozenset({
    "DELIVERED", "PICKED_UP", "PICKUP_CANCELLED", "OFD_SCAN", "UNDELIVERED",
    "RTO_DELIVERED", "RTO_UNDELIVERED",
})


def is_lat_long_event(e: "TrackingEventType | None") -> bool:
    """Mirrors `TrackingEventType.isLatLongEvent` — the events that carry coordinates."""
    return e is not None and e.name in _LAT_LONG


# ── status → event, from the enum's own static maps ──────────────────────────────────────────
# Mirrored because they record which event type a given consignment status is EXPECTED to
# produce, which is the only authority on that mapping.
FORWARD_MAP = {
    "IN": TrackingEventType.INSCAN,
    "INWARD": TrackingEventType.INWARD_SCAN,
    "OFD": TrackingEventType.DRS_SCAN,
    "DELIVERED": TrackingEventType.DELIVERED,
    "UNDELIVERED": TrackingEventType.UNDELIVERED,
    "PARTIALLY_DELIVERED": TrackingEventType.DELIVERED,
    "OFP": TrackingEventType.OUT_FOR_PICKUP,
    "PCREATED": TrackingEventType.PICKUP_CREATED,
    "PPEND": TrackingEventType.PICKUP_PENDING,
    "PCANC": TrackingEventType.PICKUP_CANCELLED,
    "PSUCC": TrackingEventType.PICKED_UP,
    "BOOK": TrackingEventType.BOOKING,
    "BOOKING_CANCELLED": TrackingEventType.BOOKING_CANCELLED,
    "IN_TRANSIT": TrackingEventType.THC_SCAN,
    "MISROUTE": TrackingEventType.MISROUTE_SCAN,
}

RTO_MAP = {
    "RTO_RETURN_TO_ORIGIN": TrackingEventType.UPDATED_STATUS,
    "OFD": TrackingEventType.RTO_DRS_SCAN,
    "RTO_IN": TrackingEventType.RTO_IN,
    "RTO_INWARD": TrackingEventType.RTO_INWARD_SCAN,
    "RTO_OUT": TrackingEventType.RTO_DRS_SCAN,
    "RTO_DELIVERED": TrackingEventType.RTO_DELIVERED,
    "RTO_UNDELIVERED": TrackingEventType.RTO_UNDELIVERED,
    "RTO_MISROUTE": TrackingEventType.MISROUTE_SCAN,
    "RTO_INTRANSIT": TrackingEventType.THC_SCAN,
}


def resolve_tracking_type(status: str | None,
                          flow_type: str | None) -> "TrackingEventType | None":
    """Mirrors `resolveTrackingType(String status, String flowType)`."""
    key = (status or "").strip().upper()
    flow = (flow_type or "").strip().upper()
    return (RTO_MAP if flow in ("RTO", "REVERSE") else FORWARD_MAP).get(key)


# ── the event groups the Losses & Debits SOPs actually reason about ──────────────────────────
# Named sets rather than inline literals at each call site, because a rule that turns on "did a
# forward connection happen" must mean the same thing in the predicate, the evidence trail and
# the harness. These are OUR groupings of the upstream vocabulary, not upstream's.

#: A shipment moving onward from a facility — the "forward connection" the 5/7-day rule tests.
FORWARD_CONNECTION = frozenset({
    TrackingEventType.MANIFEST_SCAN, TrackingEventType.THC_SCAN, TrackingEventType.OUTSCAN,
    TrackingEventType.HANDOVER, TrackingEventType.HANDED_OVER,
    TrackingEventType.HANDOVER_TO_COLOADER, TrackingEventType.MANIFEST_RECEIVED,
})

#: Arrival at a facility — the clock-start for the connection window.
FACILITY_INSCAN = frozenset({
    TrackingEventType.INSCAN, TrackingEventType.INWARD_SCAN,
    TrackingEventType.ARRIVED_AT_LOCATION, TrackingEventType.IN_SCAN_SUPPORT,
    TrackingEventType.B2B_TO_B2C_INSCAN, TrackingEventType.B2B_TO_B2C_INWARDSCAN,
    TrackingEventType.RTO_INWARD_SCAN, TrackingEventType.RTS_INSCAN,
    TrackingEventType.RTS_INWARD_SCAN,
})

#: A delivery attempt. DRS_SCAN is the out-for-delivery scan; REATTEMPT is an explicit retry.
DELIVERY_ATTEMPT = frozenset({
    TrackingEventType.DRS_SCAN, TrackingEventType.OFD_SCAN, TrackingEventType.REATTEMPT,
    TrackingEventType.RTO_DRS_SCAN, TrackingEventType.RTS_DRS_SCAN,
})

#: Misroute, in both spellings the vocabulary carries.
MISROUTE = frozenset({
    TrackingEventType.MISROUTE, TrackingEventType.MISROUTE_SCAN,
    TrackingEventType.MANIFEST_MISROUTE,
})

#: Shortage — the shipment was expected and not there.
SHORTAGE = frozenset({
    TrackingEventType.SHORTAGE_SCAN, TrackingEventType.MANIFEST_SHORT,
})

#: Terminal loss / found states.
MARKED_LOST = frozenset({TrackingEventType.CONSIGNMENT_MARKED_LOST})
MARKED_FOUND = frozenset({TrackingEventType.CONSIGNMENT_MARKED_FOUND})

#: QC failure, primary and secondary.
QC_FAILED = frozenset({TrackingEventType.QC_FAILED, TrackingEventType.SEC_QC_FAILED})
QC_PASSED = frozenset({TrackingEventType.QC_PASS, TrackingEventType.SEC_QC_PASS})

#: Tamper and the audit scan that usually accompanies an investigation.
TAMPERED = frozenset({TrackingEventType.TAMPERED})
AUDIT = frozenset({TrackingEventType.AUDIT_SCAN})

#: Vehicle arrival — the clock-start for the Shortage 24h ticket window.
VEHICLE_ARRIVED = frozenset({TrackingEventType.VEHICLE_ARRIVED})

#: Delivered, in every flow.
DELIVERED = frozenset({
    TrackingEventType.DELIVERED, TrackingEventType.RTO_DELIVERED,
    TrackingEventType.RTS_DELIVERED, TrackingEventType.PARTIAL_DELIVERED,
    TrackingEventType.RTODEL, TrackingEventType.B2B_TO_B2C_MARK_DEL,
})

#: A scan that was UNDONE. Upstream models a delink as its own event rather than deleting the
#: original, so a timeline can contain both — and counting the original without noticing the
#: delink would credit a connection that was reversed.
DELINK = frozenset(e for e in TrackingEventType if e.name.endswith("_DELINK"))
