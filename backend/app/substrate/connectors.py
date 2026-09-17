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
#
# ══ CORRECTED AGAINST THE SERVICE SOURCE, 2026-08-25 ═════════════════════════════════════════
# This file was assembled from the captain panel's client-side route table. Six service repos
# were then read directly, and the client table was wrong in three ways that would each have
# broken a real integration:
#
#  1. **EVERY captain path was missing the `/api` prefix.** The controllers declare
#     `@RequestMapping("/api/v1/captain")`; the panel's own constants omit it because its HTTP
#     client prepends a base. Reading this file as an access request would have produced a list
#     of 404s — including for the two GROWTH endpoints this registry claims are CONNECTED.
#  2. **Service ownership was guessed and mostly wrong.** "payouts-auto" is not a service that
#     exists. The payments owner is `vetan` ("Partner Payout compute and scheduler"), the captain
#     panel's own payment/loss screens are `ValCrew-Captain-Service`, and the growth dashboard and
#     hub capacity are `ValmoLogisticsService`.
#  3. **Methods were absent, and they are not guessable.** The same hub-capacity path serves GET,
#     PUT and POST with different semantics. Guessing yields 405.
#
# One catalogued endpoint DOES NOT EXIST and has been removed: `/v1/captain/dc-capacity/update`
# appears nowhere in ValmoLogisticsService — no such string in the source, the endpoint constants,
# or the OpenAPI spec. It was in the client table and nothing serves it.
#
# Paths below are now (method, path, timeout_ms) and are verbatim from the controllers.
# ── vetan — the payments service. THIS IS THE ANSWER TO PSP's LARGEST GAP. ──────────────────
# Every route verified against the controllers in vetan-develop. The two most valuable are not
# the summaries: `/api/v2/payments/details` returns the credited-on timestamp, the masked
# beneficiary account, the UTR and the FAILURE REASON, and `/api/v2/earnings/current-cycle-details`
# returns `payment_processing_date` — the only endpoint anywhere that answers "payment kab
# aayega" rather than "what happened".
#
# vetan also already COMPOSES the captain-facing sentence (PartnerAppHelper
# .buildPaymentStatusDescription): "Payment credited on %s to %s", "Your payment has failed due
# to %s reason" + "This payment is added to your next cycle", "Payment initiated on %s" + "It
# will be credited to your account in 2-3 days". PSP's Payments module invents all of this; it
# does not need to.
VETAN_PAYMENT_ROUTES = [
    ("PAYMENT_DETAILS", "POST /api/v2/payments/details", 1600),
    ("PAYMENT_HISTORY", "POST /api/v2/payments/all", 1600),
    ("CURRENT_CYCLE", "GET /api/v2/earnings/current-cycle-details", 1600),
    ("ENTITY_PENDING", "GET /api/v1/entity/payments/pending", 1600),
    ("ENTITY_PAYMENT_DETAILS", "GET /api/v1/entity/payments/details", 1600),
    ("ENTITY_EARNINGS_ALL", "POST /api/v1/entity/earnings/all", 1600),
    ("ENTITY_AGGREGATED", "POST /api/v1/entity/earnings/aggregated-data", 1600),
    ("LINE_ITEMS", "POST /api/v1/partner/line-items", 1600),
    ("INVOICE_SIGNED_URL", "POST /api/v1/invoice-document/signed-url", 10000),
    ("INVOICE_SUMMARY", "GET /api/v1/invoice-document/summary", 1600),
]

CAPTAIN_API_ROUTES = [
    # ValCrew-Captain-Service. All six verified; all six require a VALMO-ROLE-ID header, which
    # the client route table does not mention. `captain-losses` (no suffix) is allow-listed in
    # their prod config and served by nothing — a dead path, not an endpoint.
    ("PAYMENTS_SUMMARY", "/api/v1/captain/captain-payments-summary", 1600),
    ("PAYMENT_RECORDS", "/api/v1/captain/captain-payments-records", 1600),
    ("PAYMENT_RECORDS_TABLE_SUMMARY", "/api/v1/captain/captain-payments-table-summary", 1600),
    ("PAYMENT_DETAILS", "/api/v1/captain/captain-payments-details", 1600),
    ("LOSS_SUMMARY", "/api/v1/captain/captain-losses-summary", 1600),
    ("LOSS_DETAILS", "/api/v1/captain/captain-losses-records", 1600),
    ("FETCH_SHIPMENT_SUMMARY", "/v1/shipment/summary", 1600),
    ("FETCH_SHIPMENT_DETAILS", "/v1/hubs/shipments", 1600),
    ("HUB_LIST", "/api/v1/captain/hubs", 1600),
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

# ValmoLogisticsService, not a "growth service". `order-summary` already returns a COMPOSED
# answer to "capacity cut kyun laga": banner_type="red_capacity_cut" with the sentence "You are
# under capacity cut due to {low D0 | high DOH}, and capacity has been set to {N} till {date}."
# Both routes also require a VALMO-HUB-CODES authorization header the client table omits.
GROWTH_DASHBOARD_API_ROUTES = [
    ("YOUR_METRICS", "/api/v1/captain/growth-dashboard/{hubId}/your-metrics", 1000),
    ("ORDER_SUMMARY", "/api/v1/captain/growth-dashboard/{hubId}/order-summary", 1000),
]

# Same path, THREE methods, different semantics — POST is read-shaped with a write side-effect.
# `/v1/captain/dc-capacity/update` was catalogued here and does not exist in the service at all.
DC_CAPACITY_API_ROUTES = [
    ("HUB_CAPACITY_READ", "GET /api/v1/captain/hub-capacity/{hubId}", 10000),
    ("HUB_CAPACITY_UPDATE", "PUT /api/v1/captain/hub-capacity/{hubId}", 10000),
    ("HUB_CAPACITY_INIT", "POST /api/v1/captain/hub-capacity/{hubId}", 10000),
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
    ("Nexus", "SHIPMENT_DETAILS_BULK", "POST /api/v1/valmo/shipment/details/bulk", "nexus",
     "none", "losses_debits",
     "BULK shipment details in one call. Catalogued because the per-AWB alternative is what "
     "makes a multi-AWB dispute expensive: a captain contesting a bag of 40 shipments costs 40 "
     "round trips through the routes above. Read-only, and no access today — same Nexus "
     "approval as the two above, so it costs nothing extra to ask for."),
    ("LossManagementService", "LOSS_VALIDATION_TOPIC",
     "(none — Kafka loss-validation)", "lms", "none", "losses_debits",
     "A TOPIC, not an endpoint, and listed so its absence is visible rather than assumed. "
     "It carries the validation verdict on a marked loss. PSP cannot subscribe: the same "
     "reason LOSS_REVERSAL below has no HTTP write path (ADR-0015). Registered so 'can we see "
     "whether a loss was validated' has a recorded answer of NO instead of no answer."),
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
        "service": "ValmoLogisticsService (not a separate growth service)",
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
        "group": "VETAN_PAYMENT_ROUTES",
        "queue": "Payments",
        "service": "vetan — Partner Payout compute and scheduler",
        "adapter": "none written (was mis-filed under prism_provider)",
        "status": "none",
        "env": "—",
        "routes": VETAN_PAYMENT_ROUTES,
        "note": "IDENTIFIED, not unknown. Payments is the largest classified ticket sub-type and "
                "PSP can answer none of it — but the owner, the routes and the response shapes "
                "are all now known, and vetan already composes the captain-facing sentence "
                "itself. The gap is read access, not discovery or design.",
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
        "service": "ValmoLogisticsService — same controller as growth",
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
