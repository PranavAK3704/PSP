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
    # Prefer the explicit disposition CATEGORY carried on the chunk (from a compiled SOP) — that
    # is the authored taxonomy. Fall back to the lexical heuristic for legacy/corpus chunks.
    disp = (chunk.get("disposition") or "").strip()
    if disp:
        return disp
    text = (chunk.get("title", "") + " " + chunk.get("text", "")).lower()
    src = chunk.get("id", "")
    if "hardstop" in text or "hardstop" in src or "loss" in text or "debit" in text or "reversal" in text:
        return "hardstop_loss"
    if "cod" in text or "cash" in text or "pendency" in text or "deposit" in text:
        return "cod_pendency"
    return chunk.get("queue", "general").strip().lower().replace(" ", "_") or "general"


def catalogue() -> list[dict]:
    """All active dispositions — the unified taxonomy: curated executable policies PLUS the
    dispositions the authored/seeded SOP library serves (each with its SOP count) — with live
    stats. Merged by disposition key so a category appearing in both is one entry."""
    st = concern_log.stats().get("by_disposition", {})
    out: dict[str, dict] = {}
    for p in pol.all_policies():
        d = p["disposition"]
        out[d] = {
            "disposition": d, "policy_id": p["id"], "version": p["version"],
            "action": p["resolution"]["action"], "cap_inr": p["resolution"].get("cap_inr"),
            "trigger_keywords": p["trigger"].get("keywords", []),
            "volume": st.get(d, 0), "status": "active", "sop_count": 0,
        }
    # merge dispositions served by the SOP library (many SOPs → one disposition)
    try:
        from ..kt import engine as _kt
        counts: dict[str, int] = {}
        for k in _kt.all_kt():
            if k.get("compiled_sop"):
                d = (k.get("policy") or {}).get("disposition") or ""
                if d:
                    counts[d] = counts.get(d, 0) + 1
        for d, n in counts.items():
            if d in out:
                out[d]["sop_count"] = n
            else:
                out[d] = {"disposition": d, "policy_id": None, "version": "sop", "action": "",
                          "cap_inr": None, "trigger_keywords": [], "volume": st.get(d, 0),
                          "status": "active", "sop_count": n}
    except Exception:  # noqa: BLE001
        pass
    return list(out.values())
