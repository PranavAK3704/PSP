"""The connector registry — which real endpoints exist, and what PSP does with each today.

PURELY DECLARATIVE. Nothing here calls anything. It is a table, not plumbing.

WHY IT EXISTS
Every conversation about this platform reaches the same question: *what would it take to run on
real data?* Answering it in prose invites an argument about scope. Answering it with a list of
paths — each with its owning service, the adapter that would serve it, and whether that adapter
is on a fixture or live — turns it into a checklist. That is the access ask, stated as a table.

The paths are copied VERBATIM from the captain panel's own route constants
(`valmo-partner-webview :: src/modules/captain/common/api.constants.ts`), because that app is
already in production, already authenticated as the partner, and already calling them. So the
claim is not "these endpoints could exist" — it is "these endpoints are being called right now
by the app the captain has open."

`status` is deliberately narrow:
    live     PSP calls it today
    fixture  PSP reads a file shaped to its contract; one env var flips it
    none     no adapter written — named so the gap is visible rather than implied
"""
from __future__ import annotations

# ── the four route groups, verbatim ──────────────────────────────────────────────────────────
# Timeouts included because they are a real constraint the panel already lives with: a 1000ms
# budget on the growth endpoints means an adapter cannot afford a retry ladder there.
CAPTAIN_PAYOUTS_AUTO_API_ROUTES = [
    ("PAYMENTS_SUMMARY", "/v1/entity/payments/pending", 1600),
    ("PAYMENT_RECORDS", "/v1/entity/earnings/all", 1600),
    ("PAYMENT_RECORDS_TABLE_SUMMARY", "/v1/entity/earnings/aggregated-data", 1600),
    ("PAYMENT_DETAILS", "/v1/entity/payments/details", 1600),
    ("DOWNLOAD_FILE", "/v1/invoice-document/signed-url", 10000),
    ("DOCUMENTS_SUMMARY", "/v1/invoice-document/summary", 1600),
    ("LOSS_RECORDS", "/v1/shipments/loss-details", 1600),
    ("LOSS_DETAILS_FILTERS", "/v1/shipments/fetch-loss-details-filters", 1600),
    ("LOSSES_SUMMARY", "/v1/shipments/fetch-loss-aggregated-details", 1600),
]

CAPTAIN_API_ROUTES = [
    ("PAYMENTS_SUMMARY", "/v1/captain/captain-payments-summary", 1600),
    ("PAYMENT_RECORDS", "/v1/captain/captain-payments-records", 1600),
    ("PAYMENT_RECORDS_TABLE_SUMMARY", "/v1/captain/captain-payments-table-summary", 1600),
    ("PAYMENT_DETAILS", "/v1/captain/captain-payments-details", 1600),
    ("LOSS_SUMMARY", "/v1/captain/captain-losses-summary", 1600),
    ("LOSS_DETAILS", "/v1/captain/captain-losses-records", 1600),
    ("FETCH_SHIPMENT_SUMMARY", "/v1/shipment/summary", 1600),
    ("FETCH_SHIPMENT_DETAILS", "/v1/hubs/shipments", 1600),
    ("HUB_LIST", "/v1/captain/hubs", 1600),
    ("SERVICE_AREA_CLUSTER_HISTORY", "/v1/fetch/serviceable-cluster-version/history", 1600),
    ("SERVICE_AREA_MAP_LAYER_DATA", "/v2/fetch/service-type-configs", 1600),
    ("SERVICE_AREA_POLYGON_DATA", "/v1/fetch/serviceability-cluster", 1600),
    ("SERVICE_AREA_SUMMARY", "/v1/serviceable-area/summary", 1600),
    ("SERVICE_AREA_NEIGHBORING_DC_CUM_PINCODE_BOUNDARY", "/v1/fetch/serviceable-area/geo-near", 1600),
    ("SERVICE_AREA_UPDATE_METADATA", "/v1/node-metadata", 1600),
    ("FETCH_AWB_DETAILS", "/v1/valmo/misroute/awb-details", 1600),
    ("MISROUTE_SHIPMENT_SUMMARY", "/v1/misroute-shipment-summary", 100000),
    ("MISROUTE_SHIPMENT_DETAILS", "/v1/misroute-shipment-details", 5000),
    ("SHIPMENT_ADDRESS", "/api/v1/valmo/shipment/address-details/bulk", 1600),
    ("SHIPMENT_TRACKING_DETAILS", "/v1/shipment/tracking-details", 10000),
    ("CASH_PENDENCY_PAYMENT_METHODS", "/v1/cod-pendency/get-available-payment-methods", 10000),
    ("CASH_PENDENCY_RISK_CATEGORY", "/v1/cod-pendency/get-cod-pendency-buckets", 10000),
    ("CAPTAIN_SERVICE_AREA_CONSENT", "/v1/serviceable-area-consent", 1600),
    ("MISROUTE_SHIPMENT_DOWNLOAD", "/v1/misroute-shipment-download", 10000),
]

