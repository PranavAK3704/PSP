"""SOP required-data gaps — surfaces knowledge the engine can't fully act on because it
doesn't know WHAT TO ASK THE CAPTAIN, so a non-tech author can fill it in the Support Console.

Two sources:
  • corpus gaps — procedures that don't state "required from the captain" (the 97% the audit found)
  • captured gaps — auto_gap KT entries logged when the engine escalated a no-SOP case (with hit_count)

Filling a gap = submitting a nuance (kt.engine.submit_nuance) which, once approved, enters the
retrieval corpus and the engine starts asking the captain for those inputs — no code change.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import store

_KT = Path(__file__).resolve().parents[2] / "data" / "kt_queue.json"
_SCENARIO = re.compile(r"_(HS|SL|CON|FNF|PAY|ORD|COD|INV|SH|RVP)_\d+", re.I)
_HAS_REQ = re.compile(r"required (from|input|inputs|of the (captain|partner))", re.I)
_SKIP_THEMES = {"kt_notes", "domain", ""}


def _is_gap(chunk: dict) -> bool:
    if chunk.get("knowledge_type") != "procedure":
        return False                                   # policies already carry required_inputs
    cid = chunk.get("id", "")
    if _SCENARIO.search(cid):
        return False                                   # scenario handlers inherit the overview's inputs
    if (chunk.get("theme") or "").strip().lower() in _SKIP_THEMES:
        return False                                   # pure reference / domain knowledge
    return not _HAS_REQ.search(chunk.get("text", "") or "")


def detect(limit: int = 60) -> dict:
    """Return the required-data gaps for the Support Console: grouped counts + a fillable list,
    plus the auto-captured gaps from escalations."""
    corpus = store._corpus()
    gaps = [c for c in corpus if _is_gap(c)]
    by_domain: dict[str, int] = {}
    for g in gaps:
        d = (g.get("queue") or g.get("theme") or "other").strip() or "other"
        by_domain[d] = by_domain.get(d, 0) + 1

    sop_gaps = [{"id": g.get("id"), "title": g.get("title", "")[:90],
                 "domain": (g.get("queue") or g.get("theme") or "other"),
                 "snippet": (g.get("text", "") or "")[:200]}
                for g in gaps[:limit]]

    captured = []
    if _KT.exists():
        try:
            for k in json.loads(_KT.read_text()):
                if k.get("auto_gap") and k.get("status") == "pending":
                    st = k.get("structured", {}) or {}
                    captured.append({"id": k["id"], "title": st.get("title", ""),
                                     "domain": st.get("queue", "unknown"),
                                     "hit_count": k.get("hit_count", 1),
                                     "raw_text": k.get("raw_text", "")[:200]})
        except Exception:  # noqa: BLE001
            pass
    captured.sort(key=lambda c: -c.get("hit_count", 1))

    return {
        "total_sop_gaps": len(gaps),
        "by_domain": dict(sorted(by_domain.items(), key=lambda kv: -kv[1])),
        "sop_gaps": sop_gaps,
        "captured_gaps": captured,
    }
