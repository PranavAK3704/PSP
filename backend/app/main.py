"""Valmo Partner Support Platform — resolution engine API (BRD-aligned).

FastAPI service. Reactive resolution and proactive monitoring both stream their
pipeline trace over Server-Sent Events so the UI can watch the engine work live.

Auth: every /api route below is gated server-side (see the auth package). Two routes
are intentionally public — POST /api/auth/login and GET /api/health (Render health
check). Authoring writes need author-or-approver; approvals need approver.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from .audit import cpd                                 # noqa: E402
from .audit import rubric as audit_rubric              # noqa: E402
from .audit import runner as audit_runner             # noqa: E402
from .auth import store as auth_store                  # noqa: E402
from .auth import tokens as auth_tokens                # noqa: E402
from .auth.deps import current_user, require_role      # noqa: E402
from .channels import whatsapp                          # noqa: E402
from .engine.algo import followups                      # noqa: E402
from .engine import conversation, dispositions          # noqa: E402
from .engine import write_mode                          # noqa: E402
from .knowledge import blueprints, governance, sop_compiler, store  # noqa: E402
from .kt import engine as kt_engine                     # noqa: E402
from .l3 import platform as l3                          # noqa: E402
from .llm import meter as llm_meter                     # noqa: E402
from .llm import registry as llm_registry               # noqa: E402
from .ledger import concern_log, trace_log               # noqa: E402
from .monitor import monitor                             # noqa: E402
from .substrate import captain_context as ctx            # noqa: E402

app = FastAPI(title="Valmo Partner Support Platform", version="demo-1.0")

# CORS restricted to a configurable allowlist. Production is same-origin (the API serves the SPA),
# so this only matters for local Vite dev; set ALLOWED_ORIGINS (comma-separated) elsewhere.
_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:5190,http://localhost:5173").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_ALLOWED_ORIGINS,
                   allow_methods=["*"], allow_headers=["*"])

# ── Structured request logging + a correlation id — the infra-observability signal we can add
# in-repo with no new deps. In production these lines ship to the Meesho logging/metrics stack. ──
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
_log = logging.getLogger("psp")


@app.middleware("http")
async def _request_log(request, call_next):
    import time as _t
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    start = _t.perf_counter()
    try:
        resp = await call_next(request)
    except Exception:
        _log.exception("request_error method=%s path=%s rid=%s",
                       request.method, request.url.path, rid)
        raise
    resp.headers["X-Request-Id"] = rid
    if request.url.path != "/api/health":   # don't spam on the health probe
        _log.info("request method=%s path=%s status=%s dur_ms=%d rid=%s", request.method,
                  request.url.path, resp.status_code, int((_t.perf_counter() - start) * 1000), rid)
    return resp

# Role gates (server-side; the client is never trusted for role). An approver
# implicitly satisfies an author-level gate (approver ≥ author) — see auth/deps.py.
_authed = Depends(current_user)               # any authenticated user
_author = Depends(require_role("author"))      # authoring writes (author or approver)
_approver = Depends(require_role("approver"))  # approvals / go-live (approver only)


@app.on_event("startup")
def _seed_users():
    """First-run: seed the initial approver + baseline authored content (domain brains, the
    COD Shortfall SOP) so the library is populated on boot regardless of request order."""
    auth_store.seed_initial()
    try:
        kt_engine.ensure_seeded()
        blueprints.load()   # seeds the Losses brain if the store is empty
    except Exception:  # noqa: BLE001 — never let seeding block startup
        pass


class ChatIn(BaseModel):
    captain_id: str
    message: str
    conversation_id: str | None = None
    channel: str = "chat"
    attachments: list[dict] | None = None   # [{filename, mime, size}] — thumbnail kept client-side
    #: The id of a reply chip the captain tapped, when the client offered one. A tap is an exact
    #: choice, so it skips matching entirely — see engine/algo/followups.py. Optional and
    #: ignored by every other path, so an older client that never sends it is unaffected.
    selected_option: str | None = None
    #: WHICH OF OUR OWN SCREENS is asking — `partner` (the captain's docked widget) or
    #: `operator` (the internal test bench). Both post here with the same auth, so the server
    #: cannot tell them apart; the client has to say. Anything unrecognised, including absent,
    #: becomes `unclassified` at `concern_log.append` — deliberately NOT `partner`, so a caller
    #: that forgets shows up as an unlabelled row instead of inflating the partner count.
    #: Not a security boundary (an authenticated operator could send either); it distinguishes
    #: our surfaces from each other so the deck can be aggregated honestly.
    source: str | None = None


class CompileIn(BaseModel):
    sop_text: str


class SopApproveIn(BaseModel):
    policy: dict
    contributor: str = "sop-author"
    sop_id: str = ""            # set when editing an existing SOP → update in place (no duplicate)


class SopDeleteIn(BaseModel):
    sop_id: str


class BlueprintCompileIn(BaseModel):
    raw_text: str
    domain: str | None = None


class BlueprintSaveIn(BaseModel):
    blueprint: dict
    contributor: str = "author"


class BlueprintApproveIn(BaseModel):
    domain: str


class WhatsAppIn(BaseModel):
    from_: str | None = None
    text: str = ""
    payload: dict | None = None   # raw Meta webhook (optional)


class KtIn(BaseModel):
    text: str
    contributor: str = "anonymous"
    attachments: list[str] | None = None


class KtReviewIn(BaseModel):
    kt_id: str
    approve: bool
    reviewer: str = "reviewer"


class NuanceIn(BaseModel):
    text: str                              # plain-language rule / what to do
    domain: str = "general"                # payments | fe_id | losses_debits | cash_cod | consumables | orders | ...
    contributor: str = "sop-author"
    sop_ref: str = ""                      # the SOP/disposition this refines (optional)
    required_inputs: list[str] | None = None   # plain labels the captain must provide
    triggers: list[str] | None = None      # retrieval phrases (optional)
    from_concern_id: str = ""              # set when captured from a live case (L3 correction)
    resolves_gap_id: str = ""              # set when filling a captured auto_gap


class SatisfactionIn(BaseModel):
    concern_id: str
    captain_id: str
    satisfied: bool
    note: str = ""


class L3ResolveIn(BaseModel):
    concern_id: str
    resolution_note: str = ""          # PARTNER-FACING. Validated in l3.validate_reply.
    resolver: str = "L3"
    internal_note: str = ""            # never reaches the captain
    outcome: str = "resolved"          # resolved | need_input | rejected
    attachments: list[dict] | None = None   # [{filename, mime, size}] — metadata only


class AuditRubricIn(BaseModel):
    dimensions: list[dict]


class FrameworkSaveIn(BaseModel):
    framework: dict


class AuditRunIn(BaseModel):
    concern_id: str


class AuditBatchIn(BaseModel):
    # Bounded at the REQUEST BOUNDARY, not just inside the runner. Every unit of `limit`
    # is one deep-tier LLM call, and this route sits behind _authed — any logged-in
    # session, not just an author. An unvalidated `limit` therefore let one request spend
    # the entire API credit (limit=500 → 500+ Opus calls), and because the call blocks for
    # minutes it would hit a proxy timeout BEFORE finishing — the money spent with nobody
    # watching the result. le=20 keeps the worst case bounded; audit_runner clamps again
    # for callers that don't come through FastAPI.
    limit: int = Field(audit_runner.DEFAULT_BATCH, ge=1, le=audit_runner.MAX_BATCH)


class LoginIn(BaseModel):
    email: str
    password: str


class CreateUserIn(BaseModel):
    email: str
    name: str = ""
    role: str = "viewer"
    password: str


def _sse(gen):
    for event in gen:
        yield {"event": "trace", "data": json.dumps(event)}
    yield {"event": "end", "data": "{}"}


# ── Auth (login is PUBLIC; everything else needs a valid token) ──────────────
@app.post("/api/auth/login")
def login(body: LoginIn):
    """Public. Validate credentials → issue a 12h signed token + the user's public profile."""
    user = auth_store.verify_password(body.email, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="invalid email or password")
    return {"token": auth_tokens.make_token(user["email"], user["role"]), "user": user}