GROWTH_DASHBOARD_API_ROUTES = [
    ("YOUR_METRICS", "/v1/captain/growth-dashboard/:hubID/your-metrics", 1000),
    ("ORDER_SUMMARY", "/v1/captain/growth-dashboard/:hubID/order-summary", 1000),
]

DC_CAPACITY_API_ROUTES = [
    ("HUB_CAPACITY_DATA", "/v1/captain/hub-capacity/:hubID", 10000),
    ("DC_CAPACITY_UPDATE", "/v1/captain/dc-capacity/update", 10000),
]

# Endpoints outside the captain panel, from the service repos themselves. Included because two
# of PSP's queues depend on them and neither is reachable from the panel's route table.
OTHER_SERVICE_ROUTES = [
    ("TrackingService (log10)", "WAYBILL_TRACKING",
     "GET /tracking/consignment/v1/{waybillNo}", "log10", "fixture",
     "losses_debits", "Per-waybill scan timeline. The batch POST returns only 12 of 29 fields."),
    ("Nexus", "SHIPMENT_SCANS", "POST /v1/shipment/scans", "nexus", "none",
     "losses_debits", "One hop closer to source than LMS's own tracking-details."),
    ("Nexus", "TRACKING_LATEST", "POST /v1/shipment/tracking/latest", "nexus", "none",
     "losses_debits", "Latest scan only — cheaper than the full trail."),
    ("LossManagementService", "COD_PENDENCY_UPLOAD",
     "POST /api/v1/cod-pendency/file-upload", "lms", "none", "cash_cod",
     "A WRITE endpoint, but a CSV ingest — not a per-captain action."),
    ("LossManagementService", "LOSS_REVERSAL",
     "(none — Kafka LossAttributionMessage)", "lms", "none", "losses_debits",
     "THERE IS NO HTTP WRITE PATH. Reversal is a Kafka message consumed by LMS's scheduler "
     "cron (ADR-0015). This is why WRITE_MODE=live refuses."),
]

# ── which PSP queue each group serves, and which adapter would serve it ──────────────────────
# `adapter` names a module that exists (or would). `status` is about the ADAPTER, not the
# endpoint: the endpoints are all live in production; the question is what PSP does with them.
# MOVED out of CAPTAIN_API_ROUTES, not copied — check_connectors.py asserts there are no
# duplicate paths across the whole registry, so a copy trips it immediately. They earn their own
# group because they are the four an adapter now stands in for, with its own env var.
SHIPMENT_RISK_API_ROUTES = [
    ("SHIPMENT_RISK_SUMMARY", "/v1/shipments/risk-summary", 10000),
    ("SHIPMENT_RISK_CATEGORY_OVERVIEW", "/v1/shipments/risk-category-overview", 10000),
    ("SHIPMENT_RISK_DETAILS", "/v1/shipments/risk-details", 15000),
    ("SHIPMENT_RISK_DETAILS_FILTERS", "/v1/shipments/risk-details-filters", 10000),
]

