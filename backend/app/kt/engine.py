"""KT engine (revised vision).

Anyone can contribute knowledge — spoken or typed, plus uploads (sheets, images).
The engine STRUCTURES the free-form input the way policies/SOPs are structured
(machine-optimal), then queues it for APPROVAL (permission-based) before it enters
the knowledge base. Approved KT can optionally be compiled straight into an
Executable Policy via the SOP Compiler.

Distinction preserved (product owner): POLICY = rigid partner-support rule we own;
PROCEDURE = functional-team / supply-chain process. The contributor tags which.

Future (documented, not built): learn the SOP-writing pattern → auto-draft SOPs.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from ..llm import registry as llm_registry
from ..llm.gemini_provider import _parse_json
from ..state_paths import state_path
from ..durable_state import durable_path

# MUTABLE authored content → durable state dir (survives redeploys); default backend/data.
_STORE = durable_path("kt_queue.json")
# STATIC baked seed SOPs (from the SOP Redressal Tracker sheet) — shipped with the image.
_SEED_SOPS_FILE = Path(__file__).resolve().parents[2] / "data" / "knowledge" / "seed_sops.json"


def render_captain_reply(p: dict) -> str:
    """The exact captain-facing reply for an SOP, with {link} placeholders filled from policy.links.
    Empty if the SOP has none. Operational specifics (form links) are preserved here, not abstracted."""
    cr = (p or {}).get("captain_reply") or ""
    if not cr:
        return ""
    for k, v in ((p or {}).get("links") or {}).items():
        cr = cr.replace("{" + k + "}", str(v))
    return cr

_SYSTEM = ("You structure raw operational knowledge (spoken/typed, possibly with attached "
           "sheet/image summaries) into a clean, machine-usable knowledge entry for Valmo "
           "partner support. Be precise; do not invent policy.")

_PROMPT = """Structure this contribution into JSON:
{{
  "title": "<short title>",
  "type": "policy|procedure",   // policy = rigid partner-support rule we own; procedure = functional/supply-chain process
  "queue": "<domain e.g. losses_and_debits | payments | cash | consumables | orders>",
  "triggers": ["<phrases that should retrieve this>"],
  "knowledge": "<the canonical, structured statement>",
  "tags": ["..."]
}}

CONTRIBUTOR SAID:
{text}

