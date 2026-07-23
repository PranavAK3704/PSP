"""Domain Blueprints — a domain's stage-0 "brain", stored as editable JSON.

A Blueprint captures how a domain OWNER thinks about a class of concern BEFORE any
SOP or policy is compiled: which signals they read, how those signals resolve to a
canonical key (so the engine never re-asks), what to look up, how to decide, and —
critically — what to ask the captain when a needed key is missing.

This mirrors the KT engine (app/kt/engine.py): submit(draft) → review → approve →
store.reload(), backed by a JSON store (data/blueprints.json). Approved Blueprints
steer the live resolution loop additively (see engine/conversation.py); they never
replace the SOP/policy path.

Three responsibilities live here, exactly like the SOP side (store + compiler + gaps):
  • the JSON store (load/list/get/save/approve/reload + a first-run seed loader)
  • compile_blueprint_streamed — a raw walkthrough → structured Blueprint, streamed
  • detect_gaps — inline gap detection surfaced AT creation (requirement #4)
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from ..llm import registry as llm_registry
from ..llm.gemini_provider import _parse_json
from ..durable_state import durable_path

# MUTABLE authored content → durable state dir (survives redeploys); default backend/data.
_STORE = durable_path("blueprints.json")
_lock = threading.Lock()

# Stable domain keys — aligned with escalate_case's domain enum (engine/tools.py) so an
# approved Blueprint maps 1:1 onto the concern domain the engine already identifies.
# (`cod_cash` per the data model; the engine's tool enum uses `cash_cod` — both accepted
# on read so nothing breaks either way.)
DOMAINS = ["losses", "payments", "fe_id", "cod_cash", "consumables", "orders", "other"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── the approved Losses seed (from the domain owner's own walkthrough) ────────
_SEED_LOSSES = {
    "domain": "losses",
    "label": "Losses & Debits",
    "signals": [
        {"key": "amount_inr", "desc": "the ₹ amount debited", "source": "message"},
        {"key": "payment_cycle", "desc": "the payment cycle the debit fell in", "source": "message"},
        {"key": "hub_code", "desc": "the captain's hub / DC code", "source": "either"},
        {"key": "debit_note_no", "desc": "debit / credit note number", "source": "either"},
        {"key": "awb", "desc": "the disputed shipment's AWB", "source": "message"},
        {"key": "debit_image", "desc": "a screenshot / photo of the debit", "source": "image"},
    ],
    "derivations": [
        {"from": ["hub_code", "debit_note_no"], "to": "awb",
         "how": "look up hub code + debit/credit note number -> AWBs"},
        {"from": ["amount_inr", "payment_cycle"], "to": "awb_candidates",
         "how": "map amount + payment cycle -> candidate AWBs"},
    ],
    "lookups": [
        {"when_have": "awb", "fetch": ["loss_reason", "facility_inscan", "attribution", "cn_flag"],
         "from": "loss_db"},
    ],
    "decision": [
        {"condition": "facility inscan present OR attribution changed OR CN clears the partner",
         "action": "reverse",
         "note": "not the partner's fault -> reverse the debit in-conversation"},
        {"condition": "loss correctly attributed to the partner", "action": "inform_educate",
         "note": "explain honestly + educate what they can do to avoid it"},
        {"condition": "AWB not found / cannot verify in the loss records", "action": "escalate",
         "note": "file to Losses & Debits with everything gathered + honest note"},
    ],
    "ask_if_missing": [
        {"need": "awb",
         "satisfied_by": ["awb", ["hub_code", "debit_note_no"], ["amount_inr", "payment_cycle"]],
         "prompt": "AWB, ya hub code + debit/credit note number bhej dijiye — main loss ka reason "
                   "check kar deta hoon."},
    ],
    "escalation_team": "Losses & Debits",
    "proactive": "Warn the captain on the Captain Panel via monitoring before the debit posts.",
    "status": "approved",
    "contributor": "domain-owner:losses",
    "updated_at": _now(),
}


def _load() -> list[dict]:
    if _STORE.exists():
        try:
            return json.loads(_STORE.read_text())
        except Exception:  # noqa: BLE001
            return []
    return []


def _write(items: list[dict]) -> None:
    _STORE.write_text(json.dumps(items, indent=1))


def _seed_if_empty(items: list[dict]) -> list[dict]:
    """First-run seed: write the approved Losses blueprint if the store is empty."""
    if not items:
        items = [dict(_SEED_LOSSES)]
        _write(items)
    return items


def load() -> list[dict]:
    """All blueprints (seeds Losses on first run)."""
    with _lock:
        return _seed_if_empty(_load())


def list_blueprints() -> list[dict]:
    """All blueprints, newest-updated first, with status."""
    items = load()
    return sorted(items, key=lambda b: b.get("updated_at", ""), reverse=True)


def get(domain: str) -> dict | None:
    if not domain:
        return None
    d = str(domain).strip().lower()
    for b in load():
        if str(b.get("domain", "")).strip().lower() == d:
            return b
    return None


def approved() -> list[dict]:
    return [b for b in load() if b.get("status") == "approved"]


def existing_brain(domain: str | None) -> dict | None:
    """DEDUP guardrail: a compact summary of an already-stored blueprint for the same domain,
    or None. Brains are one-per-domain, so authoring "a new" one for an existing domain is
    really an EDIT/overwrite — the author must be told. Returns {domain, status, label,
    signals_count}."""
    b = get(domain)
    if not b:
        return None
    return {"domain": b.get("domain"), "status": b.get("status", "draft"),
            "label": b.get("label", ""), "signals_count": len(b.get("signals", []) or [])}


def save(blueprint: dict, contributor: str = "author") -> dict:
    """Save / replace a blueprint as a DRAFT (keyed by domain). Mirrors kt.submit → queue.
    Returns the stored draft; caller pairs it with detect_gaps() for the inline gaps."""
    bp = dict(blueprint or {})
    domain = str(bp.get("domain", "")).strip().lower()
    if not domain:
        raise ValueError("blueprint.domain is required")
    bp["domain"] = domain
    bp["status"] = "draft"
    bp["contributor"] = bp.get("contributor") or contributor
    bp["updated_at"] = _now()
    bp.setdefault("label", domain.replace("_", " ").title())
    with _lock:
        items = _seed_if_empty(_load())
        items = [b for b in items if str(b.get("domain", "")).strip().lower() != domain]
        items.append(bp)
        _write(items)
    return bp


def approve(domain: str) -> dict | None:
    """Approve a domain's blueprint → status=approved + store.reload() so the engine
    read-path picks it up live. Mirrors kt.review(approve=True)."""
    d = str(domain or "").strip().lower()
    with _lock:
        items = _seed_if_empty(_load())
        hit = None
        for b in items:
            if str(b.get("domain", "")).strip().lower() == d:
                b["status"] = "approved"
                b["updated_at"] = _now()
                hit = b
        if hit is None:
            return None
        _write(items)
    # keep parity with kt.review: refresh the retrieval corpus caches on approval.
    try:
        from . import store
        store.reload()
    except Exception:  # noqa: BLE001 — a reload hiccup must not fail the approval
        pass
    return hit


# ── INLINE GAP DETECTION (requirement #4 — surfaced AT creation) ─────────────
def detect_gaps(blueprint: dict) -> list[dict]:
    """Given a compiled/edited Blueprint, return the gaps that mean "the machine doesn't
    know what to ask / how to decide". Each gap: {severity, where, message}.

    Rules (per spec):
      • a signal with an empty/unknown source
      • a lookup `when_have` key that no signal or derivation produces
        ("we look up by X but never capture X")
      • a decision branch with an empty/vague condition
      • an ask_if_missing entry with an empty prompt
      • a needed lookup key with NO ask_if_missing fallback when it can't be derived
      • NO escalate branch present (every domain must have a safe fallback)
    """
    bp = blueprint or {}
    gaps: list[dict] = []
    _VALID_SRC = {"message", "profile", "image", "either"}

    signals = bp.get("signals", []) or []
    derivations = bp.get("derivations", []) or []
    lookups = bp.get("lookups", []) or []
    decision = bp.get("decision", []) or []
    ask = bp.get("ask_if_missing", []) or []

    # keys the domain can actually obtain: captured signals + everything a derivation produces
    signal_keys = {str(s.get("key", "")).strip() for s in signals if str(s.get("key", "")).strip()}
    derived_keys = {str(d.get("to", "")).strip() for d in derivations if str(d.get("to", "")).strip()}
    producible = signal_keys | derived_keys
    ask_keys = {str(a.get("need", "")).strip() for a in ask if str(a.get("need", "")).strip()}

    # a signal with empty/unknown source
    for s in signals:
        key = str(s.get("key", "")).strip() or "(unnamed signal)"
        src = str(s.get("source", "")).strip().lower()
        if src not in _VALID_SRC:
            gaps.append({"severity": "warn", "where": f"signals.{key}",
                         "message": f"Signal '{key}' has no clear source "
                                    f"({src or 'blank'}) — say where it comes from "
                                    f"(message / profile / image / either)."})

    # a decision branch with an empty/vague condition
    _VAGUE = {"", "n/a", "na", "tbd", "-", "?", "any", "other", "else", "otherwise"}
    for i, br in enumerate(decision):
        cond = str(br.get("condition", "")).strip()
        if cond.lower() in _VAGUE or len(cond) < 4:
            gaps.append({"severity": "warn", "where": f"decision[{i}]",
                         "message": "Decision branch has an empty / vague condition — "
                                    "state the yes/no test the engine should evaluate."})

    # a lookup when_have key that no signal or derivation produces + missing ask fallback
    for i, lk in enumerate(lookups):
        wh = str(lk.get("when_have", "")).strip()
        if not wh:
            gaps.append({"severity": "warn", "where": f"lookups[{i}]",
                         "message": "Lookup has no 'when_have' key — name the key it fetches by."})
            continue
        derivable = any(str(d.get("to", "")).strip() == wh for d in derivations)
        if wh not in producible:
            gaps.append({"severity": "high", "where": f"lookups.{wh}",
                         "message": f"We look up by '{wh}' but never capture it — add a signal "
                                    f"or a derivation that produces '{wh}'."})
        # a needed lookup key with NO ask_if_missing fallback when it can't be derived
        if not derivable and wh not in ask_keys:
            gaps.append({"severity": "high", "where": f"ask_if_missing.{wh}",
                         "message": f"'{wh}' can't be derived and has no ask_if_missing fallback — "
                                    f"the engine won't know what to ask when it's missing."})

    # an ask_if_missing entry with an empty prompt
    for a in ask:
        need = str(a.get("need", "")).strip() or "(unnamed)"
        if not str(a.get("prompt", "")).strip():
            gaps.append({"severity": "warn", "where": f"ask_if_missing.{need}",
                         "message": f"'{need}' has no prompt — write what to ask the captain "
                                    f"in their own language."})

    # NO escalate branch present — every domain must have a safe fallback
    if not any(str(br.get("action", "")).strip().lower() == "escalate" for br in decision):
        gaps.append({"severity": "high", "where": "decision",
                     "message": "No escalate branch — every domain needs a safe fallback "
                                "(escalate to the owning team when it can't be verified)."})

    return gaps


# ── BLUEPRINT COMPILER (raw walkthrough → structured Blueprint, streamed) ────
_SYSTEM = """You are the Domain-Brain Compiler for Valmo's partner-support resolution engine.
A domain OWNER describes, in plain language, how they think about a class of partner concern:
what they look at, how those clues resolve to one canonical identifier (so the captain is never
re-asked), what they look up, how they decide, and what they ask when something is missing.
You STRUCTURE that walkthrough into a Blueprint JSON. You do NOT resolve any case. Be precise
and conservative; never invent policy. Prompts you write for the captain must be in the captain's
own language (Hinglish is expected) — short and warm."""

_PROMPT = """Convert the following domain walkthrough into a Blueprint. Return ONLY JSON with
EXACTLY this shape (arrays may be empty but must be present):
{{
  "domain": "<stable key — EXACTLY one of: losses | payments | fe_id | cod_cash | consumables | orders | other>",
  "label": "<human label for the domain>",
  "signals":     [ {{"key": "<snake_case>", "desc": "<what it is>", "source": "message|profile|image|either"}} ],
  "derivations": [ {{"from": ["<key>", ...], "to": "<key>", "how": "<how these signals resolve to the key>"}} ],
  "lookups":     [ {{"when_have": "<key>", "fetch": ["<field>", ...], "from": "<data source>"}} ],
  "decision":    [ {{"condition": "<the yes/no test>", "action": "reverse|inform_educate|escalate|respond", "note": "<why>"}} ],
  "ask_if_missing":[ {{"need": "<key>", "satisfied_by": ["<key>", ["<key>","<key>"]], "prompt": "<what to ask the captain, in their language>"}} ],
  "escalation_team": "<owning functional team>",
  "proactive": "<how we could warn the captain before the problem posts, if applicable>"
}}

