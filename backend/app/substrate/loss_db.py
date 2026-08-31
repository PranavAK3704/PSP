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
import threading
from pathlib import Path

_DATA = Path(__file__).resolve().parents[2] / "data"
_DB = _DATA / "valmo.db"                      # full local file (dev)
_DB_FALLBACK = _DATA / "valmo_fallback.db"    # small subset baked into the image (offline safety net)

# Guards _init only. Per-query work uses a per-thread connection instead (see _conn).
_init_lock = threading.Lock()

# Data source: EXTERNAL Turso (libSQL over HTTPS) if TURSO_DATABASE_URL + TURSO_AUTH_TOKEN are
# set, else the LOCAL baked SQLite file. Same SQL runs on both (libSQL is SQLite-compatible).
# The remote path is fully guarded — any failure falls back to the local file, so the working
# local demo can never break.
_MODE = None                 # 'remote' | 'local' | 'none'
_url = _tok = None
_db_path: Path | None = None
_tables: set | None = None

# ── ONE CONNECTION PER THREAD ────────────────────────────────────────────────────────────────
# This used to be a single module-level `sqlite3.connect(..., check_same_thread=False)` shared
# by every caller. That flag only disables Python's same-thread ASSERTION — it does not make a
# connection safe for concurrent use, and the sqlite3 docs are explicit about it. Two threads
# calling `.execute()` on one connection interleave on the same statement handle, which raises
# `sqlite3.InterfaceError: bad parameter or other API misuse` or `IndexError: tuple index out of
# range` from inside the row factory.
#
# That is reachable in normal operation, not in theory: `/api/chat` returns an
# `EventSourceResponse` over a sync generator, so sse_starlette pulls each step through
# `iterate_in_threadpool` — different threads, and two captains talking at once is the default
# case for a support tool. It surfaced the moment a determinism test ran 48 turns in parallel.
#
# Thread-local rather than a global lock: the file is opened READ-ONLY, so N readers are safe
# and correct, and serialising every lookup behind one mutex would make a concurrent demo feel
# slower for no correctness gain.
_tls = threading.local()