@app.get("/api/auth/me")
def auth_me(user: dict = Depends(current_user)):
    """The current user (email, name, role) — resolved from the store, secrets stripped."""
    full = auth_store.get_user(user["email"])
    if not full:
        raise HTTPException(status_code=401, detail="user no longer exists")
    return {"email": full["email"], "name": full.get("name", ""), "role": full.get("role", "viewer")}


@app.get("/api/auth/users", dependencies=[_approver])
def auth_users():
    """Approver only. List users (email, name, role) — NEVER salts/hashes."""
    return {"users": auth_store.list_users()}


@app.post("/api/auth/users", dependencies=[_approver])
def auth_create_user(body: CreateUserIn):
    """Approver only. Create a team member with a temp password + role."""
    try:
        user = auth_store.create_user(body.email, body.name, body.role, body.password)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "user": user}


# ── Health (PUBLIC — Render health check) ────────────────────────────────────
@app.get("/api/health")
def health():
    from .substrate import loss_db
    ds = {"source": loss_db.source()}          # 'remote' (Turso) | 'local' (baked) | 'none'
    try:
        # _i(): Turso's Hrana wire format returns every INTEGER as a JSON *string*, so a raw
        # cell here reported "losses": "1000001" — a count that reads as a string to any client.
        ds["losses"] = loss_db._i(loss_db._query("SELECT COUNT(*) AS n FROM losses", ())[0]["n"])
    except Exception:  # noqa: BLE001
        ds["losses"] = None
    # Which account-data provider is live (canned demo vs the Prism data lake) + its reachability.
    provider = ctx.data_provider()
    ds["account_provider"] = getattr(provider, "source", "unknown")
    # Keyed `provider_status`, not `prism`: LocalDbProvider also implements status(), so the old
    # key reported a localdb probe under the name of a provider that was not running.
    if hasattr(provider, "status"):
        try:
            ds["provider_status"] = provider.status()
        except Exception as e:  # noqa: BLE001 — health must never throw
            ds["provider_status"] = {"ok": False, "detail": type(e).__name__}
    # `key_configured` is presence-only (never the value). Without it a keyless deploy looks
    # perfectly healthy here and only fails on the first real turn — see registry.key_configured.
    key_ok = llm_registry.key_configured()
    # Spend is health. A deployment sitting at 100% of its ceiling answers every request
    # normally right up until the first chat turn, at which point it fails — the same
    # invisible-until-it-matters shape as a missing key, so it is reported the same way.
    try:
        spend = llm_meter.totals()
    except Exception:  # noqa: BLE001 — health must never throw
        spend = None
    # Per-node routing, not one global label. `adversarial_verify` can now be a different
    # VENDOR from the rest of the pipeline, which means a second credential that can be missing
    # on its own — and if it is, every money decision fails closed and reads as the verifier
    # disagreeing. Reporting it here makes that a config fact anyone can check first.
    try:
        route = llm_registry.routing()
        missing = sorted({v["provider"] for v in route.values() if not v["key_configured"]})
        independence = llm_registry.independence_status()
    except Exception:  # noqa: BLE001 — health must never throw
        route, missing, independence = {}, [], None
    return {"ok": True, "provider": llm_registry.active_provider_name(),
            "llm": {"model": llm_registry.active_model_label(),
                    "key_configured": key_ok,
                    "routing": route,
                    **({"verifier_independence": independence} if independence else {}),
                    **({"detail_keys": f"no API key for: {', '.join(missing)} — nodes routed "
                                       f"there will fail (the verifier fails CLOSED, so money "
                                       f"decisions become escalations)"} if missing else {}),
                    **({} if key_ok else {"detail": "No API key set for the active provider — "
                                                    "chat will fail until it is configured."}),
                    **({"spend": spend} if spend else {}),
                    **({"detail_budget": f"LLM budget exhausted (${spend['spent_usd']:.2f} of "
                                         f"${spend['budget_usd']:.2f}) — chat will refuse to call "
                                         f"the model until LLM_BUDGET_USD is raised."}
                       if spend and spend["remaining_usd"] <= 0 else {}),
                    },
            "knowledge": store.corpus_stats(), "data": ds,
            # Two adapters that can each be misconfigured on their own. `risk` in particular:
            # if it silently drops to `seed` the monitor shows FICTIONAL shipments, which looks
            # identical to working — so its mode belongs on the health check, not in a log.
            "growth": ctx.growth_provider().status(),
            "risk": ctx.risk_provider().status()}