ATTACHMENTS (summaries, if any):
{attachments}
"""


def _read() -> list[dict]:
    if _STORE.exists():
        try:
            return json.loads(_STORE.read_text())
        except Exception:  # noqa: BLE001
            return []
    return []


def _load() -> list[dict]:
    """Read the store and reconcile it with the baked seed SOPs (matched by stable id):
      • a seed id not in the store   → added,
      • a seed id present but still a pristine seed (flagged `seeded`) whose baked content has
        changed → refreshed in place (so shipping improved/enriched seeds updates the live copy),
      • a seed the team has edited (the edit drops the `seeded` flag) → left untouched,
      • anything the team authored → left untouched.
    Nothing is ever duplicated; user edits always win. Baked in code, so seeds survive an
    ephemeral-state reset."""
    items = _read()
    seeds = {p.get("id"): p for p in _seed_policies()}
    ts = datetime.now(timezone.utc).isoformat()
    out, seen, changed = [], set(), False
    for e in items:
        eid = e.get("id")
        seen.add(eid)
        p = seeds.get(eid)
        if p is not None and e.get("seeded") and e.get("policy") != p:
            out.append(_policy_to_entry(p, ts))   # pristine seed, content changed → refresh
            changed = True
        else:
            out.append(e)                          # user-edited / authored / unchanged seed → keep
    for sid, p in seeds.items():
        if sid not in seen:
            out.append(_policy_to_entry(p, ts))    # new seed → add
            changed = True
    if changed or not items:
        try:
            _save(out)
        except Exception:  # noqa: BLE001
            pass
    return out


def _save(items: list[dict]) -> None:
    _STORE.write_text(json.dumps(items, indent=1))


# ── First-run seed: SOPs already handed to us by the domain owners ────────────
# COD Shortfall (Syed's end-to-end SOP): a compiled ExecutablePolicy, hand-structured
# from the owner's step-by-step doc. Stored as an APPROVED, compiled SOP so it appears
# in the Authored library and the retrieval corpus on first boot.
_COD_SHORTFALL_POLICY = {
    "id": "SOP-COD-SHORTFALL",
    "domain": "cod_cash",
    "disposition": "cod_shortfall",
    "title": "COD Shortfall — Captain Recovery & Rider Reactivation",
    # What the captain is actually told (the bot surfaces this verbatim, then logs the concern).
    # The captain never sees the internal relay chain — just: fill the form, you'll be notified.
    "captain_reply": ("Kindly fill this form with your shortfall details so we can verify and reactivate "
                      "your Rider/FE ID: {form}. I've logged your case — you'll be notified right here the "
                      "moment your ID is reactivated after the shortfall is cleared."),
    "links": {"form": "https://docs.google.com/forms/d/e/1FAIpQLSd5u0HcS7JBBt2F1B6UihNsjIIMN1OReoXFJC4wSlUmRkxelg/viewform"},
    "trigger": {
        "keywords": ["cod shortfall", "short cod", "cod short", "shortfall", "wrongly updated",
                     "paid to cms", "rider deactivated", "rider id deactivated", "fe id blocked",
                     # aligned to real captain ticket vocabulary (FE-ID reactivation is the shortfall consequence)
                     "fe id deactivated", "fe id is deactivated", "reactivate fe account",
                     "reactivate my fe account", "fe account deactivated", "id deactivated",
                     "reactivate fe id", "reactivate", "reactivation", "fe reactivation",
                     "wrongly debited", "wrong debit cod", "short amount deducted", "cod deducted"],
        "preconditions": [
            "A short COD amount was recorded against the captain's pilot/rider for a given date",
            "The Rider/FE ID may have been deactivated because of the shortfall",
        ],
    },
    "required_evidence": ["Shortfall amount", "Partner details", "FE / Rider details",
                          "Captain ID", "Date of incident"],
    "checks": [
        {"description": "Validate the shortfall amount entered in the Central Tracker",
         "source": "cost_ops_tracker"},
        {"description": "Verify partner and FE/Rider details against the tracker entry",
         "source": "cost_ops_tracker"},
        {"description": "Confirm the shortfall case is genuine and not already recovered",
         "source": "cost_ops"},
    ],
    "resolution": {
        "action": ("Tell the captain to fill the shortfall form, log the concern, and notify the captain "
                   "when the Rider/FE ID is reactivated (L3 clears the concern on reactivation)"),
        "cap_inr": None,   # amount = the validated shortfall; no fixed system cap
        "params": {"pay_channel": "ADHOC Adjustment / Auto Pay",
                   "reactivation_owner": "current DAP POC",
                   "recovery_owner": "Cost Ops (Gopal)"},
    },
    "escalation": {
        "team": "Cost Ops (COD shortfall)",
        # Internal relay — captain never sees this; it tells L3 where the concern travels.
        "handover": ("Form → Syed (pull + tracker entry) → Cost Ops validates (2d) → Cost Ops processes "
                     "payment via ADHOC/Auto Pay (1d) → current DAP POC reactivates the Rider/FE ID → "
                     "Cost Ops (Gopal) records recovery. L3 clears the concern once the ID is reactivated; "
                     "the bot then notifies the captain."),
    },
    "partner_rights": [
        "A genuine COD shortfall wrongly attributed to the captain is paid back via ADHOC "
        "adjustment in the upcoming payment cycle",
        "A Rider/FE ID deactivated due to the shortfall is reactivated once payment is processed",
    ],
}


@lru_cache(maxsize=1)
def _load_seed_sops() -> list[dict]:
    """The 64 SOPs parsed from the SOP Redressal Tracker sheet (baked JSON)."""
    try:
        if _SEED_SOPS_FILE.exists():
            data = json.loads(_SEED_SOPS_FILE.read_text())
            return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []
    return []


@lru_cache(maxsize=1)
def _seed_policies() -> tuple:
    """All baked seed policies (COD Shortfall + the tracker SOPs). Cached — the ids are what the
    additive seeder checks against. Returned as a tuple so it's hashable/cacheable."""
    return tuple([_COD_SHORTFALL_POLICY] + list(_load_seed_sops()))


