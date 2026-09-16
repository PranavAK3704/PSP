"""Disposition matching — nearest labelled exemplar, or NOVEL. NO LLM, NO NETWORK.

This is the tier that answers "what KIND of issue is this?". It is deterministic: the same
message against the same exemplar set produces the same ranking and the same label, every time,
replayable and diffable.

── WHY A SECOND BM25 AND NOT app/knowledge/store.py's ────────────────────────────────────────
That module's BM25 is good and measured, but its index is module-level and bound to the
knowledge corpus — aliases, a stemmer, tag boosts, queue boosts and an SOP-kind boost, all of
which encode knowledge-corpus semantics. Making it take a second corpus means refactoring a
module on the live money path to serve an analytics one.

So this is a focused ~90-line BM25 over disposition exemplars, and the intake module stays
importable with one cross-package dependency. If the two ever need to converge, this file is the
smaller of the pair to delete.

── WHAT "NOVEL" MEANS HERE ───────────────────────────────────────────────────────────────────
Not "no match" — BM25 always ranks something. NOVEL is declared when the best score is below an
absolute floor, or when the top two dispositions are too close to separate. The second test
matters: a confident wrong label is worse than an honest "I don't know", because a wrong label
is invisible downstream while a NOVEL goes to a human.

Both thresholds are config, both are reported, and `evaluate` scores them against held-out data
rather than anyone's intuition.

── THE LABELS UNDERNEATH ARE SILVER ──────────────────────────────────────────────────────────
Exemplars come from kapture_audits.json, whose dispositions were assigned by the engine's own
classifier. So accuracy measured against them is agreement with that classifier, not truth. The
corpus carries `label_provenance` per row and this module refuses to promote a silver row to an
exemplar once gold rows exist for that disposition — see `build_exemplars.py`.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from . import store

_BACKEND = Path(__file__).resolve().parents[2]
EXEMPLARS = _BACKEND / "data" / "intake" / "corpus" / "exemplars.json"
CONFIG = _BACKEND / "config" / "classify.yaml"

_K1, _B = 1.2, 0.6
_WORD = re.compile(r"[a-z0-9]+")

#: Words that carry no topic signal. Kept deliberately short — an over-eager stoplist removes
#: the Hinglish that distinguishes one complaint from another.
_STOP = {
    "the", "a", "an", "is", "am", "are", "was", "were", "be", "been", "to", "of", "in", "on",
    "at", "for", "and", "or", "but", "if", "it", "this", "that", "my", "our", "we", "i", "you",
    "please", "pls", "kindly", "sir", "madam", "team", "hi", "hello", "dear", "thanks",
    "hai", "hain", "ka", "ki", "ke", "ko", "se", "me", "mein", "kar", "karo", "raha", "rahi",
    "gaya", "gayi", "ho", "hu", "hoon", "bhi", "aur", "par", "jo", "kya", "abhi",
}


def _tok(text: str) -> list[str]:
    return [t for t in _WORD.findall((text or "").lower()) if t not in _STOP and len(t) > 1]


def load_config(path: Path = CONFIG) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


class Matcher:
    """A BM25 index over labelled exemplars. Build once, query many times."""

    def __init__(self, exemplars: list[dict], cfg: dict | None = None):
        cfg = cfg or {}
        self.min_score = float(cfg.get("min_score", 3.0))
        self.margin = float(cfg.get("min_margin", 0.15))
        self.top_k = int(cfg.get("top_k", 5))
        self.gold_weight = float(cfg.get("gold_weight", 3.0))

        self.docs = []
        for e in exemplars:
            toks = _tok(e["text"])
            if toks:
                self.docs.append({"disposition": e["disposition"], "tf": Counter(toks),
                                  "len": len(toks), "text": e["text"],
                                  "provenance": e.get("label_provenance", "silver")})
        n = len(self.docs) or 1
        df = Counter()
        for d in self.docs:
            df.update(d["tf"].keys())
        # Standard BM25 IDF. An unseen query term gets the IDF of a term seen once — an unknown
        # word is maximally specific, not free, which matters for Hinglish outside the corpus.
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.default_idf = math.log(1 + (n - 1 + 0.5) / 1.5)
        self.avgdl = sum(d["len"] for d in self.docs) / n
        self.dispositions = sorted({d["disposition"] for d in self.docs})

    def _score_doc(self, q: list[str], d: dict) -> float:
        s, norm = 0.0, _K1 * (1 - _B + _B * d["len"] / (self.avgdl or 1))
        for t in q:
            f = d["tf"].get(t, 0)
            if f:
                s += self.idf.get(t, self.default_idf) * (f * (_K1 + 1)) / (f + norm)
        return s

    def match(self, text: str) -> dict:
        """Return the best disposition, or NOVEL, with everything needed to explain it."""
        q = _tok(text)
        if not q or not self.docs:
            return {"disposition": "NOVEL", "score": 0.0, "runner_up": None, "margin": 0.0,
                    "why": "no scoreable tokens" if not q else "no exemplars loaded",
                    "neighbours": []}

        scored = sorted(((self._score_doc(q, d), d) for d in self.docs),
                        key=lambda x: -x[0])[:self.top_k]
        # Aggregate neighbours by disposition: one strong exemplar should not beat three
        # moderate ones from the same class, which is what a pure top-1 would do.
        #
        # But that sum is also why a human confirmation needs extra weight. A disposition with 80
        # silver exemplars can fill four of five slots while a just-confirmed class holds one, so
        # without `gold_weight` the person's label loses to the machine's labels on volume and
        # the NOVEL queue becomes theatre. With no confirmations recorded every doc is silver and
        # the multiplier is 1.0, so this changes nothing until someone has actually confirmed.
        agg: dict[str, float] = {}
        for s, d in scored:
            w = self.gold_weight if d["provenance"] == "gold" else 1.0
            agg[d["disposition"]] = agg.get(d["disposition"], 0.0) + s * w
        ranked = sorted(agg.items(), key=lambda kv: -kv[1])

        best, best_score = ranked[0]
        runner, runner_score = (ranked[1] if len(ranked) > 1 else (None, 0.0))
        total = best_score + runner_score or 1.0
        margin = (best_score - runner_score) / total

        if best_score < self.min_score:
            return {"disposition": "NOVEL", "score": round(best_score, 2), "runner_up": best,
                    "margin": round(margin, 3),
                    "why": f"best score {best_score:.2f} below floor {self.min_score}",
                    "neighbours": [(d["disposition"], round(s, 2)) for s, d in scored[:3]]}
        if margin < self.margin:
            # Too close to call. A confident wrong label is invisible downstream; a NOVEL is not.
            return {"disposition": "NOVEL", "score": round(best_score, 2), "runner_up": runner,
                    "margin": round(margin, 3),
                    "why": f"{best} and {runner} within {margin:.1%} — too close to separate",
                    "neighbours": [(d["disposition"], round(s, 2)) for s, d in scored[:3]]}
        return {"disposition": best, "score": round(best_score, 2), "runner_up": runner,
                "margin": round(margin, 3), "why": f"nearest exemplars agree on {best}",
                "neighbours": [(d["disposition"], round(s, 2)) for s, d in scored[:3]]}


def load_matcher(path: Path = EXEMPLARS, cfg_path: Path = CONFIG) -> Matcher | None:
    """None when no exemplar file exists — callers must treat that as 'classification is off',
    never as 'everything is NOVEL', which would look like a taxonomy collapse."""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return Matcher(data.get("exemplars") or [], load_config(cfg_path))


def run(run_id: str, *, con: sqlite3.Connection | None = None,
        exemplars: Path = EXEMPLARS, cfg_path: Path = CONFIG) -> dict:
    """Assign a disposition to every issue in the register. Idempotent."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    own = con is None
    con = con or store.connect()
    try:
        m = load_matcher(exemplars, cfg_path)
        if m is None:
            return {"skipped": True, "issues": 0,
                    "_warning": f"no exemplar index at {exemplars} — run "
                                f"scripts/build_exemplars.py. Classification is OFF; issues keep "
                                f"intent=NULL rather than being marked NOVEL."}

        rows = con.execute(
            "SELECT i.issue_id, m.text FROM issues i "
            "JOIN messages m ON m.channel_id=i.anchor_channel_id "
            "               AND m.message_id=i.anchor_message_id "
            "WHERE i.run_id=?", (run_id,)).fetchall()

        by, novel = {}, 0
        for r in rows:
            d = m.match(r["text"])
            by[d["disposition"]] = by.get(d["disposition"], 0) + 1
            novel += d["disposition"] == "NOVEL"
            con.execute(
                "UPDATE issues SET intent=?, intent_source=? WHERE run_id=? AND issue_id=?",
                (d["disposition"], f"bm25:{d['why'][:60]}", run_id, r["issue_id"]))
        con.commit()

        n = len(rows) or 1
        stats = {"issues": len(rows), "by_disposition": by,
                 "novel": novel, "novel_rate": round(novel / n, 4),
                 "exemplars": len(m.docs), "dispositions_known": len(m.dispositions),
                 "_note": "NOVEL is declared on a score floor OR a too-close margin. A "
                          "confident wrong label is invisible downstream; a NOVEL is not."}
        con.execute(
            "INSERT OR REPLACE INTO stage_runs (run_id, stage, started_at, finished_at, "
            "rows_in, rows_out, llm_calls, cost_usd, config_hash, status) "
            "VALUES (?,?,?,?,?,?,0,0.0,?,'ok')",
            (run_id, "classify", started,
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             len(rows), len(rows) - novel, f"exemplars={len(m.docs)}"))
        con.commit()
        return stats
    finally:
        if own:
            con.close()