@app.get("/api/calibration", dependencies=[_authed])
def calibration():
    """Is the gate's confidence a probability, or a label? Measured, not asserted.

    Returns the reliability bins (with empty bins EMPTY, not dropped) plus the Kapture
    agreement dataset, captioned as a different quantity from the gate's confidence.
    """
    from .audit import calibration as calib
    try:
        return calib.report()
    except Exception as e:  # noqa: BLE001 — a reporting panel must never 500 a live demo
        _log.exception("calibration report failed")
        return {"error": f"{type(e).__name__}", "reliability": None, "kapture": None}


@app.get("/api/connectors", dependencies=[_authed])
def connectors():
    """The connector registry — DECLARATIVE. It never calls anything.

    Augmented at request time with the live adapter state, so the table reflects what this
    process is actually doing rather than what the file says it should.
    """
    from .substrate import connectors as conn
    reg = conn.registry()
    # The growth adapter is the one group whose status is knowable at runtime.
    try:
        g = ctx.growth_provider()
        for grp in reg["groups"]:
            if grp["group"] == "GROWTH_DASHBOARD_API_ROUTES":
                was, now = grp["status"], ("live" if g.mode == "live" else "fixture")
                grp["status"] = now
                grp["detail"] = f"{g.source} · {len(g.known_hubs())} hub(s) on file"
                # `by_status` was summed by registry() from the PRE-mutation statuses, so under
                # PSP_GROWTH_SOURCE=live the summary tiles and this group's own pill disagreed.
                # Re-point the count rather than leaving two numbers that contradict each other.
                if was != now:
                    bs = dict(reg["by_status"])
                    bs[was] = max(0, bs.get(was, 0) - grp["count"])
                    bs[now] = bs.get(now, 0) + grp["count"]
                    reg["by_status"] = {k: v for k, v in bs.items() if v}
    except Exception:  # noqa: BLE001 — a declarative table must never fail on a probe
        pass
    reg["engine"] = {"account_provider": getattr(ctx.data_provider(), "source", "?"),
                     "write_mode": write_mode.mode()}
    return reg


# ── Growth Dashboard (Orders & Planning) ────────────────────────────────────
# The captain panel's own two endpoints, served from fixtures. This route exists so the demo
# can render the SAME payload the engine reasons over — one source of truth for the dashboard
# and for the answer, which is the whole point of docking them side by side.
@app.get("/api/growth", dependencies=[_authed])
def growth_index():
    """Which hubs have growth data, and where it comes from."""
    g = ctx.growth_provider()
    hubs = g.known_hubs()
    # status() also carries a `hubs` COUNT, so spread it first and let the list win — otherwise
    # the client receives an integer where it expects an array and renders nothing.
    return {**g.status(), "hubs": hubs, "hub_count": len(hubs),
            # Which demo partner opens each hub, so the widget can start a real conversation
            # against the same captain the dashboard is showing.
            "partners": _hub_to_partner(hubs)}


@app.get("/api/growth/{hub_code}", dependencies=[_authed])
def growth_hub(hub_code: str):
    """Both growth endpoints for one hub, plus the fixture's provenance block.

    `provenance` is served on a DIFFERENT key from the payload, mirroring the connector: a
    decision must not be able to read it, but a human looking at the screen should.
    """
    g = ctx.growth_provider()
    try:
        data = g.growth(hub_code)
    except NotImplementedError as e:            # PSP_GROWTH_SOURCE=live
        raise HTTPException(status_code=501, detail=str(e)) from e
    if not data.get("available"):
        raise HTTPException(status_code=404, detail=f"no growth data for hub {hub_code}")
    return {**data, "provenance": g.provenance(hub_code)}


def _hub_to_partner(hubs: list[str]) -> dict:
    """hub → a real partner id that sits in it. Best-effort; empty under the demo provider."""
    out: dict[str, str] = {}
    try:
        from .substrate import loss_db
        for pid in loss_db.known_partners():
            h = (loss_db.captain_summary(pid) or {}).get("hub")
            if h in hubs and h not in out:
                out[h] = pid
    except Exception:  # noqa: BLE001 — a convenience mapping must never break the route
        pass
    return out


