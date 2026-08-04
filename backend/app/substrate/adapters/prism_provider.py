"""PrismProvider — the SANCTIONED production data layer (Meesho PrismSDK / data lake).

PrismSDK is a **Java** client (`com.meesho:prismsdk`). This backend is Python, so there is no
in-process route to it. The supported shape is therefore:

    PSP (Python)  --HTTP-->  prism-sidecar (Java, wraps PrismSDK)  -->  Meesho data lake
                              backend/prism-sidecar/

This module is the Python half. It keeps the platform's core principle intact: **the LLM never
writes a query.** It selects a NAMED query from the whitelist below; we translate that name into
PrismSDK's structured fetch parameters (table + columns + filter + date window + limit). There is
no SQL string anywhere in the request path, so a prompt injection cannot reach the data lake.

PrismSDK's own contract (from the User Guide) is mirrored here so the sidecar stays a thin shim:
  • `gold.*` tables are unpartitioned → start/end date may be null.
  • `silver.*` tables are day-partitioned → start AND end date are REQUIRED, and the window may
    not exceed ~5 hours by default. `_window()` enforces this before we ever call out.
  • Results arrive as an iterator batched at ~45–50k rows; the sidecar drains it and returns JSON,
    so every named query MUST carry a `limit` (we cap defensively too).
  • Errors: 401 = token unset/unauthorised, 409 = unknown table/column, 500 = escalate to the
    data team. `PrismError` carries the code so callers can tell "misconfigured" from "broken".

SAFETY POSTURE (learned the hard way): this provider NEVER silently falls back to canned data.
A misconfigured or failing data lake raises. Fake data wearing a real provenance label is worse
than an outage — the caller must know it did not get grounded data.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests

# ── environment (values are NEVER logged) ─────────────────────────────────────
_SIDECAR_URL = "PRISM_SIDECAR_URL"          # e.g. http://localhost:8099 (sidecar container)
_ENVIRONMENT = "PRISMSDK_ENVIRONMENT"       # PRODUCTION | SANDBOX (the sidecar reads this too)
_TIMEOUT_ENV = "PRISM_TIMEOUT_SECONDS"

_DEFAULT_TIMEOUT = 60
_MAX_LIMIT = 5000                           # hard cap: PSP is an interactive resolver, not an ETL
_SILVER_MAX_WINDOW_HOURS = 5                # PrismSDK's documented partitioned-table window limit


class PrismError(RuntimeError):
    """A PrismSDK/sidecar failure. `code` mirrors the SDK's EngineException codes."""

    def __init__(self, message: str, code: str = "", query: str = ""):
        super().__init__(message)
        self.code = str(code or "")
        self.query = query

    @property
    def is_config_error(self) -> bool:
        """401 (no/invalid client_token) or a missing sidecar — an access problem, not a bug."""
        return self.code in ("401", "no_sidecar")


@dataclass(frozen=True)
class PrismQuery:
    """One whitelisted named query, expressed in PrismSDK's structured fetch vocabulary.

    `filter_template` is a PrismSDK filter predicate with {placeholders} substituted from
    caller params. Values are validated + quoted by `_literal()` — never interpolated raw.
    """
    table: str
    columns: list[str]
    description: str
    filter_template: str = ""
    params: tuple[str, ...] = ()                 # required param names
    sort: dict[str, str] = field(default_factory=dict)   # column -> ASCENDING|DESCENDING
    limit: int = 200
    lookback_days: int | None = None             # for partitioned (silver) tables

    @property
    def partitioned(self) -> bool:
        return self.table.startswith("silver.")


# ── THE WHITELIST ────────────────────────────────────────────────────────────
# Each entry is the production counterpart of a Captain-Context accessor / SOP evidence need.
# `gold.valmo_lost_awb_2k24_v1` and its columns are CONFIRMED (the loss export this platform
# already loads; see scripts/build_valmo_db.py). Entries marked TABLE-TBD carry the intended
# shape but need the real table/column names from the data team before they are enabled —
# `enabled_queries()` hides them so an unverified query can never run and silently return junk.
_LOSS_COLUMNS = [
    "awb", "consolidation_awb", "created_date", "lost_date", "actual_lost_date",
    "current_movement_type", "shipment_value", "loss_percentage", "loss_value",
    "location", "leg", "loc2", "leg2", "reason", "reason_l1",
    "attribution_changed", "facility_inscan", "DC_Tenurity",
]

