"""Knowledge store (BRD Layer 3, §4.5 Self-Structuring Knowledge).

Loads the ingested corpus snapshot and answers retrieval queries. Retrieval here
is a lightweight lexical scorer over titles/tags/text — deterministic, zero
external dependency, fast enough for a live demo. In production this is where
Voyage/BGE embeddings + pgvector plug in (BRD §10, §12); the retrieve() contract
stays the same, so the swap is behind this module.
"""
from __future__ import annotations

import collections
import hashlib
import json
import math
import re
from functools import lru_cache
from pathlib import Path

from ..durable_state import durable_path
from ..lang import FUNCTION_WORDS

_DATA = Path(__file__).resolve().parents[2] / "data" / "knowledge"

#: One shared vocabulary — see app/lang.py for the measurement that forced it. This list was
#: English-only, and the moment IDF arrived that cost 2 points of recall@1: Hinglish function
#: words appear in only the few chunks carrying Hinglish trigger tags, so IDF rated them as
#: highly specific and a chunk with eleven such tags won five golden queries.
_STOP = set(FUNCTION_WORDS)

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


def _token_list(s: str) -> list[str]:
    """Normalised tokens IN ORDER, with repeats kept.

    The set-only version discarded term frequency, which BM25 needs: a chunk that says
    "hardstop" six times is more about hardstops than one that mentions it once.
    """
    out = []
    for w in re.findall(r"[a-z0-9]+", (s or "").lower()):
        if w in _STOP or len(w) <= 1:
            continue
        out.append(_stem(_ALIASES.get(w, w)))
    return out


def _tokens(s: str) -> set[str]:
    return set(_token_list(s))


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
                    "queue": st.get("queue", ""), "disposition": st.get("disposition", ""),
                    "title": st.get("title", k.get("raw_text", "")[:60]),
                    "text": st.get("knowledge", k.get("raw_text", "")),
                    "tags": (st.get("triggers", []) or []) + (st.get("tags", []) or []),
                    "knowledge_type": k.get("type", "procedure"), "source_repo": "kt_engine/approved",
                })
        except Exception:  # noqa: BLE001
            pass
    # ── DEDUPE ──────────────────────────────────────────────────────────────────────────────
    # Measured: 76 of 537 chunks are byte-identical copies of another chunk's text (14% of the
    # corpus), and 93 titles collide — "2.3. Payments" appears 13 times. The ingest merges several
    # source repos plus approved KT, and the same SOP text arrives by more than one route.
    #
    # The cost is not storage, it is RESULT SLOTS: `retrieve(k=3)` was returning the same text
    # twice and burning two thirds of the budget on one answer. Keyed on normalised TEXT rather
    # than title, because the collisions are mostly section headings shared across documents whose
    # bodies genuinely differ — dropping by title would delete real content.
    #
    # First occurrence wins, so the earlier source repo stays authoritative and the order is
    # stable across reloads.
    seen: set[str] = set()
    deduped = []
    for c in chunks:
        body = re.sub(r"\s+", " ", (c.get("text") or "")).strip().lower()
        key = hashlib.sha1(body.encode("utf-8")).hexdigest() if body else ""
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(c)
    chunks = deduped

    for c in chunks:
        toks = _token_list(c.get("title", "") + " " + " ".join(c.get("tags", [])) + " "
                           + c.get("text", ""))
        c["_tf"] = collections.Counter(toks)      # term frequency, for BM25
        c["_len"] = len(toks)                      # document length, for BM25 normalisation
        c["_tok"] = set(toks)
        c["_titletok"] = _tokens(c.get("title", "") + " " + " ".join(c.get("tags", [])))
    return chunks


@lru_cache(maxsize=1)
def _index() -> dict:
    """Corpus statistics BM25 needs: document frequency per term, N, and average length.

    ── WHY THIS EXISTS AT ALL ────────────────────────────────────────────────────────────────
    The previous scorer counted set overlap: every matched term contributed exactly 1.0. Measured
    on this corpus, `captain` appears in 60% of the 537 chunks and `dual` in 1.3% — an 8.5×
    difference in how much each one tells you, weighted identically. The consequence was
    predictable once measured: a chunk containing many COMMON words beat the chunk that was
    actually about the question. "FE didn't close BTS" won three separate golden queries that
    way, including "shortage kis hub pe hua tha", where the correct shortage chunks sat at ranks
    2 and 3.

    IDF is the fix, and it is the whole reason BM25 exists. `ln(1 + (N-df+0.5)/(df+0.5))` is the
    standard form and stays positive even for a term in every document, which the plain
    `ln(N/df)` does not.
    """
    chunks = _corpus()
    df: collections.Counter = collections.Counter()
    for c in chunks:
        for t in c["_tok"]:
            df[t] += 1
    n = max(1, len(chunks))
    total_len = sum(c["_len"] for c in chunks)
    return {
        "df": df,
        "n": n,
        "avgdl": (total_len / n) if n else 1.0,
        "idf": {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()},
    }