@app.get("/api/captains", dependencies=[_authed])
def captains():
    return {"captains": ctx.known_captains()}


@app.get("/api/captain/{captain_id}", dependencies=[_authed])
def captain(captain_id: str):
    return ctx.get_context(captain_id) or {"error": "unknown captain"}


#: The only provenances a BROWSER may claim. `monitor`, `l3` and `harness` are asserted by the
#: code that owns those writes — monitor.py, l3/platform.py, scripts/_contain.py — and a request
#: body must not be able to mint them.
#:
#: This existed only in the browser (`CHAT_SOURCES` in api.js), while api.js's own comment
#: promised "a browser cannot claim them". `concern_log._provenance` accepts any member of the
#: full six-value SOURCES tuple, so the promise was false: a client could stamp a chat row
#: `harness` and remove it from the deck, or `monitor` and make an answered question look like
#: something the platform noticed on its own. Enforced here, where it is true.
_CLIENT_SOURCES = ("partner", "operator")


def _client_source(claimed: str | None) -> str:
    """A claim we accept, or "" — which resolves to `unclassified`, never to `partner`."""
    return claimed if claimed in _CLIENT_SOURCES else ""


@app.post("/api/chat", dependencies=[_authed])
def chat(body: ChatIn):
    """Reactive resolution — stateful, multi-turn. Streams the trace as SSE.

    Pass a stable conversation_id across turns; the engine may end a turn in a
    `need_input` event (asking the partner) and resume on the next turn.
    """
    conv_id = body.conversation_id or ("conv-" + uuid.uuid4().hex[:10])
    return EventSourceResponse(_sse(
        conversation.handle_turn(conv_id, body.captain_id, body.message, body.channel,
                                 attachments=body.attachments,
                                 selected_option=body.selected_option,
                                 source=_client_source(body.source))))


@app.get("/api/monitor/{captain_id}", dependencies=[_authed])
def monitor_scan(captain_id: str, as_of: str | None = None):
    """Proactive monitoring — streams the detect→nudge trace as SSE.

    `?as_of=YYYY-MM-DD` replays the severity ladder at one shared date instead of at each row's
    own terminal date. An invalid date is a 400 rather than a silent fall-back to `terminal`:
    the two clocks produce different distributions, so quietly ignoring the parameter would
    stream a terminal distribution that the caller reads as a replay.
    """
    clock_mode = None
    if as_of:
        from .substrate.adapters.risk import ladder as _ladder
        if _ladder.parse_date(as_of) is None:
            raise HTTPException(status_code=400,
                                detail=f"as_of must be YYYY-MM-DD, got {as_of!r}")
        clock_mode = "replay"
    return EventSourceResponse(_sse(
        monitor.scan_captain(captain_id, as_of=as_of, clock_mode=clock_mode)))


# ── at-risk shipments ────────────────────────────────────────────────────────
# A SEPARATE route from /api/growth, deliberately. The growth payload is served under
# `source: "growth-dashboard-fixture"`, and putting real valmo.db rows inside it would make the
# provenance unreconstructable — the inverse of the rule adapters/growth/connector.py states.
# Two labels, two routes, and the UI shows both chips.

def _risk_params(as_of: str | None):
    from .substrate.adapters.risk import ladder as _ladder
    if as_of and _ladder.parse_date(as_of) is None:
        raise HTTPException(status_code=400,
                            detail=f"as_of must be YYYY-MM-DD, got {as_of!r}")
    return {"as_of": as_of, "clock_mode": "replay" if as_of else "terminal"}


@app.get("/api/risk/{captain_id}", dependencies=[_authed])
def risk_partner(captain_id: str, as_of: str | None = None, limit: int = 200):
    """At-risk shipments for one partner. Index-served on attribution.partner_id."""
    try:
        out = ctx.risk_provider().for_partner(captain_id, limit=max(1, min(limit, 500)),
                                             **_risk_params(as_of))
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    # 200 + available:false for a KNOWN partner with an empty cohort; 404 only when the partner
    # is unknown. Those need different UI — "no rows join this partner" is an absence of data,
    # not a bad id — and GrowthConnector.growth() draws the same distinction.
    from .substrate import loss_db
    if not out["summary"].get("available") and captain_id not in loss_db.known_partners(500):
        raise HTTPException(status_code=404, detail=f"unknown partner {captain_id}")
    return out


@app.get("/api/risk/hub/{hub_code}", dependencies=[_authed])
def risk_hub(hub_code: str, as_of: str | None = None, limit: int = 200):
    """At-risk shipments for one hub. UNINDEXED — the payload states the cost in `keyed_on`."""
    try:
        return ctx.risk_provider().for_hub(hub_code, limit=max(1, min(limit, 500)),
                                          **_risk_params(as_of))
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e


@app.post("/api/sop/compile", dependencies=[_author])
def compile_sop(body: CompileIn):
    """Streams the compilation as SSE stages so the UI can animate the real structuring/tiering.
    The final `stage: done` event carries the full ExecutablePolicy + gaps (inline) + meta."""
    return EventSourceResponse(_sse(sop_compiler.compile_sop_streamed(body.sop_text)))


@app.post("/api/sop/extract", dependencies=[_author])
async def sop_extract(file: UploadFile = File(...)):
    """Upload a real ops artifact (Excel / Word / PDF / CSV / text) → extract its content as
    readable text (tables flattened) → return {text, source_name}. The author reviews/edits the
    text, then Compiles it through the normal SOP pipeline. The machine structures; a human
    approves. Extraction only — no LLM, no state write."""
    raw = await file.read()
    text = _extract_text(raw, file.content_type, file.filename)
    return {"text": text, "source_name": file.filename}