def _conn() -> sqlite3.Connection | None:
    """This thread's read-only connection, opened on first use."""
    if _db_path is None:
        return None
    c = getattr(_tls, "conn", None)
    if c is None:
        c = sqlite3.connect(f"file:{_db_path}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        _tls.conn = c
    return c


def _init():
    global _MODE, _url, _tok, _db_path
    with _init_lock:
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
                _db_path = path
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
        c = _conn()
        if c is None:
            return []
        return [dict(r) for r in c.execute(sql, params).fetchall()]
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


#: `losses.reason` → a refined reason_l1, for the sub-states that are DECIDABLE.
#:
#: `reason_l1` collapses 258,656 rows into the single value `shipment_shortage`, which policies.py
#: escalates unconditionally. `reason` is 100% filled and already on the row — it says which
#: shortage this is, and four of the sub-states do not need a human at all:
#:
#:     shortage - evidence not received        84,557   tell them WHAT to send, and by when
#:     shortage - evidence invalid             75,463   tell them WHY theirs failed
#:     shortage - evidence valid from both     23,839   both sides agree — this should reverse
#:     shortage_validation_delay               45,670   OUR delay, not their fault
#:     shortage_validation incorrect agent      7,654   OUR agent got it wrong
#:     shortage pending for attribution       123,477   genuinely not adjudicated yet
#:
#: The last one matters as much as the others: "still being assessed" is a true answer and a
#: different one from "escalated to L2", and a captain told the truth about a pending case does
#: not need to be handed to a human to hear it.
#:
#: The project owner's own data-discovery table records that the live LMS endpoint
#: (/v1/shipments/loss-details) does NOT expose this verdict. So this is not work that a future
#: integration makes redundant — reading the local column is the only way to get it, now or later.
_SHORTAGE_SUBSTATE = {
    "shortage - evidence not received":        "shortage_evidence_missing",
    "shortage - evidence invalid":             "shortage_evidence_invalid",
    "shortage - evidence valid from both party": "shortage_evidence_upheld",
    "shortage_validation_delay":               "shortage_our_delay",
    "shortage_validation incorrect agent":     "shortage_our_error",
    "shortage pending for attribution":        "shortage_pending",
    "post_24hrs shortage marked":              "shortage_marked_late",
}


def _refine_shortage(row: dict) -> dict:
    """Replace a blanket shortage reason_l1 with its decidable sub-state, where one exists.

    Deliberately narrow: it fires ONLY when reason_l1 is already one of the shortage buckets, so
    it can never relabel a hardstop or an RVP pickup. An unrecognised `reason` leaves the row
    exactly as it was — the map is an allow-list, not a parser.
    """
    l1 = (row.get("reason_l1") or "").strip().lower()
    if l1 not in ("shipment_shortage", "bag_shortage"):
        return row
    refined = _SHORTAGE_SUBSTATE.get((row.get("reason") or "").strip().lower())
    if not refined:
        return row
    row["_shortage_substate"] = refined
    row["_shortage_reason_raw"] = (row.get("reason") or "").strip()
    row["reason_l1"] = refined
    return row


def get_loss_by_awb(awb: str) -> dict | None:
    """Consolidated loss row for an AWB, in priority order: `losses` (full export, has
    facility_inscan/reason_l1) → `loss_attrib` → the `attribution` ledger (debit/reversal state).
    None if absent everywhere."""
    if not available() or not awb:
        return None
    awb = awb.strip().upper()
    rows = _rows("losses", awb)
    if rows:
        out = _consolidate(rows, "facility_inscan"); out["_src"] = "losses"
        return _refine_shortage(out)
    rows = _rows("loss_attrib", awb)
    if rows:
        out = _consolidate(rows, "lm_facility_inscan"); out["_src"] = "loss_attrib"
        return _refine_shortage(out)
    rows = _rows("attribution", awb)
    if rows:
        return _normalize_attribution(rows)
    rows = _rows("qc_fail", awb)
    if rows:
        return _normalize_qc(rows)
    return None


def _normalize_qc(rows: list[dict]) -> dict:
    """Map a secondary-QC-fail evidence row into the loss-row shape.

    ── THE ADJUDICATED VERDICT IS IN THIS TABLE AND WAS BEING IGNORED ────────────────────────
    `qc_fail` carries 53 columns. This function read seven, and the two it skipped are the ones
    that decide the case:

        debitable_entity_role   100% filled. 'No Debit' on 214,078 of 252,187 rows (84.9%).
        debit_reason            100% filled. 'No defect found' (147,141) and 'Box item - no
                                debit' (66,937) are the two most common values in the dataset.

    Meanwhile `reason_l1` — the only reason field this function DID read — is empty on 216,232
    rows (85.7%), so it fell through to the literal string "secondary_qc_fail", which
    `policies.py` maps to an unconditional escalation to Quality / QC (L2).

    The result was the worst kind of wrong. **The single most common outcome in the QC dataset is
    an EXONERATION, and PSP was escalating it as an open dispute** — and because `loss_value` was
    set to `price` regardless of the verdict, on 31,907 of those cleared rows it would ALSO have
    told the captain about a rupee debit the ledger says does not exist. ₹15,024,687 of debits
    that were already dismissed.

    Two string comparisons fix it. A row the ledger has cleared now carries `qc_no_debit`, a
    `loss_value` of 0, and the adjudicator's own words for why — which turns the largest
    escalation bucket in the corpus into an answer given in the conversation.
    """
    row = rows[-1]
    inscan = (row.get("rto_shipment_inscan_at_lm") or "").strip()
    inscan = inscan[:10] if inscan[:1].isdigit() else ""     # a real date, not 'null'/blank
    qc_pass = "pass" in ((row.get("sec_qc_lm_status") or "") + (row.get("sec_qc_fm_status") or "")).lower()
    # The adjudication. `role` names who (if anyone) is debitable; `verdict` is why.
    role = (row.get("debitable_entity_role") or "").strip()
    verdict = (row.get("debit_reason") or "").strip()
    no_debit = role.lower() == "no debit"
    return {
        "awb": row.get("awb"),
        # `qc_no_debit` when the ledger has cleared it — a DIFFERENT disposition, not a softer
        # version of the same one, because the action it deserves is opposite.
        "reason_l1": ("qc_no_debit" if no_debit
                      else (row.get("reason_l1") or "").strip() or "secondary_qc_fail"),
        # ZERO when nobody is being debited. Reporting `price` here is how PSP quoted amounts
        # that had already been dismissed.
        "loss_value": 0 if no_debit else row.get("price"),
        "loss_percentage": "0%" if no_debit else "100%",
        "facility_inscan": inscan, "attribution_changed": "no",
        "cn_flag": "no", "cn_number": "",
        "current_movement_type": "rto", "leg": "", "location": row.get("hub_location", ""),
        "row_count": len(rows), "_src": "qc_fail",
        "_qc_status": (row.get("sec_qc_lm_status") or row.get("sec_qc_fm_status") or ""),
        "_qc_pass": qc_pass,
        # The adjudicator's own words, so the reply can quote the reason rather than paraphrase a
        # code: "No defect found", "Box item - no debit", "ICSD vs FM image mismatch".
        "_qc_verdict": verdict,
        "_qc_debitable_role": role,
        "_qc_no_debit": no_debit,
    }


#: Cached row counts. Reset only by a process restart, which is correct: the loss dataset is a
#: read-only SNAPSHOT — nothing in this application inserts into `losses`, `attribution` or
#: `qc_fail` — so a count taken once is a count that stays true for the life of the container.
_COUNTS: dict[str, int | None] = {}


def row_count(table: str) -> int | None:
    """`COUNT(*)` for a table, computed ONCE per process.

    ── WHY THIS EXISTS, and it is not micro-optimisation ─────────────────────────────────────
    `/api/health` ran `SELECT COUNT(*) FROM losses` on every request. On SQLite that is a full
    scan of 1,000,001 rows; against Turso it is 1,000,001 metered row reads.

    `render.yaml` sets `healthCheckPath: /api/health`, so the platform polls it continuously. At
    one poll every 30 seconds that is ~2.88 BILLION row reads per day, which exhausts a
    1-billion-per-month free quota in about eight hours — and it did: reads are currently
    returning `BLOCKED`. The frontend also calls `getHealth()` on every shell mount, so each page
    load added another million.

    One scan per container instead of one per poll. Table name is validated against a fixed set
    rather than interpolated freely, because this is the one place a table name reaches SQL as a
    string.
    """
    if table not in ("losses", "attribution", "qc_fail", "captain_summary"):
        raise ValueError(f"row_count: unknown table {table!r}")
    if table in _COUNTS:
        return _COUNTS[table]
    try:
        _COUNTS[table] = _i(_query(f"SELECT COUNT(*) AS n FROM {table}", ())[0]["n"])
    except Exception:  # noqa: BLE001 — health must never throw
        _COUNTS[table] = None
    return _COUNTS[table]


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

# ─────────────────────────────────────────────────────────────────────────────
# Cell coercion for the Turso (Hrana) wire format.
# ─────────────────────────────────────────────────────────────────────────────

def _i(v) -> int:
    """Coerce a COUNT/SUM cell to int. REQUIRED, not defensive: the Turso (Hrana) wire format
    encodes every SQLite INTEGER as a JSON *string*, and turso_http._cell() passes the value
    through verbatim. So in 'remote' mode — i.e. on Render — a raw count reads as "1000001"
    rather than 1000001, and a non-empty string is TRUTHY, so `if row["n"]` is true even for
    "0". Both bugs are invisible locally, because sqlite3 returns real ints, and appear only on
    the deployed instance."""
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def _amt(v) -> float:
    """Money cell → float. Same Hrana reason as _i(): attribution_amount arrives as "244.0"."""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _has(table: str) -> bool:
    return table in _all_tables()


# ─────────────────────────────────────────────────────────────────────────────
# PARTNER-KEYED readers — the engine's captain context, from real data.
#
# Everything above is AWB-keyed ("is THIS debit wrong?"). These are the inverse: what does
# THIS captain carry. They exist for LocalDbProvider (adapters/local_db_provider.py), which
# replaces the three fictional SEED captains in the resolution engine — so unlike the panel
# readers deleted in 52d0001, these have a caller on the money path.
#
# `attribution` is the only partner-keyed table (`losses` has no partner column at all, just
# awb + location), and idx_attribution_partner_id exists, so these are index-served rather
# than full scans over a million rows.
#
# WHAT THIS DATA DOES NOT CONTAIN, and is therefore never invented here: captain NAME, TIER,
# LANGUAGE, COD pendency, and shipment/scan state. The seed provider had all five because a
# human wrote them. Returning a plausible name for a real partner_id would be a fabrication
# attached to a real identity, which is worse than a missing field — so those keys are simply
# absent and callers already treat them as optional.
# ─────────────────────────────────────────────────────────────────────────────

# attribution.loss_type → the reason_l1 vocabulary the SOPs and policies are written against.
_LT_TO_REASON = {"facility": "hardstop", "shipment shortage": "shipment_shortage",
                 "bag shortage": "bag_shortage", "in transit": "intransit"}


def _is_reversal_row(r: dict) -> bool:
    """A row is a reversal if the ledger says so by type, state, or payout status — any one of
    the three, because the export is inconsistent about which it populates."""
    return ((r.get("attribution_type") or "").lower() == "loss_reversal"
            or "reversal" in (r.get("attribution_state") or "").lower()
            or (r.get("current_status") or "").upper() == "REVERSED")


def partner_profile(partner_id: str) -> dict:
    """Identity we can actually evidence: the partner id, their hub, and their debit history
    window. Deliberately no name/tier/language — see the module note above."""
    if not available() or not partner_id or not _has("attribution"):
        return {}
    rows = _query(
        "SELECT entity_id, COUNT(*) AS n, MIN(attribution_date) AS first_seen,"
        " MAX(attribution_date) AS last_seen"
        " FROM attribution WHERE partner_id = ? AND entity_id IS NOT NULL AND entity_id != ''"
        " GROUP BY entity_id ORDER BY n DESC LIMIT 1",
        (str(partner_id).strip(),))
    if not rows:
        return {}
    r = rows[0]
    hub = (r.get("entity_id") or "").strip()
    return {"captain_id": str(partner_id), "hub": hub, "hub_name": hub,
            "debits_on_record": _i(r.get("n")),
            "first_debit": r.get("first_seen") or "", "last_debit": r.get("last_seen") or "",
            "_source": "valmo.db attribution ledger"}


def partner_losses(partner_id: str, limit: int = 200) -> list[dict]:
    """The captain's loss/debit rows in the shape the engine's context expects."""
    if not available() or not partner_id or not _has("attribution"):
        return []
    rows = _query(
        "SELECT awb, loss_type, entity_id, attribution_amount, attribution_date,"
        " attribution_state, attribution_type, current_status, metadata_reason,"
        " metadata_attribution_marked_by, cn_number, dn_number"
        " FROM attribution WHERE partner_id = ?"
        " ORDER BY attribution_date DESC, awb LIMIT ?",
        (str(partner_id).strip(), int(limit)))
    out = []
    for r in rows:
        lt = (r.get("loss_type") or "").lower()
        out.append({
            "awb": r.get("awb") or "",
            "loss_type": _LT_TO_REASON.get(lt, lt or "others"),
            "attributed_node": r.get("entity_id") or "",
            "loss_date": r.get("attribution_date") or "",
            "reason_l1": (r.get("metadata_reason") or "").strip() or _LT_TO_REASON.get(lt, "others"),
            "amount_inr": _amt(r.get("attribution_amount")),
            "status": (r.get("current_status") or "").upper(),
            "attribution_state": r.get("attribution_state") or "",
            "is_reversal": _is_reversal_row(r),
            "marked_by": r.get("metadata_attribution_marked_by") or "",
            "cn_number": r.get("cn_number") or "", "dn_number": r.get("dn_number") or "",
        })
    return out


def partner_ledger(partner_id: str, limit: int = 100) -> list[dict]:
    """The loss rows as ledger entries. A reversal is a CREDIT back to the captain; an
    attributed loss is a DEBIT. There are no payout credits in this export, so the ledger is
    honestly debit-side-only rather than padded with invented payouts."""
    entries = []
    for l in partner_losses(partner_id, limit=limit):
        rev = l["is_reversal"]
        entries.append({
            "id": (l["cn_number"] or l["dn_number"] or l["awb"] or "")[:24],
            "type": "credit" if rev else "debit",
            "amount_inr": l["amount_inr"],
            "date": l["loss_date"],
            "reason": ("loss_reversal" if rev else l["loss_type"]),
            "awb": l["awb"],
            "status": l["status"].lower() or "posted",
            "narration": ("Loss reversed — credited back" if rev
                          else f"Loss debit — {l['loss_type'].replace('_', ' ')}"),
        })
    return entries


# ─────────────────────────────────────────────────────────────────────────────
# CAPTAIN SUMMARY — the aggregate the engine sends to the model instead of rows.
#
# `partner_losses` above is the ROW reader: it exists for the panel and the trace, both of
# which are local and auditable. It is NOT what goes to the model — it carries awb,
# entity_id, and metadata_attribution_marked_by (real Meesho employee first names), and a
# tool result lands in the conversation history permanently. See engine/dataplane.py.
#
# This is the projection the model gets: counts and totals, nothing quotable. It answers
# "what does this captain carry" — enough to decide what to DO — while carrying no value
# that identifies a person or another shipment.
#
# The lifecycle vocabulary comes from the real column, not from a guess:
#   SUCCEEDED  the debit was recovered from the captain      (7,509 rows)
#   PENDING    requested, not yet recovered — still open     (2,318)
#   FAILED     recovery attempt failed                       (167)
#   REVERSED   reversed back to the captain                  (6)
# ─────────────────────────────────────────────────────────────────────────────

# A reversal row is NOT a debit. It was being counted as both — COUNT(*) included it in
# `debits` while the reversal CASE also counted it in `reversals` — so a partner whose entire
# history had been reversed was reported to the model as "4 debits on record, ₹1,036 debited"
# with recovered+pending+failed all zero, a total that reconciled with nothing. 5 partners in
# the current ledger are affected. It also broke the invariant that the aggregate is identical
# whichever provider computes it: the Python path in tools.captain_aggregate types a reversal
# as a CREDIT and excludes it, so SQL and Python disagreed and SQL won.
_IS_REV = "(current_status = 'REVERSED' OR attribution_type = 'loss_reversal')"

_SUMMARY_SQL = f"""
SELECT SUM(CASE WHEN {_IS_REV} THEN 0 ELSE 1 END)                      AS debits,
       -- Every lifecycle bucket EXCLUDES reversal rows, not just the total. A reversal can
       -- carry current_status='SUCCEEDED' while attribution_type='loss_reversal' (the export
       -- populates the two inconsistently), so keying the buckets on status alone counted
       -- the reversed amount as recovered — which left total_debited ≠ recovered+pending+failed
       -- by exactly the reversed amount for 2 of the 5 affected partners.
       SUM(CASE WHEN current_status = 'PENDING'   AND NOT {_IS_REV} THEN 1 ELSE 0 END) AS open_n,
       SUM(CASE WHEN current_status = 'PENDING'   AND NOT {_IS_REV} THEN attribution_amount ELSE 0 END) AS pending_amt,
       SUM(CASE WHEN current_status = 'SUCCEEDED' AND NOT {_IS_REV} THEN attribution_amount ELSE 0 END) AS recovered_amt,
       SUM(CASE WHEN current_status = 'FAILED'    AND NOT {_IS_REV} THEN attribution_amount ELSE 0 END) AS failed_amt,
       SUM(CASE WHEN {_IS_REV} THEN 1 ELSE 0 END)                      AS reversals,
       SUM(CASE WHEN {_IS_REV} THEN attribution_amount ELSE 0 END)      AS reversed_amt,
       SUM(CASE WHEN {_IS_REV} THEN 0 ELSE attribution_amount END)      AS total_amt,
       MIN(attribution_date)                                           AS first_debit,
       MAX(attribution_date)                                           AS last_debit
  FROM attribution WHERE partner_id = ?
"""

# Which loss types, and how many of each — the model needs the MIX to route, never the rows.
_SUMMARY_MIX_SQL = ("SELECT loss_type, COUNT(*) AS n FROM attribution"
                    " WHERE partner_id = ? GROUP BY loss_type ORDER BY n DESC LIMIT 6")


def captain_summary(partner_id: str) -> dict:
    """Aggregates for one partner, computed in SQL. Empty dict if unknown/unavailable.

    Deliberately contains NO awb, NO partner_id, and NO marked_by. `hub` is included: a
    3-letter facility code is not a person, it is what routes an escalation to the owning
    team, and the Data Foundation panel already publishes hub codes on that reasoning.
    """
    if not available() or not partner_id or not _has("attribution"):
        return {}
    pid = str(partner_id).strip()
    rows = _query(_SUMMARY_SQL, (pid,))
    # `debits` is now 0 for a partner whose every row was reversed, so emptiness has to be
    # judged on whether the partner exists AT ALL, not on the debit count — otherwise a
    # fully-reversed captain reads as an unknown captain.
    if not rows or (not _i(rows[0].get("debits")) and not _i(rows[0].get("reversals"))):
        return {}
    r = rows[0]
    prof = partner_profile(pid)
    mix = {}
    for m in _query(_SUMMARY_MIX_SQL, (pid,)):
        key = _LT_TO_REASON.get((m.get("loss_type") or "").lower(),
                                (m.get("loss_type") or "other").lower() or "other")
        mix[key] = mix.get(key, 0) + _i(m.get("n"))
    return {
        "hub": prof.get("hub", ""),
        "debits_on_record": _i(r.get("debits")),
        "open_debits": _i(r.get("open_n")),
        "total_debited_inr": round(_amt(r.get("total_amt"))),
        "recovered_inr": round(_amt(r.get("recovered_amt"))),
        "pending_inr": round(_amt(r.get("pending_amt"))),
        "failed_inr": round(_amt(r.get("failed_amt"))),
        "reversals": _i(r.get("reversals")),
        "reversed_inr": round(_amt(r.get("reversed_amt"))),
        "loss_type_mix": mix,
        "first_debit": r.get("first_debit") or "",
        "last_debit": r.get("last_debit") or "",
        "source": source(),
    }


def read_captain_summary(partner_id: str) -> dict:
    """The engine's read path: the MATERIALISED `captain_summary` row if the build script
    has run, else computed live from `attribution`.

    Materialising matters on the remote path. Computing live is two aggregate queries; over
    Turso that is two HTTPS round-trips on every turn that touches captain context, and the
    query set is fixed, so the result can be computed once by
    scripts/build_captain_summary.py and read with a single indexed lookup. Falling back to
    the live computation rather than to {} means the table is an OPTIMISATION, never a
    dependency — a deploy that forgot to run the build script is slower, not broken.
    """
    if not available() or not partner_id:
        return {}
    pid = str(partner_id).strip()
    if _has("captain_summary"):
        try:
            rows = _query("SELECT * FROM captain_summary WHERE partner_id = ?", (pid,))
            if rows:
                r = dict(rows[0])
                out = {
                    "hub": r.get("hub") or "",
                    "debits_on_record": _i(r.get("debits_on_record")),
                    "open_debits": _i(r.get("open_debits")),
                    "total_debited_inr": round(_amt(r.get("total_debited_inr"))),
                    "recovered_inr": round(_amt(r.get("recovered_inr"))),
                    "pending_inr": round(_amt(r.get("pending_inr"))),
                    "failed_inr": round(_amt(r.get("failed_inr"))),
                    "reversals": _i(r.get("reversals")),
                    "reversed_inr": round(_amt(r.get("reversed_inr"))),
                    "loss_type_mix": _parse_mix(r.get("loss_type_mix")),
                    "first_debit": r.get("first_debit") or "",
                    "last_debit": r.get("last_debit") or "",
                    "source": source(), "materialised_at": r.get("computed_at") or "",
                }
                return out
        except Exception:  # noqa: BLE001 — a stale/malformed table must not break the turn
            pass
    return captain_summary(pid)


def _parse_mix(v) -> dict:
    """loss_type_mix is stored as JSON text (Turso stores everything as TEXT)."""
    import json
    if isinstance(v, dict):
        return v
    try:
        out = json.loads(v or "{}")
        return out if isinstance(out, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def known_partners(limit: int = 12) -> list[str]:
    """Real partner ids with enough history to be worth opening a conversation about."""
    if not available() or not _has("attribution"):
        return []
    rows = _query(
        "SELECT partner_id, COUNT(*) AS n FROM attribution"
        " WHERE partner_id IS NOT NULL AND partner_id != ''"
        " GROUP BY partner_id HAVING n BETWEEN 5 AND 40 ORDER BY n DESC LIMIT ?",
        (int(limit),))
    return [str(r["partner_id"]) for r in rows]


# ── the at-risk cohort ───────────────────────────────────────────────────────────────────────
# `attribution ⋈ losses ON awb` — shipments that ALREADY became losses, with the money, the
# dates and the reversal columns attached. Read by adapters/risk/derive.py.
#
# THESE LIVE HERE, and not in a new risk_db.py, for one reason: `_query`, `_conn`, `_i`, `_amt`
# and `_consolidate` are module-private, and the per-thread connection discipline documented at
# the top of this file is the single thing a new reader must not reimplement. A separate module
# would either duplicate it or reach through the underscore.

#: The projection both readers share. Every column is TEXT in this schema — even locally, where
#: `attribution_amount` arrives as '341.0' — so `_amt`/`_i` on the way out are mandatory rather
#: than defensive. Deliberately NOT `SELECT *`: an explicit list is what makes it obvious which
#: columns the ladder and the reversal signal actually depend on.
_AT_RISK_COLS = """a.awb, a.partner_id, a.entity_id, a.attribution_amount, a.current_status,
       a.attribution_date, a.attribution_type, a.attribution_state, a.cn_number, a.loss_type,
       l.created_date, l.lost_date, l.actual_lost_date, l.facility_inscan,
       l.current_movement_type, l.leg, l.reason_l1, l.loss_value, l.shipment_value,
       l.loss_percentage, l.location, l.attribution_changed"""


def partner_at_risk_rows(partner_id: str, limit: int = 400) -> list[dict]:
    """At-risk rows for one partner. INDEX-SERVED — the primary path.

    Measured plan: `SEARCH a USING INDEX idx_attribution_partner_id` + `SEARCH l USING INDEX
    idx_awb`, 0.3 ms for 26 rows. Prefer this over the hub-keyed reader wherever a partner id is
    already to hand — and `main._hub_to_partner` already hands us one.
    """
    if not available() or not _has("attribution") or not _has("losses"):
        return []
    return _query(
        f"SELECT {_AT_RISK_COLS}"
        "  FROM attribution a JOIN losses l ON l.awb = a.awb"
        " WHERE a.partner_id = ?"
        " ORDER BY l.lost_date DESC, a.awb LIMIT ?",
        (str(partner_id), int(limit)))


def hub_at_risk_rows(hub_code: str, limit: int = 400) -> list[dict]:
    """At-risk rows for one hub. UNINDEXED — a documented-cost secondary path.

    `attribution` carries indexes on awb, partner_id, attribution_date, attribution_amount,
    cn_number and invoice_id — but **not on entity_id**. So this is `SCAN a` over ~10,000
    attribution rows (~7 ms) against the partner path's 0.3 ms. `losses` is never scanned: the
    join is a rowid lookup per matched row through `idx_awb`, linear in the 26–34 matches rather
    than in the million.
    """
    if not available() or not _has("attribution") or not _has("losses"):
        return []
    return _query(
        f"SELECT {_AT_RISK_COLS}"
        "  FROM attribution a JOIN losses l ON l.awb = a.awb"
        " WHERE a.entity_id = ?"
        " ORDER BY l.lost_date DESC, a.awb LIMIT ?",
        (str(hub_code), int(limit)))


def at_risk_corpus_stats() -> dict:
    """Cohort shape, for the harness and the health panel. Never a captain-facing route."""
    if not available() or not _has("attribution") or not _has("losses"):
        return {"available": False}
    raw = _query("SELECT COUNT(*) AS n FROM attribution a JOIN losses l ON l.awb = a.awb", ())
    awbs = _query("SELECT COUNT(DISTINCT a.awb) AS n"
                  " FROM attribution a JOIN losses l ON l.awb = a.awb", ())
    hubs = _query("SELECT COUNT(DISTINCT a.entity_id) AS n"
                  " FROM attribution a JOIN losses l ON l.awb = a.awb", ())
    n_raw, n_awb = _i(raw[0]["n"] if raw else 0), _i(awbs[0]["n"] if awbs else 0)
    return {"available": n_raw > 0, "cohort_rows": n_raw, "cohort_awbs": n_awb,
            "consolidated_awbs": n_raw - n_awb,
            "cohort_hubs": _i(hubs[0]["n"] if hubs else 0)}


# ─────────────────────────────────────────────────────────────────────────────
# AGGREGATES — corpus-level only, for the Data Foundation panel. No partner is
# identifiable from any of this, which is what makes the panel safe to show.
# ─────────────────────────────────────────────────────────────────────────────

def corpus_stats() -> dict:
    """Shape of the loss corpus: row counts, disposition mix, and the money lifecycle."""
    st = {"source": source(), "available": available(), "tables": {}, "dispositions": {},
          "lifecycle": {}, "money": {}, "reversal_rate_pct": 0.0}
    if not available():
        return st
    for t in ("losses", "attribution", "qc_fail"):
        if _has(t):
            try:
                st["tables"][t] = _i(_query(f"SELECT COUNT(*) AS n FROM {t}", ())[0]["n"])
            except Exception:  # noqa: BLE001 — a panel must never take the app down
                st["tables"][t] = None
    if not _has("attribution"):
        return st
    try:
        for r in _query("SELECT loss_type, COUNT(*) AS n FROM attribution"
                        " GROUP BY loss_type ORDER BY n DESC LIMIT 12", ()):
            key = (r.get("loss_type") or "unknown").strip() or "unknown"
            st["dispositions"][key] = _i(r.get("n"))
        for r in _query("SELECT current_status, COUNT(*) AS n, SUM(attribution_amount) AS amt"
                        " FROM attribution GROUP BY current_status ORDER BY n DESC LIMIT 12", ()):
            key = (r.get("current_status") or "unknown").strip() or "unknown"
            st["lifecycle"][key] = _i(r.get("n"))
            st["money"][key] = round(_amt(r.get("amt")))
        rev = _i(_query("SELECT COUNT(*) AS n FROM attribution"
                        " WHERE attribution_type = 'loss_reversal'"
                        " OR current_status = 'REVERSED'", ())[0]["n"])
        tot = st["tables"].get("attribution") or 0
        st["reversal_rate_pct"] = round(100.0 * rev / tot, 2) if tot else 0.0
        st["reversals"] = rev
        st["partners"] = _i(_query("SELECT COUNT(DISTINCT partner_id) AS n FROM attribution"
                                  " WHERE partner_id IS NOT NULL AND partner_id != ''", ())[0]["n"])
        st["hubs"] = _i(_query("SELECT COUNT(DISTINCT entity_id) AS n FROM attribution"
                               " WHERE entity_id IS NOT NULL AND entity_id != ''", ())[0]["n"])
    except Exception as e:  # noqa: BLE001
        st["error"] = type(e).__name__
    return st
