"""Build Log10 tracking fixtures for REAL AWBs, one per Losses & Debits branch.

    python scripts/build_log10_fixtures.py
    python scripts/build_log10_fixtures.py --list        # show what each branch selected
    python scripts/build_log10_fixtures.py --force       # overwrite existing fixtures

WHAT IS REAL AND WHAT IS NOT — the only question that matters here
Every fixture carries a `_provenance` block naming, field by field, what was DERIVED from a
real `losses` row and what was SYNTHESISED. The repo rule this serves is
`prism_provider`'s: *fake data wearing a real provenance label is worse than an outage.* So the
AWB is real, the dates that exist in the export are real, and everything a scan timeline needs
that the export does not contain is invented and says so.

Derived from real columns:
    awb                     -> waybillNo
    facility_inscan         -> INWARD_SCAN.eventTime
    lost_date / actual_lost_date -> CONSIGNMENT_MARKED_LOST.eventTime
    created_date            -> BOOKING.eventTime
    current_movement_type   -> consignmentStatus / flowType
    location                -> location.id
    shipment_value          -> consignmentAmount
    loss_value              -> payableAmount

Synthesised (the export has none of it):
    DRS codes and users, POD links, intermediate MANIFEST_SCAN / THC_SCAN / DRS_SCAN events,
    VEHICLE_ARRIVED, attempt timings, MISROUTE placement, and all identifiers inside `data`.

Dates come from the row, so nothing here needs a clock — which also keeps the output stable
across runs, so a rebuild produces no diff unless the data changed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "log10_fixtures"

# One fixture per branch the L&D reasoning has to distinguish. The SQL picks a REAL row whose
# columns actually support the branch, so the derived dates are genuine even where the events
# around them are not.
BRANCHES = [
    ("connected_in_tat",
     "an in-scan followed by an onward MANIFEST_SCAN inside the 7-day window → YES",
     "SELECT * FROM losses WHERE facility_inscan != '' AND lost_date != '' LIMIT 1"),
    ("stalled_past_tat",
     "an in-scan and NO onward movement → NO (evidence against reversal)",
     "SELECT * FROM losses WHERE facility_inscan != '' AND reason_l1 = 'hardstop' LIMIT 1 OFFSET 3"),
    ("no_inscan",
     "no facility in-scan at all → UNKNOWN, not NO (the clock never started)",
     "SELECT * FROM losses WHERE (facility_inscan = '' OR facility_inscan IS NULL) LIMIT 1"),
    ("misroute_in_window",
     "MISROUTE_SCAN two days after in-scan → YES on S11",
     "SELECT * FROM losses WHERE facility_inscan != '' LIMIT 1 OFFSET 7"),
    ("three_attempts",
     "three DRS_SCAN attempts inside seven days → YES on S5/S6",
     "SELECT * FROM losses WHERE facility_inscan != '' LIMIT 1 OFFSET 11"),
    ("marked_lost_then_found",
     "CONSIGNMENT_MARKED_LOST then CONSIGNMENT_MARKED_FOUND → NO (order matters)",
     "SELECT * FROM losses WHERE lost_date != '' LIMIT 1 OFFSET 41"),
    ("shortage_with_arrival",
     "VEHICLE_ARRIVED then SHORTAGE_SCAN → the Shortage S2 24h window is measurable",
     "SELECT * FROM losses WHERE reason_l1 LIKE '%shortage%' LIMIT 1"),
    ("qc_failed",
     "SEC_QC_FAILED with no later pass",
     "SELECT * FROM losses WHERE reason_l1 LIKE '%qc%' LIMIT 1"),
    ("delinked_connection",
     "a MANIFEST_SCAN that was DELINKED — the connection must not be credited",
     "SELECT * FROM losses WHERE facility_inscan != '' LIMIT 1 OFFSET 23"),
    ("empty_timeline",
     "tracking returns [] → every predicate UNKNOWN",
     "SELECT * FROM losses WHERE facility_inscan != '' LIMIT 1 OFFSET 29"),
]


def _d(raw) -> datetime | None:
    """A date cell from the export → aware UTC datetime."""
    s = str(raw or "").strip()
    if not s or s.lower() in ("null", "none", "nan"):
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:19] if " " in s else s[:10], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _ms(dt: datetime | None) -> int | None:
    return None if dt is None else int(dt.timestamp() * 1000)


def _ev(kind: str, dt: datetime, node: str = "", data: dict | None = None) -> dict:
    """One WaybillTrackingDTO. `data` is `{}` at minimum — never null on the wire."""
    return {"eventTime": _ms(dt), "eventType": kind,
            "user": {"id": 90210, "name": node or "hub-ops", "username": "svc.ops",
                     "location": {"id": 1001, "name": node or "hub"}},
            "data": data or {}}


def _consignment(row: dict, status: str, flow: str, inscan: datetime | None,
                 cpd: datetime | None, attempts: int) -> dict:
    loc = str(row.get("location") or "").strip()
    return {
        "id": int(loc) if loc.isdigit() else None,
        "waybillNo": str(row.get("awb") or "").strip().upper(),
        "description": None,
        "totalShipmentCount": 1,
        "totalWeight": None,
        # One 'e' — the wire name is misspelled upstream and mirroring it is the point.
        "totalChargableWeight": None,
        "consignmentAmount": _f(row.get("shipment_value")),
        "payableAmount": _f(row.get("loss_value")),
        "expectedDeliveryDate": _ms(cpd),
        "location": {"id": int(loc) if loc.isdigit() else None, "name": str(row.get("loc2") or "") or None},
        "partner": {"id": 268, "name": "Valmo"},
        "partnerId": 268,
        "customerPickupLoc": None,
        "bookingDate": _ms(_d(row.get("created_date"))),
        "bookingOfficeLoc": None,
        "consignmentStatus": status,
        "flowType": flow,
        "updatedAt": _ms(inscan),
        "customerPromiseDate": _ms(cpd),
        # Field initialiser = 0L upstream, so always present.
        "attempts": attempts,
        # mapContact hardcodes "0000000000" regardless of the DB. A real-looking number here
        # would be LESS faithful to the contract.
        "shipper": {"name": "Meesho Seller", "address": None,
                    "primaryNumber": "0000000000", "secondaryNumber": "0000000000"},
        "consignee": {"name": "Customer", "address": None,
                      "primaryNumber": "0000000000", "secondaryNumber": "0000000000"},
        "shipperPincode": None,
        "consigneePincode": None,
        "customerId": None,
        "customerName": None,
        "customerPhoneNumber": None,
        "customerEmailId": None,
        # The JSON key is consignmentPODBO but the TYPE is List<PODImageDTO>; mapPodImages
        # returns [] when all links are null, so it is never absent.
        "consignmentPODBO": [],
    }


def _f(v):
    try:
        return float(str(v).replace(",", "")) if str(v or "").strip() else None
    except ValueError:
        return None


def build(branch: str, row: dict) -> dict:
    """Assemble one fixture. Every event's placement is stated in `_provenance`."""
    awb = str(row.get("awb") or "").strip().upper()
    inscan = _d(row.get("facility_inscan"))
    lost = _d(row.get("lost_date")) or _d(row.get("actual_lost_date"))
    created = _d(row.get("created_date"))
    mv = str(row.get("current_movement_type") or "").strip()
    flow = "REVERSE" if any(k in mv.lower() for k in ("rto", "reverse", "rvp")) else "FORWARD"
    anchor = inscan or lost or created or datetime(2026, 6, 1, tzinfo=timezone.utc)

    events: list[dict] = []
    synth: list[str] = []
    derived = [f"waybillNo <- losses.awb ({awb})"]

    if created:
        events.append(_ev("BOOKING", created)); derived.append("BOOKING.eventTime <- created_date")
    if inscan and branch != "no_inscan":
        events.append(_ev("INWARD_SCAN", inscan, "LM-DC"))
        derived.append("INWARD_SCAN.eventTime <- facility_inscan")

    if branch == "connected_in_tat":
        events.append(_ev("MANIFEST_SCAN", anchor + timedelta(days=2), "LM-DC"))
        events.append(_ev("DRS_SCAN", anchor + timedelta(days=3), "LM-DC"))
        synth += ["MANIFEST_SCAN at in-scan +2d", "DRS_SCAN at +3d"]
    elif branch == "stalled_past_tat":
        events.append(_ev("MANIFEST_SCAN", anchor + timedelta(days=11), "LM-DC"))
        synth.append("MANIFEST_SCAN at in-scan +11d (deliberately outside the 7d window)")
    elif branch == "no_inscan":
        events.append(_ev("MANIFEST_SCAN", anchor + timedelta(days=1), "LM-DC"))
        synth.append("MANIFEST_SCAN at +1d, with NO in-scan so the clock never starts")
    elif branch == "misroute_in_window":
        events.append(_ev("MISROUTE_SCAN", anchor + timedelta(days=2), "WRONG-DC",
                          {"misrouteType": "WRONG_DESTINATION"}))
        synth.append("MISROUTE_SCAN at in-scan +2d")
    elif branch == "three_attempts":
        for i, d in enumerate((1, 3, 5)):
            events.append(_ev("DRS_SCAN" if i == 0 else "REATTEMPT", anchor + timedelta(days=d),
                              "LM-DC"))
        events.append(_ev("UNDELIVERED", anchor + timedelta(days=5, hours=6), "LM-DC"))
        synth.append("3 attempts at +1d/+3d/+5d, then UNDELIVERED")
    elif branch == "marked_lost_then_found":
        if lost:
            events.append(_ev("CONSIGNMENT_MARKED_LOST", lost))
            derived.append("CONSIGNMENT_MARKED_LOST.eventTime <- lost_date")
            events.append(_ev("CONSIGNMENT_MARKED_FOUND", lost + timedelta(days=4)))
            synth.append("CONSIGNMENT_MARKED_FOUND at lost_date +4d (ordering test)")
    elif branch == "shortage_with_arrival":
        events.append(_ev("VEHICLE_ARRIVED", anchor + timedelta(hours=2), "LM-DC",
                          {"vehicleNo": "SYNTHETIC"}))
        events.append(_ev("SHORTAGE_SCAN", anchor + timedelta(hours=5), "LM-DC"))
        synth.append("VEHICLE_ARRIVED at +2h, SHORTAGE_SCAN at +5h")
    elif branch == "qc_failed":
        events.append(_ev("SEC_QC_FAILED", anchor + timedelta(days=1), "QC-BAY",
                          {"qcDetails": "SYNTHETIC"}))
        synth.append("SEC_QC_FAILED at +1d, no later pass")
    elif branch == "delinked_connection":
        events.append(_ev("MANIFEST_SCAN", anchor + timedelta(days=2), "LM-DC"))
        events.append(_ev("MANIFEST_SCAN_DELINK", anchor + timedelta(days=2, hours=3), "LM-DC"))
        synth.append("MANIFEST_SCAN at +2d then MANIFEST_SCAN_DELINK 3h later — the connection "
                     "was UNDONE and must not be credited")
    elif branch == "empty_timeline":
        events = []
        synth.append("tracking deliberately EMPTY, so every predicate must return UNKNOWN")

    if lost and branch not in ("marked_lost_then_found", "empty_timeline"):
        events.append(_ev("CONSIGNMENT_MARKED_LOST", lost))
        derived.append("CONSIGNMENT_MARKED_LOST.eventTime <- lost_date")

    events.sort(key=lambda e: e["eventTime"] or 0)
    attempts = sum(1 for e in events if e["eventType"] in ("DRS_SCAN", "REATTEMPT"))
    status = {"connected_in_tat": "IN_TRANSIT", "stalled_past_tat": "IN",
              "marked_lost_then_found": "IN", "shortage_with_arrival": "IN",
              "empty_timeline": "IN"}.get(branch, mv.upper() or "IN")

    return {
        "_provenance": {
            "fixture": True,
            "branch": branch,
            "contract_source": ("log10-backend-valmo-develop :: TrackingService "
                                "ConsignmentTrackingController + WaybillTrackingResponse"),
            "real_awb": awb,
            "derived_from_real_columns": derived,
            "synthesised": synth + [
                "user / DRS identifiers", "POD links (empty list)",
                "all values inside event `data`"],
            "not_real_data": ("The AWB and the dates listed under derived_from_real_columns come "
                             "from a real losses row. The scan EVENTS around them do not exist "
                             "in that export and were constructed to exercise one branch. "
                             "Served as log10-fixture, never as log10."),
        },
        # The envelope, exactly as the wire carries it — including the 200-on-error semantics.
        "status": {"code": 200, "message": "SUCCESS"},
        "response": {
            "tracking": events,
            "consignment": _consignment(row, status, flow, inscan,
                                        (anchor + timedelta(days=4)), attempts),
            "drses": ([{"id": 55501, "drsCode": "DRS-SYNTHETIC-01",
                        "drsUser": {"id": 90210, "name": "field-exec", "username": "svc.fe",
                                    "location": {"id": 1001, "name": "LM-DC"}}}]
                      if attempts else []),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    from app.substrate import loss_db
    from app.substrate.adapters.log10 import dto

    if not loss_db.available():
        print("no loss DB — cannot pick real AWBs")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    written, problems = 0, []
    seen: set[str] = set()
    for branch, why, sql in BRANCHES:
        rows = loss_db._query(sql, ())
        if not rows:
            print(f"  --   {branch:24} no row matched — skipped ({why})")
            continue
        row = rows[0]
        awb = str(row.get("awb") or "").strip().upper()
        if not awb or awb in seen:
            print(f"  --   {branch:24} duplicate/blank AWB {awb!r} — skipped")
            continue
        seen.add(awb)
        fx = build(branch, row)
        bad = dto.contract_problems(fx)
        if bad:
            problems.append((branch, bad))
            print(f"  FAIL {branch:24} {awb}  contract: {bad[:2]}")
            continue
        path = OUT / f"{awb}.json"
        if path.exists() and not a.force and not a.list:
            print(f"  skip {branch:24} {awb}  (exists; --force to overwrite)")
            continue
        n_ev = len(fx["response"]["tracking"])
        print(f"  ok   {branch:24} {awb}  {n_ev} event(s)  {why[:52]}")
        if not a.list:
            path.write_text(json.dumps(fx, indent=1, ensure_ascii=False) + "\n")
            written += 1

    print(f"\n{written} fixture(s) written to {OUT}")
    if problems:
        print("CONTRACT PROBLEMS:")
        for b, p in problems:
            print(f"  {b}: {p}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