PRISM_QUERIES: dict[str, PrismQuery] = {
    # ── CONFIRMED ──
    "get_loss_attribution": PrismQuery(
        table="gold.valmo_lost_awb_2k24_v1",
        columns=_LOSS_COLUMNS,
        description="Losses/debits marked against a captain's hub — loss type, attribution, reason_l1.",
        filter_template="location = {hub_code}",
        params=("hub_code",),
        sort={"lost_date": "DESCENDING"},
        limit=200,
    ),
    "get_loss_for_awb": PrismQuery(
        table="gold.valmo_lost_awb_2k24_v1",
        columns=_LOSS_COLUMNS,
        description="The loss record for ONE AWB — the reversal-decision evidence (facility_inscan, "
                    "attribution_changed, loss_percentage).",
        filter_template="awb = {awb}",
        params=("awb",),
        limit=5,
    ),
    # ── TABLE-TBD: shape is right, real table/columns pending the data team ──
    "get_captain_profile": PrismQuery(
        table="gold.TBD_captain_profile",
        columns=["captain_id", "name", "hub_code", "hub_name", "tier", "language", "status"],
        description="Captain/partner profile — hub, tier, language.",
        filter_template="captain_id = {captain_id}",
        params=("captain_id",),
        limit=1,
    ),
    "get_payments_ledger": PrismQuery(
        table="gold.TBD_captain_payments",
        columns=["captain_id", "txn_id", "type", "amount_inr", "txn_date", "narration", "payment_cycle"],
        description="Captain debits/credits — payout and deduction history.",
        filter_template="captain_id = {captain_id}",
        params=("captain_id",),
        sort={"txn_date": "DESCENDING"},
        limit=100,
    ),
    "get_cod_pendency": PrismQuery(
        table="gold.TBD_cod_pendency",
        columns=["captain_id", "hub_code", "cod_pendency_inr", "last_deposit_date", "last_deposit_inr",
                 "cms_partner"],
        description="COD pendency + latest CMS deposit.",
        filter_template="captain_id = {captain_id}",
        params=("captain_id",),
        limit=1,
    ),
    "get_shipments_open": PrismQuery(
        table="gold.TBD_shipment_status",
        columns=["awb", "captain_id", "hub_code", "status", "current_movement_type",
                 "latest_scan_date", "promise_date", "misroute_type"],
        description="Open shipments + manifest path for a captain.",
        filter_template="captain_id = {captain_id}",
        params=("captain_id",),
        sort={"latest_scan_date": "DESCENDING"},
        limit=200,
    ),
    "get_shipment_scan_history": PrismQuery(
        table="silver.TBD_shipment_scan_events",
        columns=["awb", "scan_type", "scan_time", "node", "hub_code"],
        description="Full scan trail for one AWB (day-partitioned events table).",
        filter_template="awb = {awb}",
        params=("awb",),
        sort={"scan_time": "ASCENDING"},
        limit=500,
        lookback_days=30,
    ),
}

_TBD = "TBD_"


def enabled_queries() -> dict[str, PrismQuery]:
    """Only queries whose table is CONFIRMED. A TABLE-TBD entry documents intent but must never
    execute — a wrong table silently returns nothing (or the wrong rows), which is exactly the
    failure mode we refuse to ship."""
    return {k: q for k, q in PRISM_QUERIES.items() if _TBD not in q.table}


# ── value handling: params are validated + quoted, never raw-interpolated ────
_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./: ")


def _literal(value) -> str:
    """Render a param as a safe PrismSDK filter literal. Rejects anything that could break out
    of the predicate (quotes, semicolons, comment markers). Numbers pass through bare."""
    if isinstance(value, bool):
        raise ValueError("boolean params are not supported in filters")
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value).strip()
    if not s:
        raise ValueError("empty parameter value")
    if len(s) > 128:
        raise ValueError("parameter value too long")
    bad = set(s) - _ALLOWED
    if bad:
        raise ValueError(f"parameter contains disallowed characters: {''.join(sorted(bad))!r}")
    return "'" + s + "'"


def _window(q: PrismQuery) -> tuple[str | None, str | None]:
    """Date window for the fetch. Partitioned (silver) tables REQUIRE one and PrismSDK caps the
    span (~5h default), so we clamp to the cap and let the caller page if it needs more."""
    if not q.partitioned and q.lookback_days is None:
        return None, None
    hours = min(_SILVER_MAX_WINDOW_HOURS, (q.lookback_days or 0) * 24) or _SILVER_MAX_WINDOW_HOURS
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    iso = "%Y-%m-%dT%H:%M:%S.000+00:00"
    return start.strftime(iso), end.strftime(iso)


def build_request(name: str, params: dict) -> dict:
    """Translate a NAMED query + params into the sidecar's PrismSDK fetch payload.

    Pure and side-effect free, so the whole translation layer is unit-testable without a token,
    a sidecar, or the data lake."""
    q = enabled_queries().get(name)
    if q is None:
        if name in PRISM_QUERIES:
            raise PrismError(
                f"Query '{name}' is declared but not enabled: table '{PRISM_QUERIES[name].table}' is "
                "still a placeholder. Confirm the real table/columns with the data team first.",
                code="not_enabled", query=name)
        raise PrismError(
            f"Unknown query '{name}'. Register it in PRISM_QUERIES (the LLM may only select a "
            "whitelisted query — it never writes one).", code="unknown_query", query=name)

    missing = [p for p in q.params if not str((params or {}).get(p, "")).strip()]
    if missing:
        raise PrismError(f"Query '{name}' needs param(s): {', '.join(missing)}",
                         code="bad_params", query=name)

    filt = q.filter_template
    for p in q.params:
        filt = filt.replace("{" + p + "}", _literal(params[p]))
    start, end = _window(q)
    return {
        "table": q.table,
        "columns": list(q.columns),
        # PrismSDK documents "filter can not be null" for fetchData; send a always-true predicate
        # rather than null for the (rare) unfiltered case.
        "filter": filt or "1 = 1",
        "startDate": start,
        "endDate": end,
        "sortOrder": dict(q.sort),
        "limit": min(int(q.limit), _MAX_LIMIT),
        "fetchType": "REST",     # REST returns JSON rows; JDBC is for bulk ETL, which PSP is not
        "queryName": name,       # echoed in sidecar logs for traceability
    }


