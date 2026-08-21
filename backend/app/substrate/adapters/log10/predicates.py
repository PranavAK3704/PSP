"""Scan-timeline predicates. Three-valued, because "no scan data" is not "no".

THE BUG THIS REPLACES
`policy_exec._exec_hardstop` read PRECOMPUTED BOOLEANS out of the seed fixture:

    connected   = bool(scans and scans.get("connected_within_tat"))
    sop_followed = bool(scans and scans.get("hardstop_sop_followed", True))

Two things are wrong there. First, the engine never looked at an event type at all — the reply
string *named* `INWARD_SCAN` and `MANIFEST_SCAN` as prose while the code trusted a boolean
someone had written into a JSON file. Second, and worse: `bool(scans and ...)` collapses
**absent** into **False**. On a money decision those are opposite conclusions —

    NO      = the scans exist and show no forward connection  → evidence AGAINST reversal
    UNKNOWN = we cannot see the scans                         → a human must look

— and the old expression produced NO for both. A captain whose scan data simply had not synced
was told, in effect, that their shipment never moved.

So every predicate here returns `(Tri, evidence_row)`. UNKNOWN never satisfies a check and never
counts as a refutation; it escalates. `Tri` deliberately has no `__bool__`, so `if verdict:`
will not compile into the same collapse.

WHAT THE RULES ARE
From the real SOP tracker (Losses & Debits):
    S1.1 / S1.2   forward connection within 5 days (reverse) / 7 days (forward)
    S5 / S6       3 delivery attempts within 7 days
    S11.1 / S11.2 a MISROUTE scan within 2 days
    Shortage S2   vehicle arrival, then a ticket within 24h

DEFENSIVE READS
Every function tolerates an empty timeline, undated events, duplicates, and out-of-order input.
Out-of-order should be impossible against the live service (it sorts) but is entirely possible
against a fixture edited by hand five minutes before a demo — `dto._parse_events` re-sorts, and
these never assume it happened.

A DELINK is a scan that was UNDONE. Upstream models it as its own event rather than deleting the
original, so a timeline can hold both — and counting the original without noticing the delink
credits a connection that was reversed. `_effective` removes delinked pairs before any rule runs.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ....tri import Tri
from . import event_types as ET
from .dto import Event, Tracking

# The SOP windows, named so a rule cannot be read as an arbitrary constant.
FORWARD_CONNECTION_DAYS_FORWARD = 7      # S1.2
FORWARD_CONNECTION_DAYS_REVERSE = 5      # S1.1
ATTEMPT_COUNT = 3                        # S5 / S6
ATTEMPT_WINDOW_DAYS = 7                  # S5 / S6
MISROUTE_WINDOW_DAYS = 2                 # S11.1 / S11.2
SHORTAGE_TICKET_WINDOW_HOURS = 24        # Shortage S2


def _ev(label: str, value: str, verdict: Tri) -> dict:
    """An evidence row that carries the verdict, so a reviewer sees UNKNOWN as UNKNOWN rather
    than inferring it from a missing line."""
    return {"label": label, "value": value, "verdict": verdict.value, "source": "log10_scans"}


def _effective(t: Tracking) -> tuple[Event, ...]:
    """Events with delinked scans removed.

    A `*_DELINK` event undoes the most recent matching scan. Pairing is by base type and order
    rather than by id, because the tracking DTO carries no scan id — so this cancels the LAST
    un-cancelled scan of that type, which is what a delink means operationally.
    """
    out: list[Event] = []
    for e in t.events:
        if e.event_type in ET.DELINK:
            base = e.event_type.name[: -len("_DELINK")]
            for i in range(len(out) - 1, -1, -1):
                if out[i].event_type is not None and out[i].event_type.name == base:
                    out.pop(i)
                    break
            continue
        out.append(e)
    return tuple(out)


def _dated(events, types) -> list[Event]:
    want = frozenset(types)
    return sorted((e for e in events if e.event_type in want and e.at is not None),
                  key=lambda e: e.at)


def _has_undated(events, types) -> bool:
    want = frozenset(types)
    return any(e.event_type in want and e.at is None for e in events)


def _no_data(t: Tracking | None) -> tuple[Tri, dict] | None:
    """The shared UNKNOWN guard. Returns a verdict when there is nothing to reason over."""
    if t is None:
        return Tri.UNKNOWN, _ev("Scan timeline", "no tracking data available", Tri.UNKNOWN)
    if not t.ok:
        return Tri.UNKNOWN, _ev("Scan timeline", f"tracking unavailable — {t.error}", Tri.UNKNOWN)
    if not t.events:
        return Tri.UNKNOWN, _ev("Scan timeline", "tracking returned no events", Tri.UNKNOWN)
    return None


# ── S1.1 / S1.2 — forward connection within the window ───────────────────────────────────────

def forward_connection_within(t: Tracking | None, days: int | None = None) -> tuple[Tri, dict]:
    """Did the shipment move onward from its facility in-scan within the window?

    YES     an in-scan, then a forward-connection event within `days`
    NO      an in-scan, and no forward connection within `days` — the shipment sat
    UNKNOWN no timeline, no in-scan to start the clock, or the events carry no timestamps

    `days` defaults from the flow type: 5 for reverse (S1.1), 7 for forward (S1.2).
    """
    guard = _no_data(t)
    if guard:
        return guard
    events = _effective(t)
    if days is None:
        reverse = (t.flow_type or "").strip().upper() in ("REVERSE", "RTO", "RVP")
        days = FORWARD_CONNECTION_DAYS_REVERSE if reverse else FORWARD_CONNECTION_DAYS_FORWARD

    inscans = _dated(events, ET.FACILITY_INSCAN)
    if not inscans:
        # No clock-start. If in-scans exist but are undated, say so specifically — that is a
        # data problem, not an operational one, and the two need different follow-ups.
        why = ("facility in-scans present but undated" if _has_undated(events, ET.FACILITY_INSCAN)
               else "no facility in-scan on the timeline")
        return Tri.UNKNOWN, _ev(f"Forward connection ≤{days}d", why, Tri.UNKNOWN)

    start = inscans[0].at
    deadline = start + timedelta(days=days)
    onward = [e for e in _dated(events, ET.FORWARD_CONNECTION) if e.at >= start]
    within = [e for e in onward if e.at <= deadline]
    if within:
        e = within[0]
        return Tri.YES, _ev(
            f"Forward connection ≤{days}d",
            f"{e.event_type.name} on {e.at:%Y-%m-%d %H:%M}Z, "
            f"{(e.at - start).days}d after in-scan {start:%Y-%m-%d}",
            Tri.YES)
    if onward:
        e = onward[0]
        return Tri.NO, _ev(
            f"Forward connection ≤{days}d",
            f"first onward move was {e.event_type.name} on {e.at:%Y-%m-%d}, "
            f"{(e.at - start).days}d after in-scan — outside the {days}d window",
            Tri.NO)
    if _has_undated(events, ET.FORWARD_CONNECTION):
        return Tri.UNKNOWN, _ev(f"Forward connection ≤{days}d",
                                "onward-movement events present but undated", Tri.UNKNOWN)
    return Tri.NO, _ev(f"Forward connection ≤{days}d",
                       f"no onward movement after in-scan {start:%Y-%m-%d}", Tri.NO)


# ── S5 / S6 — attempts within the window ─────────────────────────────────────────────────────

def attempts_within(t: Tracking | None, count: int = ATTEMPT_COUNT,
                    days: int = ATTEMPT_WINDOW_DAYS) -> tuple[Tri, dict]:
    """Were there at least `count` delivery attempts inside any `days`-long window?

    A sliding window, not a count since the first attempt: the SOP asks whether three attempts
    were made within seven days, and three attempts spread over three weeks does not satisfy it.
    """
    guard = _no_data(t)
    if guard:
        return guard
    events = _effective(t)
    tries = _dated(events, ET.DELIVERY_ATTEMPT)
    label = f"{count} attempts ≤{days}d"
    if not tries:
        if _has_undated(events, ET.DELIVERY_ATTEMPT):
            return Tri.UNKNOWN, _ev(label, "attempt events present but undated", Tri.UNKNOWN)
        # `attempts` on the consignment is a COUNTER, not a timeline. If it disagrees with the
        # scans we cannot place the attempts in time, so the window is unanswerable.
        if t.attempts >= count:
            return Tri.UNKNOWN, _ev(
                label, f"consignment.attempts={t.attempts} but no attempt scans on the "
                       f"timeline — cannot place them within a window", Tri.UNKNOWN)
        return Tri.NO, _ev(label, "no delivery-attempt scans on the timeline", Tri.NO)

    window = timedelta(days=days)
    best = 1
    for i, anchor in enumerate(tries):
        n = sum(1 for e in tries[i:] if e.at - anchor.at <= window)
        best = max(best, n)
        if n >= count:
            last = [e for e in tries[i:] if e.at - anchor.at <= window][count - 1]
            return Tri.YES, _ev(
                label, f"{n} attempts between {anchor.at:%Y-%m-%d} and {last.at:%Y-%m-%d}",
                Tri.YES)
    return Tri.NO, _ev(label,
                       f"{len(tries)} attempt(s) total, at most {best} inside any {days}d window "
                       f"(first {tries[0].at:%Y-%m-%d}, last {tries[-1].at:%Y-%m-%d})", Tri.NO)


# ── S11.1 / S11.2 — misroute within the window ───────────────────────────────────────────────

def misroute_within(t: Tracking | None, days: int = MISROUTE_WINDOW_DAYS) -> tuple[Tri, dict]:
    """Was a misroute scanned within `days` of the facility in-scan?

    NO here means two different things worth distinguishing in the evidence: no misroute at all,
    or a misroute that came too late. The verdict is the same; the sentence is not.
    """
    guard = _no_data(t)
    if guard:
        return guard
    events = _effective(t)
    label = f"Misroute scan ≤{days}d"
    mis = _dated(events, ET.MISROUTE)
    if not mis:
        if _has_undated(events, ET.MISROUTE):
            return Tri.UNKNOWN, _ev(label, "misroute events present but undated", Tri.UNKNOWN)
        return Tri.NO, _ev(label, "no misroute scan on the timeline", Tri.NO)
    inscans = _dated(events, ET.FACILITY_INSCAN)
    if not inscans:
        return Tri.UNKNOWN, _ev(label, f"{len(mis)} misroute scan(s) but no in-scan to measure "
                                       f"from", Tri.UNKNOWN)
    start = inscans[0].at
    first = mis[0]
    delta = first.at - start
    if delta <= timedelta(days=days):
        return Tri.YES, _ev(label, f"{first.event_type.name} on {first.at:%Y-%m-%d}, "
                                   f"{delta.days}d after in-scan", Tri.YES)
    return Tri.NO, _ev(label, f"{first.event_type.name} on {first.at:%Y-%m-%d} — {delta.days}d "
                              f"after in-scan, outside the {days}d window", Tri.NO)


# ── Shortage S2 — vehicle arrival to ticket ──────────────────────────────────────────────────

def vehicle_arrival_to_ticket(t: Tracking | None, ticket_at: datetime | None,
                              hours: int = SHORTAGE_TICKET_WINDOW_HOURS) -> tuple[Tri, dict]:
    """Was the ticket raised within `hours` of the vehicle arriving?

    UNKNOWN when either end of the interval is missing — a rule about an interval cannot be
    decided from one endpoint, and guessing the other is how a captain loses a claim.
    """
    label = f"Ticket ≤{hours}h of vehicle arrival"
    guard = _no_data(t)
    if guard:
        return guard
    if ticket_at is None:
        return Tri.UNKNOWN, _ev(label, "ticket timestamp not supplied", Tri.UNKNOWN)
    arrivals = _dated(_effective(t), ET.VEHICLE_ARRIVED)
    if not arrivals:
        return Tri.UNKNOWN, _ev(label, "no VEHICLE_ARRIVED scan on the timeline", Tri.UNKNOWN)
    arrival = arrivals[-1].at            # the arrival the shortage would have been found at
    if ticket_at.tzinfo is None:
        from datetime import timezone as _tz
        ticket_at = ticket_at.replace(tzinfo=_tz.utc)
    delta = ticket_at - arrival
    if delta < timedelta(0):
        return Tri.UNKNOWN, _ev(label, f"ticket {ticket_at:%Y-%m-%d %H:%M}Z predates the "
                                       f"arrival {arrival:%Y-%m-%d %H:%M}Z", Tri.UNKNOWN)
    h = delta.total_seconds() / 3600.0
    verdict = Tri.of(h <= hours)
    return verdict, _ev(label, f"{h:.1f}h after VEHICLE_ARRIVED on {arrival:%Y-%m-%d %H:%M}Z",
                        verdict)


# ── terminal states ──────────────────────────────────────────────────────────────────────────

def marked_lost(t: Tracking | None) -> tuple[Tri, dict]:
    """Is the shipment marked lost — and NOT subsequently found?

    Order matters: CONSIGNMENT_MARKED_FOUND after CONSIGNMENT_MARKED_LOST reverses it, and a
    set-membership test that ignored ordering would report a recovered shipment as still lost.
    """
    guard = _no_data(t)
    if guard:
        return guard
    events = _effective(t)
    lost = _dated(events, ET.MARKED_LOST)
    found = _dated(events, ET.MARKED_FOUND)
    if not lost:
        if _has_undated(events, ET.MARKED_LOST):
            return Tri.UNKNOWN, _ev("Marked lost", "lost event present but undated", Tri.UNKNOWN)
        return Tri.NO, _ev("Marked lost", "no CONSIGNMENT_MARKED_LOST on the timeline", Tri.NO)
    last_lost = lost[-1].at
    later_found = [e for e in found if e.at > last_lost]
    if later_found:
        return Tri.NO, _ev("Marked lost",
                           f"marked lost {last_lost:%Y-%m-%d} then FOUND "
                           f"{later_found[0].at:%Y-%m-%d}", Tri.NO)
    return Tri.YES, _ev("Marked lost", f"CONSIGNMENT_MARKED_LOST on {last_lost:%Y-%m-%d}",
                        Tri.YES)


def qc_failed(t: Tracking | None) -> tuple[Tri, dict]:
    """Did QC fail, with no later pass? Same ordering discipline as `marked_lost`."""
    guard = _no_data(t)
    if guard:
        return guard
    events = _effective(t)
    fails = _dated(events, ET.QC_FAILED)
    passes = _dated(events, ET.QC_PASSED)
    if not fails:
        return Tri.NO, _ev("QC failure", "no QC_FAILED / SEC_QC_FAILED on the timeline", Tri.NO)
    last_fail = fails[-1]
    later_pass = [e for e in passes if e.at > last_fail.at]
    if later_pass:
        return Tri.NO, _ev("QC failure",
                           f"{last_fail.event_type.name} on {last_fail.at:%Y-%m-%d} then "
                           f"{later_pass[0].event_type.name} on {later_pass[0].at:%Y-%m-%d}",
                           Tri.NO)
    return Tri.YES, _ev("QC failure",
                        f"{last_fail.event_type.name} on {last_fail.at:%Y-%m-%d}, no later pass",
                        Tri.YES)


def tampered(t: Tracking | None) -> tuple[Tri, dict]:
    guard = _no_data(t)
    if guard:
        return guard
    hits = _dated(_effective(t), ET.TAMPERED)
    if hits:
        return Tri.YES, _ev("Tamper", f"TAMPERED on {hits[-1].at:%Y-%m-%d}", Tri.YES)
    return Tri.NO, _ev("Tamper", "no TAMPERED scan on the timeline", Tri.NO)


def shortage_scanned(t: Tracking | None) -> tuple[Tri, dict]:
    guard = _no_data(t)
    if guard:
        return guard
    hits = _dated(_effective(t), ET.SHORTAGE)
    if hits:
        e = hits[0]
        return Tri.YES, _ev("Shortage scan", f"{e.event_type.name} on {e.at:%Y-%m-%d}", Tri.YES)
    return Tri.NO, _ev("Shortage scan", "no SHORTAGE_SCAN / MANIFEST_SHORT on the timeline",
                       Tri.NO)


def timeline_summary(t: Tracking | None) -> dict:
    """A compact evidence row describing the timeline itself. Never a verdict — context."""
    if t is None or not t.ok or not t.events:
        return _ev("Scan timeline", "unavailable", Tri.UNKNOWN)
    events = _effective(t)
    known = [e for e in events if e.known]
    unknown_types = sorted({e.raw_type for e in events if not e.known and e.raw_type})
    dated = [e for e in known if e.at]
    span = (f"{dated[0].at:%Y-%m-%d} → {dated[-1].at:%Y-%m-%d}" if dated else "undated")
    detail = (f"{len(events)} event(s) after delinks, {span}"
              + (f"; last {known[-1].event_type.name}" if known else "")
              + (f"; UNRECOGNISED types: {', '.join(unknown_types)}" if unknown_types else ""))
    return _ev("Scan timeline", detail, Tri.YES if dated else Tri.UNKNOWN)
