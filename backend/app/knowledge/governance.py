"""Governance Framework — a dynamic, fully editable governance model, stored as JSON.

The Auditing Studio ships with a PLACEHOLDER framework mirroring Meesho's Seller
Governance OS: signal sources, weighted scoring dimensions (with sub-factors + an
optional ordinal ladder), a combine rule + formula, priority bands + band-movement
rules, metrics, accountability, and a live catalogue of prioritised problems. It is
DATA, not code — the Valmo team edits every part (or uploads their own captain-
governance document and has the machine structure it) with zero code change.

Mirrors the editable-config stores (audit/rubric.py, knowledge/blueprints.py):
a JSON store under the durable-state dir + versioning + seed-on-first-run, with
get()/save(draft)/approve(). structure_framework_from_text() reuses the SAME LLM
mechanism the SOP compiler uses (for_node("sop_compile"), json_mode, _parse_json).
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone

from ..llm import registry as llm_registry
from ..llm.gemini_provider import _parse_json
from ..durable_state import durable_path

# MUTABLE authored content → durable state dir (survives redeploys); default backend/data.
_STORE = durable_path("governance_framework.json")
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── The PLACEHOLDER seed (Meesho's Seller Governance OS) — clearly an example ──
# Everything below is illustrative: the Valmo captain-governance team should swap the
# dimensions / bands / metrics / items for their own model. All of it is editable in
# the Auditing Studio with zero code change.
_SEED_NOTE = (
    "PLACEHOLDER example, mirroring Meesho's Seller Governance OS. Swap the dimensions, "
    "bands, metrics and items for Valmo's own captain-governance framework — every field "
    "here is editable in the Auditing Studio (or upload your own doc and have it structured), "
    "with zero code change."
)


def _seed() -> dict:
    return {
        "name": "Seller Governance OS — PLACEHOLDER (replace with Valmo captain governance)",
        "version": 1,
        "status": "approved",
        "updated_at": _now(),
        "objective": ("Identify seller pain-points systematically, prioritise them, and hold "
                      "accountability (Product/Policy/Ops owners, SLAs, NPS impact per problem)."),
        "note": _SEED_NOTE,
        "signal_sources": [
            {"label": "Seller tells us", "mode": "reactive",
             "examples": ["tickets", "NPS", "calls", "in-panel feedback"]},
            {"label": "Seller tells the world", "mode": "reactive",
             "examples": ["social media", "app reviews", "forums", "courts", "press"]},
            {"label": "Sellers tell each other", "mode": "reactive",
             "examples": ["WhatsApp", "Telegram", "FB groups", "aggregators"]},
            {"label": "Our own people see it", "mode": "reactive",
             "examples": ["Slack", "field/KAM notes", "leadership escalations"]},
            {"label": "Seller acts, says nothing", "mode": "proactive",
             "examples": ["stops shipping", "pulls ads", "delists", "logs in less"]},
            {"label": "The platform knows first", "mode": "proactive",
             "examples": ["error rates", "ticket spikes", "release watch", "test sellers"]},
        ],
        "dimensions": [
            {"key": "scale", "label": "Scale",
             "description": "How many sellers / how much of the platform the problem touches.",
             "scale_min": 2, "scale_max": 10,
             "sub_factors": [
                 {"label": "% transacting sellers affected", "scale_min": 1, "scale_max": 5,
                  "normalization": "percentile-rank", "note": ""},
                 {"label": "OC % at risk", "scale_min": 1, "scale_max": 5,
                  "normalization": "percentile-rank", "note": ""},
             ],
             "ladder": []},
            {"key": "severity", "label": "Severity",
             "description": "How badly it hurts an affected seller — functional damage plus behavioural signals.",
             "scale_min": 2, "scale_max": 10,
             "sub_factors": [
                 {"label": "Functional severity", "scale_min": 1, "scale_max": 5,
                  "normalization": "min-max", "note": ""},
                 {"label": "Behavioural signals", "scale_min": 1, "scale_max": 5,
                  "normalization": "min-max",
                  "note": "manifest stops, panel disengagement, listing withdrawal, ads pullback, OOS"},
             ],
             "ladder": [
                 {"level": 5, "label": "Seller CODB"},
                 {"level": 4, "label": "Active orders at risk"},
                 {"level": 3, "label": "Order growth at risk"},
                 {"level": 2, "label": "Operational friction"},
                 {"level": 1, "label": "Information gap"},
             ]},
            {"key": "recoverability", "label": "Recoverability",
             "description": "Multiplier — can the seller recover via the panel/support, or not?",
             "scale_min": 1, "scale_max": 2,
             "sub_factors": [
                 {"label": "Recoverability multiplier", "scale_min": 1, "scale_max": 2,
                  "normalization": "", "note": "Solvable via panel/support (1×) vs Not solvable (2×)"},
             ],
             "ladder": []},
        ],
        "combine": "multiply",
        "formula": "Priority Index = Scale × Severity × Recoverability",
        "bands": [
            {"label": "P0", "meaning": "critical — confirmed platform-exit behaviour",
             "action": "immediate owner + SLA; red-flag in weekly Kaizen"},
            {"label": "P1", "meaning": "high severity, recoverable",
             "action": "owner + TAT active"},
            {"label": "P2", "meaning": "recoverable, monitored",
             "action": "monitored queue"},
            {"label": "Watch", "meaning": "tracked",
             "action": "watchlist; reopen if scale spikes"},
            {"label": "Closed", "meaning": "Scale = 0",
             "action": "removed from live set; reopen if seller signals re-emerge"},
        ],
        "band_movement": [
            {"from": "P0", "to": "P0",
             "condition": "still P0 or worsens → red-flag in weekly Kaizen, escalate, Governance Score does not fall"},
            {"from": "P0", "to": "P1",
             "condition": "recovering; residual sellers remain; owner + TAT stay active"},
            {"from": "P1", "to": "P2/Watch",
             "condition": "move to monitored queue; reopen if scale spikes"},
            {"from": "any", "to": "Closed",
             "condition": "Scale=0 → remove from live P0/P1 set; reopen if seller signals re-emerge"},
        ],
        "metrics": [
            {"name": "Manifest Stopped %", "kind": "behavioural",
             "definition": "Share of affected sellers who have stopped manifesting shipments."},
            {"name": "Panel Usage Change %", "kind": "behavioural",
             "definition": "Change in seller panel logins / active usage vs baseline."},
            {"name": "Listing run-rate change %", "kind": "behavioural",
             "definition": "Change in the rate of new/active listings vs baseline."},
            {"name": "Ad Participation Change %", "kind": "behavioural",
             "definition": "Change in ad spend / campaign participation vs baseline."},
            {"name": "OOS OC %", "kind": "behavioural",
             "definition": "Order-contribution share of sellers going out-of-stock."},
            {"name": "NPS Impact", "kind": "impact",
             "definition": "Modelled NPS movement attributable to the problem."},
            {"name": "OC%/Spread", "kind": "impact",
             "definition": "Order-contribution share affected and how widely it spreads."},
        ],
        "accountability": {
            "owners_by": ("BU (Product / Policy / Ops — e.g. Claims, Pricing, Payments, "
                          "First Mile Ops, Catalog)"),
            "sla_hours": 48,
            "score_def": ("Weekly Governance Score per BU = sum of Priority Index across all "
                          "unpicked + overdue P0/P1 problems (higher = more unaddressed seller "
                          "pain; falls only as Scale drops)."),
        },
        "items": [
            {"name": "Claims for damaged/wrong returns rejected even when customer and seller confirm damage",
             "journey_stage": "Post-Order: Claims", "priority": "P0", "impact_type": "CODB",
             "scale": 8.8, "severity": 6, "recoverability": "2×", "index": 104.3,
             "metrics": {}, "root_cause": "", "policy_proposed": "", "owner": "SX: Claims",
             "timeline": "", "nps_impact": "", "recovery_path": "",
             "example": True},
            {"name": "High / incorrect reverse shipping charge for the seller",
             "journey_stage": "Post-Order: Reverse Leg", "priority": "P0", "impact_type": "CODB",
             "scale": 6.5, "severity": 7.2, "recoverability": "2×", "index": 94.1,
             "metrics": {}, "root_cause": "", "policy_proposed": "", "owner": "Pricing",
             "timeline": "", "nps_impact": "", "recovery_path": "",
             "example": True},
            {"name": "Sellers receive wrong, damaged, or missing products on return",
             "journey_stage": "Post-Order: Reverse Leg", "priority": "P0", "impact_type": "CODB",
             "scale": 10, "severity": 6.1, "recoverability": "1×", "index": 61,
             "metrics": {}, "root_cause": "", "policy_proposed": "", "owner": "SX: Claims",
             "timeline": "", "nps_impact": "", "recovery_path": "",
             "example": True},
        ],
    }


# ── Shape coercion — every part present so the editor never crashes on a missing key ──
_LIST_KEYS = ["signal_sources", "dimensions", "bands", "band_movement", "metrics", "items"]


def _coerce(fw: dict) -> dict:
    """Normalise an author/LLM-supplied framework into the stored shape (fills defaults;
    never drops author content). Lists stay lists; accountability stays a dict."""
    fw = dict(fw or {})
    fw["name"] = str(fw.get("name") or "Untitled Governance Framework")
    fw["objective"] = str(fw.get("objective") or "")
    fw["note"] = str(fw.get("note") or "")
    for k in _LIST_KEYS:
        v = fw.get(k)
        fw[k] = v if isinstance(v, list) else []
    fw["combine"] = fw.get("combine") or "multiply"
    fw["formula"] = str(fw.get("formula") or "")
    acc = fw.get("accountability")
    acc = acc if isinstance(acc, dict) else {}
    acc.setdefault("owners_by", "")
    try:
        acc["sla_hours"] = int(acc.get("sla_hours") or 0)
    except (TypeError, ValueError):
        acc["sla_hours"] = 0
    acc.setdefault("score_def", "")
    fw["accountability"] = acc
    return fw


def _load() -> dict | None:
    if _STORE.exists():
        try:
            data = json.loads(_STORE.read_text())
            if isinstance(data, dict) and data.get("name"):
                return data
        except Exception:  # noqa: BLE001
            return None
    return None


def _write(fw: dict) -> None:
    _STORE.write_text(json.dumps(fw, indent=1))


def get() -> dict:
    """Current framework (seeds the PLACEHOLDER on first run)."""
    with _lock:
        fw = _load()
        if fw is None:
            fw = _seed()
            _write(fw)
        return fw


def save(framework: dict) -> dict:
    """Save an edited framework as a DRAFT (version preserved; bumped only on approve).
    Returns the stored draft."""
    with _lock:
        current = _load() or _seed()
        fw = _coerce(framework)
        fw["version"] = int(current.get("version", 1) or 1)
        fw["status"] = "draft"
        fw["updated_at"] = _now()
        _write(fw)
        return fw


def approve() -> dict:
    """Publish the current framework → status=approved + bump version (approver-only route)."""
    with _lock:
        fw = _load() or _seed()
        fw = _coerce(fw)
        fw["version"] = int(fw.get("version", 1) or 1) + 1
        fw["status"] = "approved"
        fw["updated_at"] = _now()
        _write(fw)
        return fw


# ── SOP ↔ Governance CONFORMANCE (the self-sustaining loop) ───────────────────
# The Framework doesn't just describe how to prioritise problems — it mandates HOW a
# resolution must be governed: an accountable owner, binding partner rights, a money
# ceiling, idempotent money moves, and a priority band. check_conformance() scores a
# compiled ExecutablePolicy against those mandates so the Authoring Studio can gate
# approval on governance — turning "author an SOP and hope it's safe" into "the SOP is
# checked against the org's own governance model before it goes live". Advisory by design
# (findings, not a hard block) — parity with detect_policy_gaps / detect_fidelity_gaps.
#
# INFRA NOTE: the rules below are framework-derived but intentionally conservative; the
# Valmo governance team will fine-tune thresholds/severities as their framework matures.
# "Moves money" must be an actual money-MOVING verb, not a mention — the verb list is deliberately
# specific (reverse/refund/credit/compensate/…) so it does NOT fire on "explain the payout timeline"
# ("payout" is a noun and doesn't match `pay\s+out`). We intentionally DON'T suppress on a leading
# informational verb: "Confirm the damage and reimburse the seller" still moves money, and for a
# money GUARDRAIL a false-positive (an info SOP asked to set a cap) is far safer than a false-negative
# (an unbounded automated payout certified conformant). Fine-tune the verb list as the framework matures.
_MONEY_MOVE_RE = re.compile(
    r"(?i)\b(revers\w*|refund\w*|credit\w*|compensat\w*|waiv\w*|reimburs\w*|"
    r"adjust\w*|settle\w*|disburse\w*|pay\s+(?:out|the|back|₹|rs))\b")


def _moves_money(res: dict) -> bool:
    """True when the RESOLUTION disburses/reverses money — a declared ₹ cap, or a money-moving verb
    anywhere in the action (a leading info verb must NOT hide a later money verb)."""
    res = res or {}
    if res.get("cap_inr") is not None:
        return True
    return bool(_MONEY_MOVE_RE.search(str(res.get("action", "") or "")))


def _band_labels(fw: dict) -> list[str]:
    return [str(b.get("label", "")).strip() for b in (fw.get("bands") or []) if str(b.get("label", "")).strip()]


def classify_band(policy: dict, fw: dict | None = None) -> dict:
    """Best-effort map an SOP to a Framework band. An explicit policy.priority that matches a
    framework band wins; otherwise infer from severity signals (moves money / hard-stops the
    partner). Returns {band, action (the framework's mandate for that band), inferred}."""
    fw = fw or get()
    labels = _band_labels(fw)
    up = {b.upper(): b for b in labels}
    explicit = str(policy.get("priority", "") or "").strip().upper()
    band = up.get(explicit, "")
    inferred = not band
    if inferred and labels:
        res = policy.get("resolution", {}) or {}
        blob = json.dumps(policy, ensure_ascii=False).lower()
        money = _moves_money(res)
        hardstop = any(t in blob for t in ("hardstop", "hard stop", "deactivat", "block", "fe id", "fe-id", "codb"))
        band = labels[0] if (money or hardstop) else labels[min(1, len(labels) - 1)]
    action = ""
    for b in (fw.get("bands") or []):
        if str(b.get("label", "")).strip().upper() == band.upper():
            action = str(b.get("action", "") or "")
            break
    return {"band": band, "action": action, "inferred": inferred}


def check_conformance(policy: dict, fw: dict | None = None) -> dict:
    """Score a compiled ExecutablePolicy against the approved Governance Framework's mandates.
    Returns {band, band_action, conformant, findings, mandates}. `conformant` is False only if a
    HIGH-severity mandate is violated (unowned, or unbounded money) — warns/info don't block."""
    fw = fw or get()
    p = policy or {}
    res = p.get("resolution", {}) or {}
    esc = p.get("escalation", {}) or {}
    params = res.get("params", {}) or {}
    moves_money = _moves_money(res)
    cls = classify_band(p, fw)
    acc = fw.get("accountability") or {}
    findings: list[dict] = []

    # 1) Accountable owner — the framework assigns an owner to every problem (accountability.owners_by).
    if not str(esc.get("team", "") or "").strip():
        findings.append({"severity": "high", "rule": "accountability.owner",
                         "message": "No accountable owner/team on the escalation route — the framework mandates "
                                    "every problem has an owner. Set escalation.team."})
    # 2) A money action with no ₹ cap is an unbounded automated money move — a governance red flag.
    if moves_money and res.get("cap_inr") is None:
        findings.append({"severity": "high", "rule": "resolution.cap_inr",
                         "message": "This resolution moves money but declares no ₹ cap. Unbounded automated money "
                                    "actions aren't allowed — set resolution.cap_inr to the ceiling this SOP may auto-clear."})
    # 3) Binding partner right (guardrail) — each conformant SOP names the right that protects the partner.
    if not (p.get("partner_rights") or []):
        findings.append({"severity": "warn", "rule": "partner_rights",
                         "message": "No binding Partner-Constitution right cited. The framework mandates each resolution "
                                    "names the guardrail that protects the partner (e.g. right to a reason, to escalate)."})
    # 4) Idempotency on money moves — a retry must not double-pay.
    if moves_money and not params.get("idempotent"):
        findings.append({"severity": "warn", "rule": "resolution.idempotent",
                         "message": "Money action isn't marked idempotent — a retry/duplicate could double-pay. "
                                    "Set resolution.params.idempotent = true."})
    # 5) Priority band — accountability + SLA hang off the band; confirm it rather than leaving it inferred.
    if cls.get("inferred") and _band_labels(fw):
        findings.append({"severity": "info", "rule": "band",
                         "message": f"No explicit priority band — inferred {cls['band'] or 'none'} from severity signals. "
                                    "Confirm the band so the right owner + SLA apply."})

    conformant = not any(f["severity"] == "high" for f in findings)
    return {
        "band": cls.get("band"), "band_action": cls.get("action"),
        "conformant": conformant, "findings": findings,
        "mandates": {
            "owners_by": acc.get("owners_by", ""), "sla_hours": acc.get("sla_hours", 0),
            "framework": fw.get("name"), "framework_version": fw.get("version"),
            "framework_status": fw.get("status"),
        },
    }


# ── STRUCTURE-FROM-TEXT (the upload brain) ────────────────────────────────────
_SYSTEM = """You are the Governance-Framework Compiler for a partner/seller-support platform.
A team uploads a governance document (how they identify problems, score/prioritise them,
set bands, track metrics, and hold owners accountable). You STRUCTURE that document into a
strict Governance Framework JSON. You do NOT invent policy — infer only what the document
supports, and leave a field empty rather than fabricate. Be precise and conservative."""

_PROMPT = """Convert the following governance document into a Governance Framework. Return
ONLY JSON with EXACTLY this shape (arrays may be empty but MUST be present):
{{
  "name": "<short name of the framework>",
  "objective": "<one-sentence objective>",
  "signal_sources": [ {{"label": "<where a problem shows up>", "examples": ["<source>", ...], "mode": "reactive|proactive"}} ],
  "dimensions": [
    {{ "key": "<snake_case>", "label": "<label>", "description": "<what it measures>",
       "scale_min": <int>, "scale_max": <int>,
       "sub_factors": [ {{"label": "<label>", "scale_min": <int>, "scale_max": <int>, "normalization": "<how it's normalised>", "note": "<optional>"}} ],
       "ladder": [ {{"level": <int>, "label": "<ordinal level label>"}} ]
    }} ],
  "combine": "multiply|weighted_sum",
  "formula": "<the scoring formula in words>",
  "bands": [ {{"label": "<band>", "meaning": "<what it means>", "action": "<what to do>"}} ],
  "band_movement": [ {{"from": "<band>", "to": "<band>", "condition": "<when it moves>"}} ],
  "metrics": [ {{"name": "<metric>", "definition": "<what it measures>", "kind": "behavioural|functional|impact"}} ],
  "accountability": {{"owners_by": "<how owners are assigned>", "sla_hours": <int>, "score_def": "<how the governance score is defined>"}},
  "items": [ {{"name": "<problem/pain-point>", "journey_stage": "<stage>", "priority": "<band>", "impact_type": "<type>", "scale": <number>, "severity": <number>, "recoverability": "<e.g. 1× / 2×>", "index": <number>, "metrics": {{}}, "root_cause": "", "policy_proposed": "", "owner": "<owner>", "timeline": "", "nps_impact": "", "recovery_path": ""}} ]
}}

Rules:
- Best-effort: infer dimensions, sub-factors, the combine rule + formula, bands, band-movement,
  metrics, signal sources, and any items/pain-points the document lists.
- Every dimension needs a key + label; sub_factors and ladder may be empty arrays.
- Leave a field empty ("" or []) rather than inventing content the document does not support.

GOVERNANCE DOCUMENT:
---
{text}
---
"""


def structure_framework_from_text(text: str) -> dict:
    """LLM-structure an uploaded governance document into the Framework JSON above.
    Returns a DRAFT framework for human review — never auto-approves. Robust JSON parse
    with one retry (a stricter re-ask) if the first attempt yields nothing usable.

    Reuses the exact LLM mechanism the SOP compiler uses: for_node("sop_compile") +
    json_mode + _parse_json."""
    provider, model = llm_registry.for_node("sop_compile")
    prompt = _PROMPT.format(text=(text or "").strip())

    def _try(extra: str = "") -> dict:
        res = provider.generate(prompt + extra, model=model, node="sop_compile",
                                system=_SYSTEM, json_mode=True)
        parsed = _parse_json(res.text)
        if isinstance(parsed, list) and parsed:   # some runs wrap the object in an array
            parsed = parsed[0]
        return parsed if isinstance(parsed, dict) else {}

    parsed = _try()
    if not parsed or not (parsed.get("dimensions") or parsed.get("bands")):
        # one retry with a stricter instruction (parity with the robust-parse pattern)
        parsed = _try("\n\nReturn STRICT valid JSON only — no prose, no markdown fences.")

    fw = _coerce(parsed)
    # a structured upload is always a DRAFT for review; it slots in at the current version
    current = _load() or _seed()
    fw["version"] = int(current.get("version", 1) or 1)
    fw["status"] = "draft"
    fw["updated_at"] = _now()
    if not fw.get("note"):
        fw["note"] = "Structured from an uploaded document by the machine — review before approving."
    return fw