@app.post("/api/sop/save", dependencies=[_author])
def save_sop(body: SopApproveIn):
    """Save a compiled SOP as a DRAFT (author-or-approver) so it is never lost — it shows in
    the library and can be approved later. sop_id set → edit in place. Returns {ok, id, gaps}."""
    entry = sop_compiler.save_sop_draft(body.policy, body.contributor, body.sop_id)
    return {"ok": True, "id": entry["id"], "gaps": sop_compiler.detect_policy_gaps(body.policy),
            "conformance": entry.get("conformance")}


@app.post("/api/sop/approve", dependencies=[_approver])
def approve_sop(body: SopApproveIn):
    """A reviewed structured SOP enters the retrieval corpus (with reload) so the engine
    follows it. sop_id set → update the existing SOP in place. Returns {ok, id, gaps}."""
    entry = sop_compiler.approve_sop(body.policy, body.contributor, body.sop_id)
    return {"ok": True, "id": entry["id"], "gaps": sop_compiler.detect_policy_gaps(body.policy),
            "conformance": entry.get("conformance")}


@app.post("/api/sop/conformance", dependencies=[_author])
def sop_conformance(body: SopApproveIn):
    """Score a policy against the approved Governance Framework on demand (owner, money cap,
    partner rights, idempotency, band) — the studio re-checks after an edit before approving."""
    return {"ok": True, "conformance": governance.check_conformance(body.policy)}


@app.post("/api/sop/delete", dependencies=[_approver])
def delete_sop(body: SopDeleteIn):
    """Remove a compiled SOP by id (Knowledge Base management, approver only). Returns {ok, removed}."""
    removed = sop_compiler.delete_sop(body.sop_id)
    return {"ok": True, "removed": removed}


# ── Authoring Studio: Domain Blueprints (a domain's stage-0 "brain") ─────────
@app.post("/api/blueprint/compile", dependencies=[_author])
def compile_blueprint(body: BlueprintCompileIn):
    """Streams the structuring of a free-text domain walkthrough into a Blueprint (SSE stages).
    The final `stage: done` event carries {blueprint, gaps} — gaps surfaced AT creation (req #4)."""
    return EventSourceResponse(_sse(
        blueprints.compile_blueprint_streamed(body.raw_text, body.domain)))


@app.get("/api/blueprints", dependencies=[_authed])
def blueprints_list():
    """All Blueprints (draft + approved), newest-updated first, each with its inline gaps."""
    items = blueprints.list_blueprints()
    return {"blueprints": [{**b, "gaps": blueprints.detect_gaps(b)} for b in items]}


@app.post("/api/blueprint/save", dependencies=[_author])
def save_blueprint(body: BlueprintSaveIn):
    """Save/replace a Blueprint as a draft. Returns {ok, blueprint, gaps, existing_brain}.
    existing_brain is the dedup summary of any blueprint already stored for this domain —
    resolved BEFORE the save (save overwrites the one-per-domain entry)."""
    domain = str((body.blueprint or {}).get("domain", "")).strip().lower()
    existing = blueprints.existing_brain(domain)
    bp = blueprints.save(body.blueprint, body.contributor)
    return {"ok": True, "blueprint": bp, "gaps": blueprints.detect_gaps(bp),
            "existing_brain": existing}


@app.post("/api/blueprint/approve", dependencies=[_approver])
def approve_blueprint(body: BlueprintApproveIn):
    """Approve a Blueprint → status=approved + reload; the engine now follows it. Returns {ok}."""
    bp = blueprints.approve(body.domain)
    if bp is None:
        return {"ok": False, "error": "blueprint not found"}
    return {"ok": True, "domain": bp["domain"], "status": bp["status"]}


@app.get("/api/ledger", dependencies=[_authed])
def ledger():
    return {"concerns": concern_log.all_concerns(), "stats": concern_log.stats()}


@app.get("/api/concern/{concern_id}/trace", dependencies=[_authed])
def concern_trace(concern_id: str):
    """The persisted resolution TRACE for a concern — the stage-by-stage events
    (node/label/status/detail/data) the engine streamed while resolving it.
    Returns {events: []} if no trace was stored (e.g. pre-dating the feature)."""
    rec = trace_log.get(concern_id)
    if not rec:
        return {"concern_id": concern_id, "events": []}
    return rec


# CSV column order for the flat ledger export.
# `source` sits right after the id on purpose: it is the first thing a reader of this file needs
# in order to know whether the row counts. It was missing, and `csv.DictWriter(...,
# extrasaction="ignore")` drops an unlisted key without complaint — so the CSV and the JSON
# export of the SAME ledger disagreed on schema, and the flat one (the artefact anyone actually
# opens in a spreadsheet to do their own totals) carried no provenance at all.
_EXPORT_COLUMNS = ["id", "source", "seq", "logged_at", "captain_id", "conversation_id",
                   "disposition", "action_taken", "outcome", "intent", "amount_inr",
                   "escalation_team", "reply"]