def _policy_to_entry(p: dict, ts: str) -> dict:
    """Turn a baked ExecutablePolicy into an APPROVED, compiled KT entry (retrievable + shown in
    the Knowledge Base, grouped by its domain)."""
    p = p or {}
    trig = p.get("trigger", {}) or {}
    res = p.get("resolution", {}) or {}
    esc = p.get("escalation", {}) or {}
    lines = []
    if p.get("l3_category"):
        lines.append(f"Category: {p['l3_category']}.")
    if p.get("required_evidence"):
        lines.append("Required from the captain: " + ", ".join(p["required_evidence"]) + ".")
    for c in (p.get("checks") or []):
        if c.get("description"):
            lines.append("Step: " + c["description"])
    if res.get("action"):
        cap = f" (cap ₹{res.get('cap_inr')})" if res.get("cap_inr") is not None else ""
        lines.append(f"Resolution: {res['action']}{cap}.")
    cr = render_captain_reply(p)   # the exact captain-facing reply (with links filled in) — must not be dropped
    if cr:
        lines.append("Tell the captain (verbatim, adapt to their language): " + cr)
    for label, url in (p.get("links") or {}).items():
        lines.append(f"Link · {label}: {url}")
    if esc.get("team"):
        lines.append(f"Escalate to {esc['team']}" + (f" — {esc['handover']}" if esc.get("handover") else "") + ".")
    if p.get("priority"):
        lines.append(f"Priority: {p['priority']}.")
    ref = p.get("doc_reference") or {}   # richer detail merged from the Collated SOPs doc
    if ref.get("l1_process"):
        lines.append("L1 process (detailed): " + ref["l1_process"])
    if ref.get("l2_l3_process"):
        lines.append("L2/L3 process: " + ref["l2_l3_process"])
    if ref.get("guardrails"):
        lines.append("Guardrails: " + ref["guardrails"])
    # title = the SOP's scenario NAME; disposition = the concern CATEGORY it serves.
    title = p.get("title") or p.get("disposition") or p.get("id") or "SOP"
    disp = p.get("disposition") or ""
    lines.insert(0, f"Disposition: {disp}." if disp else "")
    knowledge = "\n".join(l for l in lines if l) or title
    src = p.get("source", "")
    contributor = "sop-redressal-tracker" if src else "domain-owner:cost_ops (Syed)"
    return {
        "id": p.get("id") or ("SOP-" + uuid.uuid4().hex[:8].upper()),
        "contributor": contributor, "raw_text": json.dumps(p, ensure_ascii=False),
        "structured": {"title": title, "type": "policy",
                       "queue": p.get("domain") or (esc.get("team") or "general"),
                       "disposition": disp,
                       "triggers": (trig.get("keywords") or []) + [title, disp],
                       "knowledge": knowledge,
                       "tags": ["sop", "compiled"] + ([src] if src else []) + ([disp] if disp else []) + (trig.get("keywords") or [])[:8]},
        "type": "policy", "status": "approved", "compiled_sop": True, "seeded": True,
        "policy": p, "submitted_at": ts, "reviewed_by": contributor, "reviewed_at": ts,
    }


def submit(text: str, contributor: str, attachments: list[str] | None = None) -> dict:
    """Structure a contribution and add it to the approval queue (status=pending)."""
    provider, model = llm_registry.for_node("sop_compile")
    res = provider.generate(
        _PROMPT.format(text=text.strip(), attachments="\n".join(attachments or []) or "(none)"),
        model=model, node="sop_compile", system=_SYSTEM, json_mode=True)
    structured = _parse_json(res.text)
    if isinstance(structured, list) and structured:
        structured = structured[0]
    entry = {
        "id": "KT-" + uuid.uuid4().hex[:8].upper(),
        "contributor": contributor, "raw_text": text,
        "structured": structured, "type": structured.get("type", "procedure"),
        "status": "pending", "submitted_at": datetime.now(timezone.utc).isoformat(),
        "model": res.model,
    }
    items = _load()
    items.append(entry)
    _save(items)
    return entry