class PrismProvider:
    """DataProvider over the Meesho data lake via the Java PrismSDK sidecar.

    Implements the same accessor contract as DemoDataProvider (see adapters/experimental/base.py)
    so `captain_context` swaps providers without any pipeline change.
    """

    source = "prism (Meesho data lake)"
    accepts_hub_code = True     # get_losses keys on hub (the real loss table is hub-keyed)

    def __init__(self, base_url: str | None = None, timeout: int | None = None):
        self.base = (base_url or os.environ.get(_SIDECAR_URL, "")).rstrip("/")
        try:
            self.timeout = int(timeout or os.environ.get(_TIMEOUT_ENV, _DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            self.timeout = _DEFAULT_TIMEOUT
        self.environment = os.environ.get(_ENVIRONMENT, "SANDBOX")

    # ── configuration / health ──
    def configured(self) -> bool:
        return bool(self.base)

    def status(self) -> dict:
        """Non-throwing health probe for /api/health. Never returns credentials."""
        st = {"provider": "prism", "environment": self.environment,
              "sidecar_configured": self.configured(),
              "enabled_queries": sorted(enabled_queries()),
              "pending_queries": sorted(k for k in PRISM_QUERIES if k not in enabled_queries())}
        if not self.configured():
            st.update(ok=False, detail=f"{_SIDECAR_URL} not set — sidecar unreachable")
            return st
        try:
            r = requests.get(f"{self.base}/health", timeout=min(self.timeout, 10))
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            st.update(ok=r.status_code == 200, detail=str(body.get("detail", ""))[:200],
                      token_present=bool(body.get("token_present")))
        except requests.RequestException as e:
            st.update(ok=False, detail=f"sidecar unreachable: {type(e).__name__}")
        return st

    # ── the one query interface ──
    def query(self, name: str, params: dict | None = None) -> list[dict]:
        """Run a whitelisted named query. Raises PrismError — never returns canned data."""
        payload = build_request(name, params or {})
        if not self.configured():
            raise PrismError(
                f"{_SIDECAR_URL} is not set, so the data lake is unreachable. Start the "
                "prism-sidecar (backend/prism-sidecar) or keep PSP_DATA_PROVIDER=demo.",
                code="no_sidecar", query=name)
        try:
            r = requests.post(f"{self.base}/fetch", json=payload, timeout=self.timeout)
        except requests.RequestException as e:
            raise PrismError(f"sidecar request failed: {type(e).__name__}", code="transport",
                             query=name) from e
        if r.status_code != 200:
            detail = ""
            try:
                detail = str(r.json().get("message", ""))[:300]
            except ValueError:
                detail = (r.text or "")[:300]
            raise PrismError(f"PrismSDK error {r.status_code}: {detail}", code=str(r.status_code),
                             query=name)
        try:
            body = r.json()
        except ValueError as e:
            raise PrismError("sidecar returned non-JSON", code="bad_response", query=name) from e
        rows = body.get("rows")
        if not isinstance(rows, list):
            raise PrismError("sidecar response missing 'rows'", code="bad_response", query=name)
        return rows

    # ── Captain-Context accessors (same contract as DemoDataProvider) ──
    def get_profile(self, captain_id: str) -> dict:
        rows = self.query("get_captain_profile", {"captain_id": captain_id})
        return rows[0] if rows else {}

    def get_ledger(self, captain_id: str) -> list[dict]:
        return self.query("get_payments_ledger", {"captain_id": captain_id})

    def get_losses(self, captain_id: str, hub_code: str = "") -> list[dict]:
        return self.query("get_loss_attribution", {"hub_code": hub_code or captain_id})

    def get_cash(self, captain_id: str) -> dict:
        rows = self.query("get_cod_pendency", {"captain_id": captain_id})
        return rows[0] if rows else {}

    def get_shipments(self, captain_id: str) -> list[dict]:
        return self.query("get_shipments_open", {"captain_id": captain_id})

    def get_scans(self, captain_id: str, awb: str) -> dict | None:
        rows = self.query("get_shipment_scan_history", {"awb": awb})
        if not rows:
            return None
        return {"awb": awb, "events": [{"scan": r.get("scan_type"), "at": r.get("scan_time"),
                                        "node": r.get("node")} for r in rows]}

    def get_loss_for_awb(self, awb: str) -> dict | None:
        """Single-AWB loss record — the reversal-decision evidence, and the join the Kapture
        audit engine needs to verify what an agent told a partner."""
        rows = self.query("get_loss_for_awb", {"awb": awb})
        return rows[0] if rows else None

    def known_captains(self) -> list[str]:
        return []   # the lake is not enumerable by design; captains arrive from the conversation
