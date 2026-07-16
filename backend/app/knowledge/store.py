"""Knowledge store (BRD Layer 3, §4.5 Self-Structuring Knowledge).

Loads the ingested corpus snapshot and answers retrieval queries. Retrieval here
is a lightweight lexical scorer over titles/tags/text — deterministic, zero
external dependency, fast enough for a live demo. In production this is where
Voyage/BGE embeddings + pgvector plug in (BRD §10, §12); the retrieve() contract
stays the same, so the swap is behind this module.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from ..state_paths import state_path
from ..durable_state import durable_path

_DATA = Path(__file__).resolve().parents[2] / "data" / "knowledge"

_STOP = set("the a an of to for is are was were be been in on at by with and or not "
            "my me i you your it this that from as have has".split())

# Vocabulary normalization — map captain phrasings / Hinglish variants to a canonical
# token, applied to BOTH corpus and query so they align. Kept small + high-precision.
# (In production, semantic embeddings replace this.)
_ALIASES = {
    # FE-ID deactivation cluster
    "deactivated": "deactivate", "reactivate": "deactivate", "reactivation": "deactivate",
    "reactivated": "deactivate", "deactivation": "deactivate", "inactive": "deactivate",
    # money terms
    "galat": "wrong", "nuksan": "loss", "paisa": "payment", "paise": "payment",
    "wapas": "reversal", "reverse": "reversal", "reversed": "reversal", "refund": "reversal",
    "pendency": "pending", "parcel": "shipment", "bag": "shipment",
}


def _stem(w: str) -> str:
    # light suffix stripping so variants collide (scan/scans, reverse/reversal-ish, etc).
    for suf in ("ational", "ation", "tion", "ings", "ing", "ers", "ed", "es", "s", "d", "e"):
        if len(w) - len(suf) >= 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _tokens(s: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", (s or "").lower()):
        if w in _STOP or len(w) <= 1:
            continue
        out.add(_stem(_ALIASES.get(w, w)))
    return out


@lru_cache(maxsize=1)
def _corpus() -> list[dict]:
    path = _DATA / "corpus.json"
    chunks = []
    if path.exists():
        chunks = json.loads(path.read_text()).get("chunks", [])
    # GAP FIX: merge APPROVED KT so newly-approved knowledge is instantly retrievable
    # (no re-ingest). Approved KT is already structured (title/triggers/knowledge/type).
    # Read the durable-state kt_queue (same store the KT engine + SOP compiler write) so an
    # approval reliably enters the corpus. Locally $PSP_STATE_DIR is unset → backend/data.
    kt_path = durable_path("kt_queue.json")
    if kt_path.exists():
        try:
            for k in json.loads(kt_path.read_text()):
                if k.get("status") != "approved":
                    continue
                st = k.get("structured", {}) or {}
                chunks.append({
                    "id": k["id"], "kind": "kt", "theme": st.get("queue", ""),
                    "queue": st.get("queue", ""), "title": st.get("title", k.get("raw_text", "")[:60]),
                    "text": st.get("knowledge", k.get("raw_text", "")),
                    "tags": (st.get("triggers", []) or []) + (st.get("tags", []) or []),
                    "knowledge_type": k.get("type", "procedure"), "source_repo": "kt_engine/approved",
                })
        except Exception:  # noqa: BLE001
            pass
    for c in chunks:
        c["_tok"] = _tokens(c.get("title", "") + " " + " ".join(c.get("tags", [])) + " " + c.get("text", ""))
        c["_titletok"] = _tokens(c.get("title", "") + " " + " ".join(c.get("tags", [])))
    return chunks


def reload() -> None:
    """Drop caches so newly-approved KT / re-ingested corpus is picked up live."""
    _corpus.cache_clear()


def corpus_stats() -> dict:
    chunks = _corpus()
    by_kind: dict[str, int] = {}
    by_repo: dict[str, int] = {}
    for c in chunks:
        by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
        by_repo[c["source_repo"]] = by_repo.get(c["source_repo"], 0) + 1
    return {"total": len(chunks), "by_kind": by_kind, "by_repo": by_repo}


def retrieve(query: str, k: int = 6, queue: str | None = None, tags: list[str] | None = None) -> list[dict]:
    """Return top-k chunks by lexical overlap. Tag/queue hits are boosted."""
    q = _tokens(query)
    want_tags = {t.lower() for t in (tags or [])}
    scored: list[tuple[float, dict]] = []
    for c in _corpus():
        overlap = len(q & c["_tok"])
        if overlap == 0 and not (want_tags & {t.lower() for t in c.get("tags", [])}):
            continue
        score = float(overlap)
        chunk_tags = {t.lower() for t in c.get("tags", [])}
        score += 2.0 * len(want_tags & chunk_tags)          # tag match is strong signal
        score += 1.5 * len(q & c.get("_titletok", set()))    # hits in title/tags = the signal layer
        if queue and c.get("queue") and queue.lower() in c["queue"].lower():
            score += 1.5
        if c["kind"] in ("sop", "scenario"):
            score += 0.5                                     # authoritative sources
        # length penalty: a precise scenario-atomic chunk should beat a giant
        # category dump that matches everything weakly.
        tlen = len(c.get("text", ""))
        if tlen > 1400:
            score *= 0.8
        if tlen > 2600:
            score *= 0.75
        scored.append((score, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    # Relevance cutoff: keep only matches close to the best, so we return the SOP(s)
    # that actually map to the query — not a padded top-N of tangential chunks.
    # (In production this is a semantic-similarity threshold on embeddings.)
    top = scored[0][0] if scored else 0.0
    cutoff = max(1.5, top * 0.7)
    scored = [(s, c) for s, c in scored if s >= cutoff]
    out = []
    for score, c in scored[:k]:
        out.append({
            "id": c["id"], "kind": c["kind"], "title": c["title"],
            "text": c["text"][:500], "queue": c.get("queue", ""),
            "knowledge_type": c.get("knowledge_type", "procedure"),  # policy | procedure
            "source_repo": c["source_repo"], "score": round(score, 1),
        })
    return out