@app.get("/api/ledger/export", dependencies=[_authed])
def ledger_export(format: str = "json"):
    """Download the Concern Log. format=csv → flat columns; format=json → full records.
    Sets Content-Disposition so the browser downloads it as a file."""
    concerns = concern_log.all_concerns()
    fmt = (format or "json").lower()
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_EXPORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for c in concerns:
            row = {k: c.get(k, "") for k in _EXPORT_COLUMNS}
            # flatten any newlines in free-text so the CSV stays one-row-per-concern
            if row.get("reply"):
                row["reply"] = str(row["reply"]).replace("\n", " ").replace("\r", " ")
            if row.get("intent"):
                row["intent"] = str(row["intent"]).replace("\n", " ").replace("\r", " ")
            writer.writerow(row)
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="concern_log.csv"'},
        )
    return Response(
        content=json.dumps(concerns, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="concern_log.json"'},
    )


# ── Channels: WhatsApp (the near-term primary channel) ──────────────────────
@app.post("/api/whatsapp/webhook", dependencies=[_authed])
def whatsapp_webhook(body: WhatsAppIn):
    """WhatsApp version of the engine. Parses a webhook, runs a conversation turn
    keyed by phone (so multi-turn works over WhatsApp), and returns the reply."""
    payload = body.payload or {"from": body.from_, "text": body.text}
    parsed = whatsapp.parse_webhook(payload)
    if not parsed or not parsed.get("captain_id"):
        return {"error": "unknown sender or empty message", "hint": "map phone→captain in whatsapp.PHONE_TO_CAPTAIN"}
    conv_id = f"wa-{parsed['phone']}"
    reply, terminal = None, None
    # handle_turn ends a turn with a `reply` node carrying the captain-facing message
    # (same shape as the SSE 'reply' event): ev['data']['reply'], with ev['detail'] as
    # the plain-text fallback. Capture that so we forward the engine's ACTUAL reply.
    # `partner` is asserted SERVER-SIDE here, unlike /api/chat: a WhatsApp webhook is inbound
    # from a real captain's handset by construction, so there is no client claim to trust.
    for ev in conversation.handle_turn(conv_id, parsed["captain_id"], parsed["text"],
                                       channel="whatsapp", source="partner"):
        if ev["node"] == "reply":
            terminal = ev
            reply = ev["data"].get("reply") or ev.get("detail")
    # ── the buttonless degradation ────────────────────────────────────────────────────
    # `whatsapp.send()` takes text and nothing else — Meta's interactive-message API needs a
    # Business account this environment does not have. So predicted follow-ups, which are
    # tappable chips in the panel, go out here as a numbered list; `followups.ordinal_choice`
    # turns the captain's "2" back into that exact option on the next turn. Same menu, same
    # determinism, no transport features required.
    opts = ((terminal or {}).get("data") or {}).get("options") or []
    body_text = followups.as_numbered_text(reply, opts) if (reply and opts) else (reply or "…")
    # whatsapp.send is a STUB — pending real WhatsApp Business API wiring (see channels/whatsapp.py).
    outbound = whatsapp.send(parsed["phone"], body_text)
    return {"captain_id": parsed["captain_id"], "state": terminal["node"] if terminal else "?",
            "reply": reply, "options": opts, "outbound": outbound}


# ── L3 functional-team platform ─────────────────────────────────────────────
@app.get("/api/l3/inbox", dependencies=[_authed])
def l3_inbox():
    # `meta` lets the desk explain an empty queue instead of looking like a failed fetch.
    return {"items": l3.inbox(), "teams": l3.team_metrics(), "meta": l3.inbox_meta()}


@app.post("/api/l3/resolve", dependencies=[_authed])
def l3_resolve(body: L3ResolveIn):
    """L3 resolves an escalated case → drops it from the active queue and creates a
    captain-facing follow-up (closes the loop across states).

    A 400 on a rejected message rather than a 200 with an `error` key: the composer needs to keep
    what was typed and say why, and a silent 200 is how "done" became every resolution in the log.
    """
    out = l3.resolve(body.concern_id, body.resolution_note, body.resolver,
                     internal_note=body.internal_note, outcome=body.outcome,
                     attachments=body.attachments)
    if out.get("error"):
        raise HTTPException(status_code=400, detail=out["error"])
    return out


@app.get("/api/captain/{captain_id}/nudges", dependencies=[_authed])
def captain_nudges(captain_id: str, limit: int = 5):
    """What proactive monitoring found for this captain — the output of the scan, addressed to the
    person it concerns rather than to an internal trace view. Each carries the panel MODULE it is
    about, so the UI can badge the screen the problem is on."""
    return {"nudges": l3.nudges(captain_id, limit=max(1, min(limit, 20)))}


@app.get("/api/captain/{captain_id}/cases", dependencies=[_authed])
def captain_cases(captain_id: str, limit: int = 6, include_test: bool = False):
    """Captain-facing 'My Cases': escalated cases + live status + resolution (polled by the widget).

    Defaults are the CAPTAIN's defaults — harness rows excluded and the list capped — because
    this route's primary caller is a ~90px strip on their own panel. `include_test=1` is for the
    internal bench, which wants to see what it just wrote.
    """
    return {"cases": l3.cases(captain_id, limit=max(1, min(limit, 50)),
                              include_test=include_test)}


# ── Data Foundation — CORPUS-LEVEL aggregates over both databases ────────────────────────────
# Deliberately aggregate-only: no partner id, no AWB, no per-captain drill-down. That is what
# makes it safe to display, and it is the lesson from the panel deleted in 52d0001, which
# listed every captain behind a picker and so could never travel into a captain-facing widget.
@app.get("/api/data/foundation", dependencies=[_authed])
def data_foundation():
    from .substrate import loss_db, tickets_db
    t = tickets_db.summary()
    return {
        "losses": loss_db.corpus_stats(),
        "tickets": {k: t.get(k) for k in ("available", "source", "total", "sla",
                                          "avg_resolution_hours", "by_status", "by_source",
                                          "by_queue", "by_sub_type", "monthly",
                                          "hubs_with_tickets")},
        "top_hubs": tickets_db.top_hubs(),
        "engine_provider": getattr(ctx.data_provider(), "source", "unknown"),
    }


# ── Support tickets — READ-ONLY analytics over the Kapture export (tickets.db) ───────────────
# 140k tickets, PII scrubbed at build time. SELECT-only: these endpoints cannot write anything,
# and a missing tickets.db just reports available:false. Aggregate + per-hub drill-down.
@app.get("/api/tickets/summary", dependencies=[_authed])
def tickets_summary():
    from .substrate import tickets_db
    return {**tickets_db.summary(), "top_hubs": tickets_db.top_hubs()}


@app.get("/api/tickets/hub/{hub_code}", dependencies=[_authed])
def tickets_by_hub(hub_code: str):
    from .substrate import tickets_db
    return tickets_db.by_hub(hub_code)


# ── KT engine ───────────────────────────────────────────────────────────────
@app.post("/api/kt/submit", dependencies=[_author])
def kt_submit(body: KtIn):
    return kt_engine.submit(body.text, body.contributor, body.attachments)


@app.get("/api/kt", dependencies=[_authed])
def kt_list():
    return {"pending": kt_engine.pending(), "all": kt_engine.all_kt()}


@app.post("/api/kt/review", dependencies=[_approver])
def kt_review(body: KtReviewIn):
    """Approver only — approving/rejecting KT changes what the engine follows (go-live)."""
    res = kt_engine.review(body.kt_id, body.approve, body.reviewer)
    return res or {"error": "kt not found"}


# ── non-tech nuance authoring ───────────────────────────────────────────────
@app.post("/api/sop/nuance", dependencies=[_author])
def sop_nuance(body: NuanceIn):
    """A non-tech author fills a gap / adds a correction. Enters the KT approval queue;
    once approved it becomes engine behaviour via retrieval — no code change."""
    return kt_engine.submit_nuance(
        body.text, body.domain, body.contributor, sop_ref=body.sop_ref,
        required_inputs=body.required_inputs, triggers=body.triggers,
        from_concern_id=body.from_concern_id, resolves_gap_id=body.resolves_gap_id)


# ── Audit + CPD + satisfaction ──────────────────────────────────────────────
@app.post("/api/satisfaction", dependencies=[_authed])
def satisfaction(body: SatisfactionIn):
    return cpd.record_satisfaction(body.concern_id, body.captain_id, body.satisfied, body.note)


@app.get("/api/audit", dependencies=[_authed])
def audit():
    return {"trail": cpd.audit_trail(), "cpd": cpd.cpd_items(), "satisfaction": cpd.satisfaction_stats()}


# ── Auditing Studio: editable rubric + LLM judge + score dashboard ───────────
@app.get("/api/audit/rubric", dependencies=[_authed])
def audit_rubric_get():
    """The current (versioned) audit rubric the LLM judge scores against."""
    return audit_rubric.get_rubric()


@app.post("/api/audit/rubric", dependencies=[_author])
def audit_rubric_save(body: AuditRubricIn):
    """Save an edited rubric → bumps version. Returns the new rubric. Authoring write."""
    return audit_rubric.save_rubric(body.dimensions)


@app.post("/api/audit/run", dependencies=[_authed])
def audit_run(body: AuditRunIn):
    """Audit one concern with the LLM judge → composite + per-dimension scores."""
    return audit_runner.audit_concern(body.concern_id)


@app.post("/api/audit/run_batch", dependencies=[_authed])
def audit_run_batch(body: AuditBatchIn):
    """Audit the most recent N un-audited concerns (sampling). Returns a summary."""
    return audit_runner.audit_batch(body.limit)


@app.get("/api/audit/scores", dependencies=[_authed])
def audit_scores():
    """Audit history + aggregates (avg composite, per-dimension avg, trend, by disposition)."""
    return audit_runner.scores()


# ── Auditing Studio: dynamic, editable Governance Framework ──────────────────
def _extract_text(raw: bytes, content_type: str, filename: str) -> str:
    """Best-effort text extraction from an uploaded doc, so an author can drop a real
    operations artifact (SOP sheet, KT doc, framework) straight in and let the machine
    structure it. Handles: PDF (pypdf), Excel .xlsx (openpyxl), Word .docx (python-docx),
    and utf-8 text/csv/markdown/plain. Tables are flattened to readable rows so the LLM
    keeps the structure. Raises 400 on empty/unreadable content."""
    if raw and len(raw) > 12_000_000:   # ~12MB cap — a huge upload read fully into memory could OOM
        raise HTTPException(status_code=413, detail="File too large — please upload under ~12MB.")
    import io as _io
    ctype = (content_type or "").lower()
    name = (filename or "").lower()

    def _fail(msg):
        raise HTTPException(status_code=400, detail=msg)

    if "application/pdf" in ctype or name.endswith(".pdf"):
        from pypdf import PdfReader
        try:
            reader = PdfReader(_io.BytesIO(raw))
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception as e:  # noqa: BLE001
            _fail(f"could not read PDF: {e}")
    elif name.endswith((".xlsx", ".xlsm")) or "spreadsheetml" in ctype:
        try:
            from openpyxl import load_workbook
            wb = load_workbook(_io.BytesIO(raw), read_only=True, data_only=True)
            parts = []
            for ws in wb.worksheets:
                parts.append(f"## Sheet: {ws.title}")
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                    if cells:
                        parts.append(" | ".join(cells))
            text = "\n".join(parts)
        except Exception as e:  # noqa: BLE001
            _fail(f"could not read Excel file: {e}")
    elif name.endswith((".docx",)) or "wordprocessingml" in ctype:
        try:
            import docx  # python-docx
            doc = docx.Document(_io.BytesIO(raw))
            parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
            for tbl in doc.tables:
                for row in tbl.rows:
                    cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
            text = "\n".join(parts)
        except Exception as e:  # noqa: BLE001
            _fail(f"could not read Word file: {e}")
    elif name.endswith((".xls", ".doc")):
        _fail("legacy .xls/.doc isn't supported — please re-save as .xlsx / .docx (or paste the text).")
    else:
        text = raw.decode("utf-8", errors="replace")

    text = (text or "").strip()
    if not text:
        _fail("no extractable text in the uploaded file")
    return text


@app.get("/api/framework", dependencies=[_authed])
def framework_get():
    """The current (versioned) Governance Framework — the seeded PLACEHOLDER on first run."""
    return governance.get()


@app.post("/api/framework", dependencies=[_author])
def framework_save(body: FrameworkSaveIn):
    """Save an edited framework as a draft (author-or-approver). Returns {ok, framework}."""
    fw = governance.save(body.framework)
    return {"ok": True, "framework": fw}


@app.post("/api/framework/upload", dependencies=[_author])
async def framework_upload(file: UploadFile = File(...)):
    """Upload a framework document → extract text (pypdf for PDF; utf-8 for text/csv/md/plain)
    → structure it into the Framework model with the LLM → return {framework(draft), source_name}.
    The machine structures it; a human reviews before approving (author-or-approver)."""
    raw = await file.read()
    text = _extract_text(raw, file.content_type, file.filename)
    fw = governance.structure_framework_from_text(text)
    return {"framework": fw, "source_name": file.filename}


@app.post("/api/framework/approve", dependencies=[_approver])
def framework_approve():
    """Publish the current framework → status=approved + bump version (approver only)."""
    fw = governance.approve()
    return {"ok": True, "framework": fw}


@app.get("/api/insights", dependencies=[_authed])
def insights():
    """One aggregate for the ops control tower."""
    # Additive ops metrics — each guarded so a hiccup here can never blank the panel.
    try:
        active_breaches = sum(1 for it in l3.inbox() if it.get("breached"))
    except Exception:  # noqa: BLE001
        active_breaches = 0
    try:
        avg_resolution_time = concern_log.resolution_time_stats()
    except Exception:  # noqa: BLE001
        avg_resolution_time = {"display": "—", "hours": None, "sample": 0, "basis": "n/a"}
    return {
        "provider": llm_registry.active_provider_name(),
        "knowledge": store.corpus_stats(),
        "ledger": concern_log.stats(),
        # The composition behind every ledger number above, so the UI can show what was excluded
        # rather than presenting a filtered figure as the whole truth.
        "provenance": concern_log.provenance_counts(),
        "satisfaction": cpd.satisfaction_stats(),
        "l3_teams": l3.team_metrics(),
        "cpd_open": len(cpd.cpd_items()),
        "dispositions": len(dispositions.catalogue()),
        "active_breaches": active_breaches,
        "avg_resolution_time": avg_resolution_time,
    }


# ── Serve the built frontend (production / Render) ───────────────────────────
# The Vite build lands in <repo>/frontend/dist (local) or /app/frontend/dist (Docker).
# We mount it AFTER every /api route so the API always wins; a missing dist (local dev
# via `run.sh`, where Vite serves the UI on :5190) simply skips mounting so boot never
# breaks. Unknown non-/api GET paths fall back to index.html (SPA-friendly).
_DIST = Path(os.environ.get("PSP_STATIC_DIR") or (Path(__file__).resolve().parents[2] / "frontend" / "dist"))

# THE SPA CACHING CONTRACT. index.html was served with NO Cache-Control at all, which lets a
# browser apply heuristic caching to it. Vite fingerprints the asset filenames, so that one
# missing header is enough to break every deploy: the stale index.html keeps pointing at the
# PREVIOUS bundle hash, the browser happily serves it from disk, and the new build is invisible
# — the app looks unchanged even though the server is running new code. (Observed exactly that:
# the deploy was live and correct while the browser still rendered the old UI.)
#
# The fix is the standard pair, and both halves are required:
#   • index.html          → no-cache: revalidate EVERY load. It is ~2.5 kB, so this is free.
#   • /assets/<hash>.js   → immutable, 1 year: the hash IS the cache key, so a changed file is
#                           a changed URL and can never be served stale.
_HTML_CACHE = {"Cache-Control": "no-cache, must-revalidate"}
_ASSET_CACHE = {"Cache-Control": "public, max-age=31536000, immutable"}


def _html(path: Path) -> FileResponse:
    return FileResponse(str(path), media_type="text/html", headers=_HTML_CACHE)


if (_DIST / "index.html").is_file():
    _assets = _DIST / "assets"
    if _assets.is_dir():
        # StaticFiles does not set long-lived caching itself; wrap it so the fingerprinted
        # bundles get the immutable half of the contract above.
        class _ImmutableStatic(StaticFiles):
            def file_response(self, *args, **kwargs):   # noqa: D102
                resp = super().file_response(*args, **kwargs)
                resp.headers.update(_ASSET_CACHE)
                return resp

        app.mount("/assets", _ImmutableStatic(directory=str(_assets)), name="assets")

    @app.get("/", include_in_schema=False)
    def _spa_root():
        return _html(_DIST / "index.html")

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa_fallback(full_path: str):
        # never let the SPA catch-all shadow the API surface
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        candidate = _DIST / full_path
        if candidate.is_file() and candidate.suffix.lower() != ".html":
            return FileResponse(str(candidate))
        # Every SPA route resolves to index.html, so it carries the no-cache header too.
        return _html(_DIST / "index.html")