def reload() -> None:
    """Drop caches so newly-approved KT / re-ingested corpus is picked up live."""
    _corpus.cache_clear()
    _index.cache_clear()          # MUST clear too — a stale IDF table over a new corpus scores
                                  # every new chunk as if its terms were unseen.


def corpus_stats() -> dict:
    chunks = _corpus()
    by_kind: dict[str, int] = {}
    by_repo: dict[str, int] = {}
    for c in chunks:
        by_kind[c["kind"]] = by_kind.get(c["kind"], 0) + 1
        by_repo[c["source_repo"]] = by_repo.get(c["source_repo"], 0) + 1
    return {"total": len(chunks), "by_kind": by_kind, "by_repo": by_repo}


#: BM25 parameters. k1 bounds how much repetition can help (a term said 20 times is not 20×
#: more relevant than once); b controls length normalisation. 1.2 / 0.6 rather than the textbook
#: 1.2 / 0.75 because these chunks are deliberately scenario-atomic and vary from one line to
#: 2,600 characters — at b=0.75 the long authoritative SOPs were pushed below one-line KT notes.
_K1 = 1.2
_B = 0.6


def retrieve(query: str, k: int = 6, queue: str | None = None, tags: list[str] | None = None) -> list[dict]:
    """Return top-k chunks by BM25, with domain boosts on top.

    BM25 does the lexical work — see `_index()` for why IDF was the missing piece. The boosts
    below are kept because they encode signal no term statistic can know: that a tag is a
    curated trigger, that a hit in the TITLE means the chunk is about the thing rather than
    mentioning it, and that an SOP outranks a note. They are expressed as multiples of the
    query's mean IDF so they stay proportionate — as fixed +1.5 constants they were worth a
    great deal against set-overlap scores of 3-8 and almost nothing against BM25 scores that
    reach 15.
    """
    q_terms = _token_list(query)
    if not q_terms and not tags:
        return []
    idx = _index()
    idf, avgdl = idx["idf"], idx["avgdl"] or 1.0
    # Unseen query terms get the IDF of a term appearing once — an unknown word is maximally
    # specific, not free. Without this, a typo or a Hinglish word outside the corpus silently
    # contributed nothing and the query scored on its filler alone.
    default_idf = math.log(1 + (idx["n"] - 1 + 0.5) / 1.5)
    mean_idf = (sum(idf.get(t, default_idf) for t in set(q_terms)) / len(set(q_terms))) if q_terms else 1.0

    q = set(q_terms)
    want_tags = {t.lower() for t in (tags or [])}
    scored: list[tuple[float, dict]] = []
    for c in _corpus():
        chunk_tags = {t.lower() for t in c.get("tags", [])}
        if not (q & c["_tok"]) and not (want_tags & chunk_tags):
            continue
        tf, dl = c["_tf"], c["_len"] or 1
        norm = _K1 * (1 - _B + _B * dl / avgdl)
        score = 0.0
        for t in q:
            f = tf.get(t, 0)
            if not f:
                continue
            score += idf.get(t, default_idf) * (f * (_K1 + 1)) / (f + norm)
        # ── domain boosts, scaled to this query's mean IDF ──
        score += 1.4 * mean_idf * len(want_tags & chunk_tags)      # curated trigger
        score += 0.9 * mean_idf * len(q & c.get("_titletok", set()))  # about it, not mentioning it
        if queue and c.get("queue") and queue.lower() in c["queue"].lower():
            score += 0.7 * mean_idf
        if c["kind"] in ("sop", "scenario"):
            score += 0.3 * mean_idf                                 # authoritative source
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
            "disposition": c.get("disposition", ""),   # carry the SOP's concern category for routing
            "knowledge_type": c.get("knowledge_type", "procedure"),  # policy | procedure
            "source_repo": c["source_repo"], "score": round(score, 1),
        })
    return out