Rules:
- {domain_rule}
- Capture EVERY clue the owner reads as a signal, with the right source.
- If several clues resolve to ONE canonical key, encode that as a derivation (so we never re-ask).
- Every lookup must fetch by a key that a signal or derivation produces.
- ALWAYS include a safe escalate branch as the fallback.
- ask_if_missing prompts must be short, warm, and in the captain's language.

DOMAIN OWNER'S WALKTHROUGH:
---
{raw_text}
---
"""


def _norm_domain(d: str | None) -> str:
    """Normalize a domain string to the stable enum, else "" (unknown/invalid).
    `cash_cod` (the engine tool's spelling) maps to `cod_cash` (the data-model key)."""
    d = str(d or "").strip().lower()
    if d == "cash_cod":
        d = "cod_cash"
    return d if d in DOMAINS else ""


def compile_blueprint(raw_text: str, domain: str | None = None) -> tuple[dict, dict]:
    """Return (blueprint, meta). Reuses the active LLM via the SAME node the SOP compiler uses.
    If `domain` is given (and valid) it is respected; otherwise the LLM INFERS the best-fit
    domain from the walkthrough (chosen from the stable enum) and that inferred key is used."""
    explicit = _norm_domain(domain)
    domain_rule = (f'The domain is "{explicit}" — set "domain" to exactly that.' if explicit else
                   'INFER the single best-fit domain from the walkthrough and set "domain" to that '
                   'enum value (losses | payments | fe_id | cod_cash | consumables | orders | other).')
    provider, model = llm_registry.for_node("sop_compile")
    res = provider.generate(
        _PROMPT.format(raw_text=(raw_text or "").strip(), domain_rule=domain_rule),
        model=model, node="sop_compile", system=_SYSTEM, json_mode=True,
    )
    bp = _parse_json(res.text)
    if isinstance(bp, list) and bp:   # some runs wrap the object in an array
        bp = bp[0]
    bp = bp or {}
    # Explicit (valid) domain wins; else the LLM's inferred domain (if valid); else safe fallback.
    bp["domain"] = explicit or _norm_domain(bp.get("domain")) or "other"
    bp.setdefault("label", bp["domain"].replace("_", " ").title())
    for k in ("signals", "derivations", "lookups", "decision", "ask_if_missing"):
        bp.setdefault(k, [])
    bp.setdefault("escalation_team", "")
    bp.setdefault("proactive", "")
    meta = {"model": res.model, "provider": provider.name,
            "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
            "latency_ms": res.latency_ms}
    return bp, meta


def compile_blueprint_streamed(raw_text: str, domain: str | None = None):
    """Stream the structuring as stages so the UI can animate the real brain being assembled.
    Yields: understand → signals → derivations → lookups → decision → ask_if_missing → done.
    The final `stage: done` event carries {blueprint, gaps} (gaps surfaced AT creation, req #4).
    Reuses the exact SSE event framing /api/sop/compile uses (stage/label/data + final payload)."""
    yield {"stage": "understand", "label": "Understanding the walkthrough",
           "detail": "Reading the domain owner's walkthrough with the deep-tier model…"}

    bp, meta = compile_blueprint(raw_text, domain)

    def _stage(stage, label, data):
        return {"stage": stage, "label": label, "data": data}

    yield _stage("signals", "Extracting the signals it reads", {"signals": bp.get("signals", [])})
    yield _stage("derivations", "Mapping clues → one canonical key",
                 {"derivations": bp.get("derivations", [])})
    yield _stage("lookups", "Wiring the data lookups", {"lookups": bp.get("lookups", [])})
    yield _stage("decision", "Compiling the decision branches", {"decision": bp.get("decision", [])})
    yield _stage("ask_if_missing", "Writing what to ask when a key is missing",
                 {"ask_if_missing": bp.get("ask_if_missing", []),
                  "escalation_team": bp.get("escalation_team", "")})

    gaps = detect_gaps(bp)
    # DEDUP guardrail: if a blueprint already exists for this (inferred/explicit) domain, tell the
    # author — "a new brain" for an existing domain is really an edit/overwrite (one-per-domain).
    yield {"stage": "done", "label": "Domain Brain compiled",
           "blueprint": bp, "gaps": gaps, "meta": meta,
           "existing_brain": existing_brain(bp.get("domain"))}
