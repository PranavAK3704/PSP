"""Assertions for the typed Log10 scan layer. NO LLM CALLS, NO NETWORK.

    python scripts/check_log10.py

THE BUG THIS GUARDS
`_exec_hardstop` read two precomputed booleans out of a seed file:

    connected    = bool(scans and scans.get("connected_within_tat"))
    sop_followed = bool(scans and scans.get("hardstop_sop_followed", True))

The engine never looked at an event type, while the reply string *named* INWARD_SCAN and
MANIFEST_SCAN as prose. And `bool(scans and ...)` collapsed **absent** into **False** — so a
captain whose scan data had not synced was told, in effect, that their shipment never moved.
`hardstop_sop_followed` was worse: it exists in no real dataset at all.

Exit code 0 = clean.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts._contain import contain; contain()   # MUST precede every `app.` import — see _contain.py

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)


def head(n: str) -> None:
    print(f"\n{'-' * 78}\n{n}\n{'-' * 78}")


def main() -> int:
    from app.engine import policy_exec
    from app.knowledge import policies as pol
    from app.substrate import loss_db
    from app.substrate.adapters.log10 import Log10Connector, dto
    from app.substrate.adapters.log10 import event_types as ET
    from app.substrate.adapters.log10 import predicates as P
    from app.tri import Tri

    # ── 1. the enum ──────────────────────────────────────────────────────────
    head("[1] TrackingEventType — all 121, verbatim, in declaration order")
    check("121 members", len(ET.TrackingEventType) == 121, str(len(ET.TrackingEventType)))
    # Enum member names are unique by language guarantee, so checking that is a tautology. What
    # is NOT guaranteed is that the VALUES are distinct and that none collided during transcription.
    check("no duplicate VALUES", len({e.value for e in ET.TrackingEventType}) == 121)
    check("wire value == identifier (a plain Java enum, no @JsonValue)",
          all(e.value == e.name for e in ET.TrackingEventType))
    # Every value the brief called out by name.
    WANT = ["MISROUTE", "MISROUTE_SCAN", "SHORTAGE_SCAN", "MANIFEST_SHORT",
            "CONSIGNMENT_MARKED_LOST", "CONSIGNMENT_MARKED_FOUND", "QC_FAILED",
            "SEC_QC_FAILED", "TAMPERED", "VEHICLE_ARRIVED", "WRONG_FACILITY_SCAN",
            "REATTEMPT", "AUDIT_SCAN"]
    missing = [w for w in WANT if w not in ET.DECLARATION_ORDER]
    check(f"all {len(WANT)} named values present", not missing, str(missing))
    check("declaration order starts as the source does",
          ET.DECLARATION_ORDER[:3] == ("SYNC_REQUEST", "SYNC_RESPONSE", "BOOKING_RECEIVED"))
    check("   and ends as the source does", ET.DECLARATION_ORDER[-1] == "TAMPERED")
    check("from_string is case-insensitive",
          ET.from_string("misroute_scan") is ET.TrackingEventType.MISROUTE_SCAN)
    check("an unknown type is None, not an exception", ET.from_string("NOPE") is None,
          "an unrecognised event type is a data-quality signal, not a turn-killer")
    check("is_rto_event includes UPDATED_STATUS, as upstream does",
          ET.is_rto_event(ET.TrackingEventType.UPDATED_STATUS))
    check("all 9 *_DELINK values are grouped", len(ET.DELINK) == 9, str(len(ET.DELINK)))

    # ── 2. the epoch-millis boundary ─────────────────────────────────────────
    head("[2] the ONE place epoch-millis becomes a datetime")
    check("millis parse", dto.to_datetime(1.75e12) is not None)
    check("SECONDS are REFUSED, not read 1000x wrong", dto.to_datetime(1.75e9) is None,
          "1.7e9 read as millis is Jan 1970, which makes every date rule fire on a 55y-old date")
    check("None stays None", dto.to_datetime(None) is None)
    check("a bool is not a timestamp", dto.to_datetime(True) is None)
    check("garbage stays None", dto.to_datetime("soon") is None)
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    check("round-trips", dto.to_datetime(dto.to_millis(now)) == now)

    # ── 3. fixtures against the contract ─────────────────────────────────────
    head("[3] fixtures — wire shape and provenance")
    c = Log10Connector()
    awbs = c.known_awbs()
    check("fixtures exist", bool(awbs), f"{len(awbs)}: {', '.join(awbs[:4])}…")
    both_shapes = {("VLR" if a.startswith("VLR") else "VL") for a in awbs}
    check("both real AWB shapes are represented", both_shapes == {"VL", "VLR"},
          str(sorted(both_shapes)) + " — the lexer bug was that only one was matched")
    for awb in awbs:
        raw = c.get_tracking_raw(awb)
        bad = dto.contract_problems(raw)
        check(f"{awb} matches the wire contract", not bad, "; ".join(bad[:2]))
        check(f"{awb} _provenance stripped from the payload",
              not any(k.startswith("_") for k in raw),
              "a key the real API never sends must not reach a consumer")
        pv = c.provenance(awb)
        check(f"{awb} says it is a fixture on disk",
              pv.get("fixture") is True and bool(pv.get("derived_from_real_columns"))
              and bool(pv.get("synthesised")),
              f"branch={pv.get('branch')}")
        check(f"{awb} is a REAL awb from the ledger",
              loss_db.get_loss_by_awb(awb) is not None)
    check("source is the fixture label, never the live one", c.source == "log10-fixture")
    saved = dict(os.environ)
    os.environ["PSP_LOG10_SOURCE"] = "live"
    raised = ""
    try:
        Log10Connector().get_tracking_raw("VL0000000000001")
    except NotImplementedError as e:
        raised = str(e)
    check("live raises rather than serving a file", bool(raised))
    check("   and names the 200-on-error semantics", "status.code" in raised, raised[:70])
    check("   and refuses to invent a shipments listing",
          "no captain->shipments listing" in
          (lambda: (Log10Connector().get_shipments("x"), "")[1] if False else _err(Log10Connector))(),
          "the GET is per-waybill only")
    os.environ.clear(); os.environ.update(saved)

    # ── 4. the tri-state, which is the point ─────────────────────────────────
    head("[4] UNKNOWN is not NO")
    by_branch = {c.provenance(a).get("branch"): a for a in awbs}
    EXPECT = {
        "connected_in_tat":       (P.forward_connection_within, Tri.YES),
        "stalled_past_tat":       (P.forward_connection_within, Tri.NO),
        "no_inscan":              (P.forward_connection_within, Tri.UNKNOWN),
        "empty_timeline":         (P.forward_connection_within, Tri.UNKNOWN),
        "misroute_in_window":     (P.misroute_within, Tri.YES),
        "three_attempts":         (P.attempts_within, Tri.YES),
        "qc_failed":              (P.qc_failed, Tri.YES),
        "shortage_with_arrival":  (P.shortage_scanned, Tri.YES),
        "marked_lost_then_found": (P.marked_lost, Tri.NO),
        "delinked_connection":    (P.forward_connection_within, Tri.NO),
    }
    for branch, (fn, want) in EXPECT.items():
        awb = by_branch.get(branch)
        if not awb:
            check(f"{branch} fixture present", False); continue
        got, row = fn(c.get_tracking(awb))
        check(f"{branch:24} {fn.__name__} → {want.value}", got is want,
              f"got {got.value}: {row['value'][:60]}")
    check("no timeline at all → UNKNOWN on every predicate",
          all(f(None)[0] is Tri.UNKNOWN for f in
              (P.forward_connection_within, P.attempts_within, P.misroute_within,
               P.marked_lost, P.qc_failed, P.tampered, P.shortage_scanned)),
          "this is the case `bool(scans and ...)` turned into False")
    check("every evidence row carries its verdict",
          P.forward_connection_within(None)[1].get("verdict") == "UNKNOWN",
          "so a reviewer sees UNKNOWN rather than inferring it from a missing line")
    check("Tri.all_yes propagates ignorance",
          __import__("app.tri", fromlist=["x"]).all_yes(Tri.YES, Tri.UNKNOWN) is Tri.UNKNOWN)
    check("   and an empty conjunction is UNKNOWN, not YES",
          __import__("app.tri", fromlist=["x"]).all_yes() is Tri.UNKNOWN,
          "'no checks ran' must never read as 'everything passed'")

    # ── 5. the subtleties ────────────────────────────────────────────────────
    head("[5] delinks, ordering, and out-of-order input")
    awb = by_branch.get("delinked_connection")
    if awb:
        raw = c.get_tracking_raw(awb)
        with_dl, _ = P.forward_connection_within(dto.parse_service_response(raw, awb))
        stripped = copy.deepcopy(raw)
        stripped["response"]["tracking"] = [
            e for e in stripped["response"]["tracking"] if not e["eventType"].endswith("_DELINK")]
        without_dl, _ = P.forward_connection_within(dto.parse_service_response(stripped, awb))
        check("a DELINKED connection is NOT credited",
              with_dl is Tri.NO and without_dl is Tri.YES,
              f"with delink={with_dl.value}, without={without_dl.value}")
        rev = copy.deepcopy(raw); rev["response"]["tracking"].reverse()
        check("out-of-order input is re-sorted defensively",
              P.forward_connection_within(dto.parse_service_response(rev, awb))[0] is with_dl,
              "the live service sorts; a fixture edited on stage might not")
    awb = by_branch.get("marked_lost_then_found")
    if awb:
        check("MARKED_FOUND after MARKED_LOST reverses it",
              P.marked_lost(c.get_tracking(awb))[0] is Tri.NO,
              "a set-membership test that ignored order would report a recovered shipment as lost")
    # A sliding window, not a count since the first attempt.
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    spread = {"status": {"code": 200}, "response": {"tracking": [
        {"eventType": "DRS_SCAN", "eventTime": dto.to_millis(base + timedelta(days=d)), "data": {}}
        for d in (0, 10, 20)], "consignment": {"attempts": 3, "consignmentPODBO": []}, "drses": []}}
    check("3 attempts spread over 20d does NOT satisfy '3 within 7d'",
          P.attempts_within(dto.parse_service_response(spread))[0] is Tri.NO,
          "a running count since the first attempt would wrongly say YES")
    # A counter that disagrees with the timeline is UNKNOWN, not NO.
    counter_only = {"status": {"code": 200}, "response": {"tracking": [
        {"eventType": "INWARD_SCAN", "eventTime": dto.to_millis(base), "data": {}}],
        "consignment": {"attempts": 5, "consignmentPODBO": []}, "drses": []}}
    check("consignment.attempts without attempt scans → UNKNOWN",
          P.attempts_within(dto.parse_service_response(counter_only))[0] is Tri.UNKNOWN,
          "a counter cannot place attempts inside a window")
    # The envelope's own error channel — HTTP was 200.
    err = {"status": {"code": 500, "message": "boom"}}
    t_err = dto.parse_service_response(err)
    check("status.code != 200 is read as an error despite HTTP 200",
          not t_err.ok and "500" in t_err.error, t_err.error[:50])
    check("   and every predicate then returns UNKNOWN",
          P.forward_connection_within(t_err)[0] is Tri.UNKNOWN)

    # ── 6. the legacy seed path, and PARITY ──────────────────────────────────
    head("[6] the seed captains flow through the SAME typed predicates")
    from app.substrate.seed import SEED
    blob = SEED["VLMO-CPT-3310"]["scans"]["VL0093310077"]
    t = dto.from_legacy_scans(blob)
    check("the legacy blob lifts into a Tracking", t is not None and len(t.events) >= 3,
          f"{len(t.events)} events" if t else "None")
    derived, _row = P.forward_connection_within(t)
    check("the DERIVED verdict matches the blob's old boolean",
          (derived is Tri.YES) == bool(blob["connected_within_tat"]),
          f"derived={derived.value}, file said {blob['connected_within_tat']} — parity")
    check("the precomputed booleans are ignored, not read",
          "connected_within_tat" not in
          __import__("inspect").getsource(dto.from_legacy_scans).split('"""')[2],
          "they were answers written into a file; the point is to derive them")

    # ── 7. _exec_hardstop, and its reachability ──────────────────────────────
    head("[7] _exec_hardstop on the typed path")
    FAKE = "VL9999999999999"
    check("the fixture AWBs take the _eval_real_loss branch, not this one",
          loss_db.get_loss_by_awb(awbs[0]) is not None,
          "_exec_hardstop is reachable only for an AWB ABSENT from the loss ledger")
    ctx = {"captain_id": "VLMO-CPT-3310", "profile": {}, "cash": {}, "shipments": [], "losses": [],
           "ledger": [{"id": "D1", "type": "debit", "amount_inr": 244, "date": "2026-06-01",
                       "reason": "hardstop", "awb": FAKE, "status": "posted"}]}
    d = policy_exec.execute("hardstop_loss", ctx, {"awb": FAKE})
    c2 = next((x for x in d["checks_run"] if x["id"] == "attributable_to_partner"), {})
    check("no scan data → escalate", d["action"] == "escalate")
    check("   attributable is None, NOT True", d.get("attributable") is None,
          "'we cannot see the scans' and 'it was your fault' are opposite claims")
    check("   the check records the tri-state verdict", c2.get("verdict") == "UNKNOWN")
    check("   the reason says undetermined, not attributable",
          "can't determine" in d["reason"] and "attributable" not in d["reason"].lower(),
          d["reason"][:70])
    check("   the UNKNOWN evidence row is not repeated",
          len([e for e in d["evidence_trail"]
               if e.get("value") == "no tracking data available"]) == 1,
          "three predicates share one no-data row")
    # Asserted as a READ, not as a word: the comment that explains what replaced this field
    # necessarily contains its name, so a plain substring search finds the explanation.
    hs_src = __import__("inspect").getsource(policy_exec._exec_hardstop)
    reads = ['get("hardstop_sop_followed"', '["hardstop_sop_followed"]',
             "get('hardstop_sop_followed'", "['hardstop_sop_followed']"]
    check("nothing READS hardstop_sop_followed any more", not any(r in hs_src for r in reads),
          "it was a boolean written into a seed file; it exists in no real dataset")
    reads_cw = ['get("connected_within_tat"', '["connected_within_tat"]']
    check("nothing READS the precomputed connected_within_tat either",
          not any(r in hs_src for r in reads_cw),
          "the verdict is derived from the events now")

    # ── 8. THE REGRESSION THAT MATTERS: the money branch mix is unchanged ─────
    head("[8] the money path is UNCHANGED — scan evidence is additive")
    rows = loss_db._query("SELECT awb FROM losses LIMIT 400", ())
    mix: dict[str, int] = {}
    empty = {"captain_id": "P", "profile": {}, "ledger": [], "losses": [], "cash": {},
             "shipments": []}
    for r in rows:
        dd = policy_exec.execute("hardstop_loss", empty, {"awb": str(r["awb"])})
        mix[dd["action"]] = mix.get(dd["action"], 0) + 1
    n = sum(mix.values()) or 1
    pct = {k: round(100 * v / n, 1) for k, v in sorted(mix.items())}
    print(f"       {n} AWBs -> {mix}  ({pct})")

    # The INVARIANT is additivity, not a remembered percentage. An earlier note recorded
    # 58.2/25.0/16.8 from a 400-AWB sample whose selection is not reproducible from here
    # (`LIMIT 400` is a different 400 rows), so asserting that number would be asserting
    # someone's arithmetic rather than this code's behaviour.
    #
    # What CAN be asserted exactly: with the scan-evidence block disabled, every decision must
    # be identical. That is what "additive" means, and it is the thing that would break if the
    # typed timeline ever started influencing the money branch.
    import app.engine.policy_exec as PE
    orig = PE._tracking_for
    PE._tracking_for = lambda *a, **k: None          # disable the scan-evidence block
    try:
        baseline = [PE.execute("hardstop_loss", empty, {"awb": str(r["awb"])}) for r in rows]
    finally:
        PE._tracking_for = orig
    withscan = [PE.execute("hardstop_loss", empty, {"awb": str(r["awb"])}) for r in rows]
    KEYS = ("action", "confidence", "amount_inr", "disposition")
    diff = [(str(rows[i]["awb"]), {k: (baseline[i].get(k), withscan[i].get(k)) for k in KEYS
                                  if baseline[i].get(k) != withscan[i].get(k)})
            for i in range(len(rows))
            if any(baseline[i].get(k) != withscan[i].get(k) for k in KEYS)]
    # True by construction TODAY (the block only appends to `ev`), which is exactly why it is
    # worth pinning: the day someone makes the scan verdict influence the branch, this fails.
    check(f"scan evidence changes NO decision across {len(rows)} AWBs", not diff,
          f"{len(diff)} changed: {diff[:2]}" if diff
          else "identical with the block on and off — a regression tripwire, not a discovery")
    # Actually run it TWICE. The old version asserted `sum(mix.values()) == len(rows)` — true by
    # construction, one increment per row — and that the action set was a subset of the only
    # three values the executors can return. It claimed "stable across runs" while running once.
    mix2: dict[str, int] = {}
    for r in rows:
        dd = policy_exec.execute("hardstop_loss", empty, {"awb": str(r["awb"])})
        mix2[dd["action"]] = mix2.get(dd["action"], 0) + 1
    check("the distribution IS stable across two runs", mix == mix2,
          f"{mix} vs {mix2}")

    # Which AWBs actually gain scan evidence: only the ones with a fixture. Reported rather
    # than asserted as a ratio, because it is a property of how many fixtures exist.
    fixture_awbs = set(awbs)
    covered = [r for r in rows if str(r["awb"]) in fixture_awbs]
    print(f"       {len(covered)}/{len(rows)} of this sample have a scan fixture "
          f"({len(fixture_awbs)} fixtures exist)")
    for awb in sorted(fixture_awbs):
        branch = c.provenance(awb).get("branch", "?")
        d2 = PE.execute("hardstop_loss", empty, {"awb": awb})
        srows = [e for e in d2["evidence_trail"] if e.get("source") == "log10_scans"]
        if branch == "empty_timeline":
            # A timeline with no events has nothing to evidence. Adding a row would assert a
            # reading that was never made.
            check(f"{branch:24} carries NO scan evidence", not srows,
                  "an empty timeline is not a finding")
        else:
            check(f"{branch:24} carries scan evidence",
                  bool(srows), f"{len(srows)} row(s): {[e.get('verdict') for e in srows]}")
    conflicts = [awb for awb in sorted(fixture_awbs)
                 if any(e.get("label") == "Signal conflict" for e in
                        PE.execute("hardstop_loss", empty, {"awb": awb})["evidence_trail"])]
    print(f"       {len(conflicts)}/{len(fixture_awbs)} fixtures show a column-vs-timeline "
          f"CONFLICT — surfaced, not resolved")

    print(f"\n{'=' * 78}")
    if FAILED:
        print(f"FAILED {len(FAILED)}:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("TYPED SCAN LAYER VERIFIED — UNKNOWN never reads as NO, money mix unchanged.")
    return 0


def _err(cls) -> str:
    import os as _os
    saved = _os.environ.get("PSP_LOG10_SOURCE")
    _os.environ["PSP_LOG10_SOURCE"] = "live"
    try:
        cls().get_shipments("x")
        return ""
    except NotImplementedError as e:
        return str(e)
    finally:
        if saved is None:
            _os.environ.pop("PSP_LOG10_SOURCE", None)
        else:
            _os.environ["PSP_LOG10_SOURCE"] = saved


if __name__ == "__main__":
    raise SystemExit(main())