_GROUPS = [
    {
        "group": "GROWTH_DASHBOARD_API_ROUTES",
        "queue": "Orders & Planning",
        "service": "captain-panel BFF → growth service",
        "adapter": "substrate/adapters/growth",
        "status": "fixture",
        "env": "PSP_GROWTH_SOURCE=live",
        "routes": GROWTH_DASHBOARD_API_ROUTES,
        "note": "Hub-keyed, so PSP needs no join — the profile already carries the hub. "
                "The two endpoints this build actually serves.",
    },
    {
        "group": "SHIPMENT_RISK_API_ROUTES",
        "queue": "Losses & Debits (prevention)",
        "service": "captain-service → shipment-risk",
        "adapter": "substrate/adapters/risk",
        "status": "fixture",
        "env": "PSP_RISK_SOURCE=live",
        "routes": SHIPMENT_RISK_API_ROUTES,
        "note": "The at-risk ladder the captain panel already shows — BREACHED / Extreme / High "
                "/ Moderate. PSP stands in for these by deriving the cohort from the real loss "
                "ledger (attribution JOIN losses), which is a HINDSIGHT reconstruction: every "
                "shipment in it already became a loss. Live would be the forward-looking queue.",
    },
    {
        "group": "CAPTAIN_PAYOUTS_AUTO_API_ROUTES",
        "queue": "Payments · Losses & Debits",
        "service": "payouts-auto",
        "adapter": "substrate/adapters/prism_provider (payments unwired)",
        "status": "none",
        "env": "PSP_DATA_PROVIDER=prism",
        "routes": CAPTAIN_PAYOUTS_AUTO_API_ROUTES,
        "note": "payment_not_received is ~26% of labelled tickets and has no data source today. "
                "/v1/shipments/loss-details is the per-captain loss list PSP currently reads "
                "from valmo.db instead.",
    },
    {
        "group": "CAPTAIN_API_ROUTES",
        "queue": "Losses & Debits · Cash/COD · Service area · Misroute",
        "service": "captain-service / valmo-logistics / LMS",
        "adapter": "substrate/adapters/local_db_provider (valmo.db stands in)",
        "status": "fixture",
        "env": "PSP_DATA_PROVIDER=prism",
        "routes": CAPTAIN_API_ROUTES,
        "note": "The widest group. /v1/shipment/tracking-details is the scan data the Hardstop "
                "date rules need; cod-pendency-buckets is the COD gap (~16% of tickets). The "
                "risk-* routes moved to SHIPMENT_RISK_API_ROUTES once an adapter served them.",
    },
    {
        "group": "DC_CAPACITY_API_ROUTES",
        "queue": "Orders & Planning (capacity)",
        "service": "captain-panel BFF → capacity service",
        "adapter": "(none)",
        "status": "none",
        "env": "—",
        "routes": DC_CAPACITY_API_ROUTES,
        "note": "DC_CAPACITY_UPDATE is a WRITE. Not wired, and not proposed: PSP recommends, "
                "it does not set a hub's capacity.",
    },
]

_STATUS_ORDER = {"live": 0, "fixture": 1, "none": 2}


def registry() -> dict:
    """The whole table, plus counts. No I/O, no calls — safe to serve on any request."""
    groups = []
    for g in _GROUPS:
        groups.append({**{k: v for k, v in g.items() if k != "routes"},
                       "count": len(g["routes"]),
                       "routes": [{"name": n, "path": p, "timeout_ms": t} for n, p, t in g["routes"]]})

    others = [{"service": svc, "name": name, "path": path, "adapter": adapter,
               "status": status, "queue": queue, "note": note}
              for svc, name, path, adapter, status, queue, note in OTHER_SERVICE_ROUTES]

    total = sum(g["count"] for g in groups) + len(others)
    by_status: dict[str, int] = {}
    for g in groups:
        by_status[g["status"]] = by_status.get(g["status"], 0) + g["count"]
    for o in others:
        by_status[o["status"]] = by_status.get(o["status"], 0) + 1

    return {
        "source": ("valmo-partner-webview :: src/modules/captain/common/api.constants.ts "
                   "(+ the service repos for the rest)"),
        "declarative": True,
        "note": ("Nothing here is called. These are the endpoints the captain panel already "
                 "invokes in production, with what PSP does about each one today."),
        "total": total,
        "by_status": dict(sorted(by_status.items(), key=lambda kv: _STATUS_ORDER.get(kv[0], 9))),
        "groups": sorted(groups, key=lambda g: _STATUS_ORDER.get(g["status"], 9)),
        "other_services": others,
        "legend": {
            "live": "PSP calls this today",
            "fixture": "PSP reads a file shaped to its contract — one env var flips it",
            "none": "no adapter written; named so the gap is visible rather than implied",
        },
    }
