"""Per-AWB lookup against the local SQLite database (backend/data/valmo.db).

Tables (all keyed on awb): `losses` (the big loss export), and three enrichment tables
synced from Metabase — `loss_attrib` (loss + credit-note `cn_flag` + `lm_facility_inscan`),
`pendency` (current shipment state / leg / status), `attrib_change` (attribution before→after).
A disputed AWB is looked up in `losses` (or `loss_attrib` as a fallback) and enriched with its
current pendency + attribution history so the engine sees the full story. Absent db / no match
⇒ callers fall back to the honest "couldn't locate" escalation.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_DATA = Path(__file__).resolve().parents[2] / "data"
_DB = _DATA / "valmo.db"                      # full local file (dev)
_DB_FALLBACK = _DATA / "valmo_fallback.db"    # small subset baked into the image (offline safety net)

# Data source: EXTERNAL Turso (libSQL over HTTPS) if TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are
# set, else the LOCAL baked SQLite file. Same SQL runs on both (libSQL is SQLite-compatible).
# The remote path is fully guarded — any failure falls back to the local file, so the working
# local demo can never break.
_MODE = None                 # 'remote' | 'local' | 'none'
_url = _tok = None
_local: sqlite3.Connection | None = None
_tables: set | None = None


def _init():
    global _MODE, _url, _tok, _local
    if _MODE is not None:
        return
    url, tok = os.environ.get("TURSO_DATABASE_URL"), os.environ.get("TURSO_AUTH_TOKEN")
    if url and tok:
        try:
            from . import turso_http
            turso_http.execute(url, tok, "SELECT 1")   # probe
            _url, _tok, _MODE = url, tok, "remote"
        except Exception:                              # noqa: BLE001 — fall back to local
            _url = _tok = None
    if _MODE is None:
        path = _DB if _DB.exists() else (_DB_FALLBACK if _DB_FALLBACK.exists() else None)
        if path is not None:
            _local = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
            _local.row_factory = sqlite3.Row
            _MODE = "local"
    if _MODE is None:
        _MODE = "none"


def source() -> str:
    _init()
    return _MODE


def available() -> bool:
    _init()
    return _MODE in ("remote", "local")


def _all_tables() -> set:
    global _tables
    if _tables is None:
        _tables = set(r["name"] for r in _query("SELECT name FROM sqlite_master WHERE type='table'", ()))
    return _tables


def _query(sql: str, params: tuple) -> list[dict]:
    """Run a read query on whichever source is active; return list-of-dict rows."""
    _init()
    if _MODE == "remote":
        from . import turso_http
        return turso_http.execute(_url, _tok, sql, params)
    if _MODE == "local":
        return [dict(r) for r in _local.execute(sql, params).fetchall()]
    return []


def _rows(table: str, awb: str) -> list[dict]:
    if table not in _all_tables():
        return []
    return _query(f"SELECT * FROM {table} WHERE awb = ?", (awb,))


def _consolidate(rows: list[dict], inscan_col: str = "facility_inscan") -> dict:
    """Collapse multiple rows for one AWB (multi-row ⇒ attribution changed). Latest is the
    representative; reversal signals are aggregated across all rows; facility-inscan is
    normalised to `facility_inscan` regardless of source column."""
    rows.sort(key=lambda r: (r.get("lost_date") or r.get("created_date") or "", r.get("created_date") or ""))
    primary = dict(rows[-1])
    primary["row_count"] = len(rows)
    inscan = next((r.get(inscan_col) for r in rows if (r.get(inscan_col) or "").strip()), primary.get(inscan_col, ""))
    primary["facility_inscan"] = inscan or ""
    if len(rows) > 1 or any((r.get("attribution_changed") or "").lower() == "yes" for r in rows):
        primary["attribution_changed"] = "yes"
    return primary


# loss-attribution ledger loss_type → our disposition reason_l1
_LT_TO_RL1 = {"facility": "hardstop", "shipment shortage": "shipment_shortage",
              "bag shortage": "bag_shortage", "in transit": "intransit"}


def _normalize_attribution(rows: list[dict]) -> dict:
    """Map a loss-attribution-ledger row into the loss-row shape the engine expects.
    Reversal here = attribution_type 'loss_reversal' or a 'reversal' state (NOT mere cn_number
    presence — CN accompanies active loss debits too)."""
    row = rows[-1]
    reversed_ = ((row.get("attribution_type") or "").lower() == "loss_reversal"
                 or "reversal" in (row.get("attribution_state") or "").lower())
    return {
        "awb": row.get("awb"),
        "reason_l1": _LT_TO_RL1.get((row.get("loss_type") or "").lower(), "others"),
        "loss_value": row.get("attribution_amount"), "loss_percentage": "100%",
        "facility_inscan": "", "attribution_changed": "yes" if reversed_ else "no",
        "cn_flag": "yes" if reversed_ else "no", "cn_number": row.get("cn_number", ""),
        "current_movement_type": row.get("loss_type", ""), "leg": "", "location": "",
        "row_count": len(rows), "_src": "attribution",
        "_attribution_state": row.get("attribution_state"), "_attribution_type": row.get("attribution_type"),
    }


def get_loss_by_awb(awb: str) -> dict | None:
    """Consolidated loss row for an AWB, in priority order: `losses` (full export, has
    facility_inscan/reason_l1) → `loss_attrib` → the `attribution` ledger (debit/reversal state).
    None if absent everywhere."""
    if not available() or not awb:
        return None
    awb = awb.strip().upper()
    rows = _rows("losses", awb)
    if rows:
        out = _consolidate(rows, "facility_inscan"); out["_src"] = "losses"; return out
    rows = _rows("loss_attrib", awb)
    if rows:
        out = _consolidate(rows, "lm_facility_inscan"); out["_src"] = "loss_attrib"; return out
    rows = _rows("attribution", awb)
    if rows:
        return _normalize_attribution(rows)
    rows = _rows("qc_fail", awb)
    if rows:
        return _normalize_qc(rows)
    return None


def _normalize_qc(rows: list[dict]) -> dict:
    """Map a secondary-QC-fail evidence row into the loss-row shape. QC PASS or an LM in-scan
    is the reversal signal; `price` is the debit; reason_l1 falls back to secondary_qc_fail."""
    row = rows[-1]
    inscan = (row.get("rto_shipment_inscan_at_lm") or "").strip()
    inscan = inscan[:10] if inscan[:1].isdigit() else ""     # a real date, not 'null'/blank
    qc_pass = "pass" in ((row.get("sec_qc_lm_status") or "") + (row.get("sec_qc_fm_status") or "")).lower()
    return {
        "awb": row.get("awb"),
        "reason_l1": (row.get("reason_l1") or "").strip() or "secondary_qc_fail",
        "loss_value": row.get("price"), "loss_percentage": "100%",
        "facility_inscan": inscan, "attribution_changed": "no",
        "cn_flag": "no", "cn_number": "",
        "current_movement_type": "rto", "leg": "", "location": row.get("hub_location", ""),
        "row_count": len(rows), "_src": "qc_fail",
        "_qc_status": (row.get("sec_qc_lm_status") or row.get("sec_qc_fm_status") or ""),
        "_qc_pass": qc_pass,
    }


def get_attribution(awb: str) -> dict | None:
    """Raw loss-attribution-ledger row (debit/reversal state) for evidence enrichment."""
    if not available() or not awb:
        return None
    rows = _rows("attribution", awb.strip().upper())
    return rows[-1] if rows else None


def get_pendency(awb: str) -> dict | None:
    if not available() or not awb:
        return None
    rows = _rows("pendency", awb.strip().upper())
    return rows[0] if rows else None


def get_attrib_change(awb: str) -> dict | None:
    if not available() or not awb:
        return None
    rows = _rows("attrib_change", awb.strip().upper())
    return rows[0] if rows else None


# ─────────────────────────────────────────────────────────────────────────────
# Captain-keyed readers — the real per-captain loss/debit ledger.
#
# Everything above is AWB-keyed (the single-dispute path: "is THIS debit wrong?").
# The Captain-360 panel needs the inverse: every debit/reversal a captain carries,
# keyed on `attribution.partner_id`. This is the loss-attribution ledger — one row
# per AWB debit or reversal — and it is what proves the platform runs on real data.
# All values are read straight from valmo.db; nothing here is seeded.
# ─────────────────────────────────────────────────────────────────────────────

# loss_type → the SOP disposition the engine + panel share (same map the AWB path uses).
_LT_TO_DISP = {"facility": "hardstop_loss", "shipment shortage": "shipment_shortage",
               "bag shortage": "bag_shortage", "in transit": "intransit"}

# vetan payout status → a plain lifecycle word (what a captain actually experiences):
#   SUCCEEDED = the debit was recovered from the captain; PENDING = raised, not yet recovered;
#   REVERSED  = given back; FAILED = the debit attempt failed.
_STATUS_LABEL = {"SUCCEEDED": "recovered", "PENDING": "pending",
                 "FAILED": "failed", "REVERSED": "reversed"}


def _has_captain_ledger() -> bool:
    return "attribution" in _all_tables()


def _is_reversal(r: dict) -> bool:
    """A row is a reversal if the ledger says so by type, state, or payout status — any one."""
    return ((r.get("attribution_type") or "").lower() == "loss_reversal"
            or "reversal" in (r.get("attribution_state") or "").lower()
            or (r.get("current_status") or "").upper() == "REVERSED")


def _shape_captain_loss(r: dict) -> dict:
    """One attribution row → the flat shape the panel renders. Keeps the raw payout status
    AND a plain lifecycle word so the UI can colour it without re-deriving meaning."""
    status = (r.get("current_status") or "").upper()
    return {
        "awb": r.get("awb"),
        "loss_type": r.get("loss_type"),
        "disposition": _LT_TO_DISP.get((r.get("loss_type") or "").lower(), "others"),
        "amount_inr": r.get("attribution_amount"),
        "status": status,                                          # raw vetan payout status
        "lifecycle": _STATUS_LABEL.get(status, status.lower() or "unknown"),
        "attribution_state": r.get("attribution_state") or "",     # the LMS field the API omits
        "is_reversal": _is_reversal(r),
        "cn_number": r.get("cn_number") or "",
        "dn_number": r.get("dn_number") or "",
        "reason": (r.get("metadata_reason") or "").strip(),
        "attribution_date": r.get("attribution_date") or "",
        "payout_start_date": r.get("payout_start_date") or "",
        "counted": bool(r.get("is_included_in_calculation")),
    }


def get_captain_losses(partner_id: str, limit: int = 500) -> list[dict]:
    """Every loss-attribution row for a captain (newest first), shaped for the panel."""
    if not available() or not partner_id or not _has_captain_ledger():
        return []
    rows = _query(
        "SELECT awb, loss_type, attribution_type, attribution_state, current_status,"
        " attribution_amount, attribution_date, payout_start_date, cn_number, dn_number,"
        " metadata_reason, is_included_in_calculation"
        " FROM attribution WHERE partner_id = ?"
        " ORDER BY attribution_date DESC, awb LIMIT ?",
        (str(partner_id).strip(), int(limit)))
    return [_shape_captain_loss(r) for r in rows]


def _amt(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _i(v) -> int:
    """Coerce a COUNT/SUM cell to int. REQUIRED, not defensive: the Turso (Hrana) wire format
    encodes every SQLite INTEGER as a JSON *string*, and turso_http._cell() passes the value
    through verbatim. So in 'remote' mode — i.e. on Render — aggregates arrive as "0"/"12",
    and a non-empty string is TRUTHY: `if r["reversals"]` is true even for "0", and sorting on
    a string count orders "9" above "30". Both bugs are invisible locally (sqlite3 returns real
    ints) and only appear in the deployed demo. Mirrors _amt() above."""
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def get_captain_summary(partner_id: str) -> dict:
    """Aggregate a captain's ledger: counts + rupee lifecycle (debited / recovered / pending /
    reversed) + a disposition breakdown. The money split is honest — recovered is money already
    taken, pending is still-open exposure — rather than one conflated 'exposure' number."""
    losses = get_captain_losses(partner_id, limit=100000)
    summ = {"partner_id": str(partner_id), "count": len(losses),
            "total_inr": 0, "recovered_inr": 0, "pending_inr": 0, "reversed_inr": 0,
            "reversals": 0, "by_status": {}, "by_disposition": {}}
    if not losses:
        return summ
    total = recovered = pending = reversed_ = 0.0
    for l in losses:
        a = _amt(l["amount_inr"])
        total += a
        st = l["status"]
        summ["by_status"][st] = summ["by_status"].get(st, 0) + 1
        summ["by_disposition"][l["disposition"]] = summ["by_disposition"].get(l["disposition"], 0) + 1
        if l["is_reversal"] or st == "REVERSED":
            reversed_ += a
            summ["reversals"] += 1
        elif st == "SUCCEEDED":
            recovered += a
        elif st == "PENDING":
            pending += a
    summ.update(total_inr=round(total), recovered_inr=round(recovered),
                pending_inr=round(pending), reversed_inr=round(reversed_))
    return summ


def demo_captains(limit: int = 8) -> list[dict]:
    """A real set of captains from the ledger, ranked to surface good demo material — a reversal
    (the hero flow) first, then a mixed pending+recovered lifecycle, then more rows. Curated by
    QUERY, not by hand: every id and number is real, so any of them holds up under scrutiny."""
    if not available() or not _has_captain_ledger():
        return []
    rows = _query(
        "SELECT partner_id AS partner_id, COUNT(*) AS n,"
        " ROUND(SUM(attribution_amount)) AS total_inr,"
        " SUM(CASE WHEN current_status='PENDING'   THEN 1 ELSE 0 END) AS pending,"
        " SUM(CASE WHEN current_status='SUCCEEDED' THEN 1 ELSE 0 END) AS recovered,"
        " SUM(CASE WHEN current_status='REVERSED' OR attribution_type='loss_reversal'"
        "          OR attribution_state='loss_reversal_req_sent' THEN 1 ELSE 0 END) AS reversals"
        " FROM attribution WHERE partner_id IS NOT NULL AND partner_id != ''"
        " GROUP BY partner_id HAVING n BETWEEN 5 AND 30",
        ())

    def score(r: dict):
        mixed = 1 if (_i(r.get("pending")) and _i(r.get("recovered"))) else 0
        return (1 if _i(r.get("reversals")) else 0, mixed, _i(r.get("n")))

    rows.sort(key=score, reverse=True)
    return [{"partner_id": r["partner_id"], "count": _i(r["n"]), "total_inr": _amt(r["total_inr"]),
             "pending": _i(r["pending"]), "recovered": _i(r["recovered"]),
             "reversals": _i(r["reversals"])}
            for r in rows[:limit]]
