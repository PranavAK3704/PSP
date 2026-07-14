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
from pathlib import Path

from ..llm import registry as llm_registry
from ..llm.gemini_provider import _parse_json
from ..state_paths import state_path

# MUTABLE authored content → durable state dir (survives redeploys); default backend/data.
_STORE = Path(state_path("kt_queue.json"))

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


def _load() -> list[dict]:
    if _STORE.exists():
        try:
            return json.loads(_STORE.read_text())
        except Exception:  # noqa: BLE001
            return []
    return []


def _save(items: list[dict]) -> None:
    _STORE.write_text(json.dumps(items, indent=1))


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