def log_gap(intent: str, reason: str, captain_id: str) -> dict:
    """Capture a knowledge gap (NO LLM call) so a human can author the SOP later.
    Surfaces in /api/kt pending like any contribution, tagged auto_gap. De-dupes by
    normalized intent: a repeat gap bumps hit_count instead of spamming the queue."""
    key = " ".join((intent or "").lower().split())
    items = _load()
    for k in items:
        if k.get("auto_gap") and " ".join((k.get("structured", {}).get("title", "")).lower().split()) == key[:60].strip():
            k["hit_count"] = k.get("hit_count", 1) + 1
            _save(items)
            return k
    entry = {
        "id": "KT-" + uuid.uuid4().hex[:8].upper(),
        "contributor": f"auto_gap:{captain_id}", "raw_text": f"GAP: {intent} — {reason}",
        "structured": {"title": intent[:60], "type": "procedure", "queue": "unknown",
                       "triggers": [intent], "knowledge": "", "tags": ["auto_gap", "needs_authoring"]},
        "type": "procedure", "status": "pending", "auto_gap": True, "hit_count": 1,
        "submitted_at": datetime.now(timezone.utc).isoformat(), "model": None,
    }
    items.append(entry)
    _save(items)
    return entry


def submit_nuance(text: str, domain: str, contributor: str, *, sop_ref: str = "",
                  required_inputs: list[str] | None = None, triggers: list[str] | None = None,
                  from_concern_id: str = "", resolves_gap_id: str = "") -> dict:
    """No-LLM structured nuance/correction from a NON-TECH author. Approved → enters the retrieval
    corpus (via store.reload), so the engine follows it with zero code change: the required-inputs
    line makes the agent ask the captain for exactly those, and the rule text steers behaviour.
    `required_inputs` = plain labels ("pickup date", "UTR"); `triggers` = retrieval phrases."""
    req = [r for r in (required_inputs or []) if str(r).strip()]
    body = text.strip()
    if req:
        body += "\nRequired from the captain: " + ", ".join(req) + "."
    title = (text.strip().split("\n")[0][:60]) or (sop_ref or domain or "nuance")
    trig = list(dict.fromkeys((triggers or []) + [domain, sop_ref] + req))  # dedup, drop blanks below
    trig = [t for t in trig if str(t).strip()]
    entry = {
        "id": "KT-" + uuid.uuid4().hex[:8].upper(),
        "contributor": contributor, "raw_text": text,
        "structured": {"title": title, "type": "procedure", "queue": domain or "general",
                       "triggers": trig, "knowledge": body,
                       "tags": [domain, "nuance"] + (["required_input"] if req else ["rule"])},
        "type": "procedure", "status": "pending", "nuance": True,
        "sop_ref": sop_ref, "required_inputs": req,
        "from_concern_id": from_concern_id, "resolves_gap_id": resolves_gap_id,
        "submitted_at": datetime.now(timezone.utc).isoformat(), "model": None,
    }
    items = _load()
    # a nuance that fills a captured auto_gap supersedes it (mark the gap resolved)
    if resolves_gap_id:
        for k in items:
            if k.get("id") == resolves_gap_id:
                k["status"] = "superseded"; k["superseded_by"] = entry["id"]
    items.append(entry)
    _save(items)
    return entry


def pending() -> list[dict]:
    return [k for k in _load() if k["status"] == "pending"]


def all_kt() -> list[dict]:
    return list(reversed(_load()))


def ensure_seeded() -> None:
    """Plant the first-run seed (COD Shortfall SOP) if the store is empty. Safe to call any
    number of times. Called on startup and before any SOP write, so the seed is authoritative
    regardless of which module touches kt_queue.json first."""
    _load()


def review(kt_id: str, approve: bool, reviewer: str) -> dict | None:
    items = _load()
    for k in items:
        if k["id"] == kt_id:
            k["status"] = "approved" if approve else "rejected"
            k["reviewed_by"] = reviewer
            k["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            _save(items)
            # GAP FIX: approved KT enters the retrieval corpus immediately.
            from ..knowledge import store
            store.reload()
            return k
    return None
