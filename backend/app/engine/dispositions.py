"""Living Dispositions (BRD §4.2) — an emergent, self-maintaining taxonomy.

There are no pre-declared buckets. A disposition is a cluster (here: a theme in
the ingested corpus) plus its Executable Policy plus running statistics from the
Concern Log. A new Concern maps to the nearest disposition within a similarity
threshold, or is NOVEL — which is Continuous Problem Discovery (log it, route to
a human, flag the team to author an SOP).
"""
from __future__ import annotations

from ..knowledge import policies as pol
from ..knowledge import store
from ..ledger import concern_log

# similarity floor below which a Concern is NOVEL (no SOP-backed cluster)
NOVEL_THRESHOLD = 1.5


def locate(intent: str, keywords: list[str]) -> dict:
    """Map an intent to the nearest living disposition, or flag NOVEL (CPD)."""
    hits = store.retrieve(intent, k=5, tags=keywords)
    if not hits or hits[0]["score"] < NOVEL_THRESHOLD:
        return {"disposition": "NOVEL", "novel": True, "score": hits[0]["score"] if hits else 0.0,
                "cpd": {"reason": "No SOP-backed cluster within similarity threshold",
                        "action": "route to human + flag team to author an SOP"},
                "supporting": hits[:3]}
    top = hits[0]
    # derive disposition theme from the top chunk's queue/kind + policy availability
    disposition = _theme_for(top)
    policy = pol.get_policy(disposition)
    return {
        "disposition": disposition,
        "novel": False,
        "score": top["score"],
        "has_policy": policy is not None,
        "policy_id": policy["id"] if policy else None,
        "supporting": hits[:3],
    }


def _theme_for(chunk: dict) -> str:
    text = (chunk.get("title", "") + " " + chunk.get("text", "")).lower()
    src = chunk.get("id", "")
    if "hardstop" in text or "hardstop" in src or "loss" in text or "debit" in text or "reversal" in text:
        return "hardstop_loss"
    if "cod" in text or "cash" in text or "pendency" in text or "deposit" in text:
        return "cod_pendency"
    return chunk.get("queue", "general").strip().lower().replace(" ", "_") or "general"


def catalogue() -> list[dict]:
    """All active dispositions with policy + live stats (for the UI)."""
    st = concern_log.stats().get("by_disposition", {})
    out = []
    for p in pol.all_policies():
        d = p["disposition"]
        out.append({
            "disposition": d, "policy_id": p["id"], "version": p["version"],
            "action": p["resolution"]["action"], "cap_inr": p["resolution"].get("cap_inr"),
            "trigger_keywords": p["trigger"].get("keywords", []),
            "volume": st.get(d, 0), "status": "active",
        })
    return out
