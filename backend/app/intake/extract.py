"""Stage 4 — pull identifiers out of message text. NO LLM CALLS, NO NETWORK.

Pure functions over the store, so a regex change is a re-run rather than a bill. The DC/hub
tiers live in `app/engine/algo/entities.py` (extended, not duplicated) so this repo has one
home for hub-code extraction and one measured stoplist; everything else is driven by
`config/entities.yaml`.

── THE REGISTRY IS ABSENT-SAFE, AND LOUD ABOUT IT ────────────────────────────────────────────
If `config/dc_codes.txt` is missing or empty, Tier A still runs — it needs no registry — and
Tier B is SKIPPED. It does NOT fall back to a bare-token or digit-bearing pass: "342 Tids are
coming in Hardstop loss" is word-bounded, three characters and digit-bearing, so a
digit-bearing heuristic extracts 342 with confidence. `run()` reports the number of messages
that WOULD have been Tier B candidates, so the gap is a number rather than silence.

── TYPO RECONCILIATION ───────────────────────────────────────────────────────────────────────
`tier_a_summary()` returns distinct Tier A extractions with counts, and flags near-identical
pairs (Ii3 vs II3) so the list can be reconciled against the ops master when it arrives.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import yaml

from . import store

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
from app.engine.algo.entities import (  # noqa: E402
    dc_tier_b_candidates, extract_dc_tier_a, extract_dc_tier_b,
)

CONFIG = _BACKEND / "config" / "entities.yaml"
DC_CODES = _BACKEND / "config" / "dc_codes.txt"
DC_DENYLIST = _BACKEND / "config" / "dc_denylist.txt"


def load_code_list(path: Path) -> set[str]:
    """One code per line, uppercase. '#' comments and blank lines ignored, and anything after
    the code on a line is ignored too — so a pasted 'CODE,City' or 'CODE  Name' column loads
    without reformatting, which is what the ops master will look like."""
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tok = re.split(r"[,\s;|]", line, maxsplit=1)[0].strip().upper()
        if tok:
            out.add(tok)
    return out


def load_patterns(path: Path = CONFIG) -> dict[str, dict]:
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for kind, cfg in spec.items():
        cfg["_re"] = re.compile(cfg["pattern"])
    return spec


def extract_text(text: str, patterns: dict, registry: set[str],
                 denylist: set[str]) -> list[tuple[str, str, str | None, str]]:
    """Every identifier in one message, as (kind, value, tier, matched_by).

    Tier A runs first and its hits are excluded from Tier B, so a code is attributed to the
    evidence that actually found it rather than being double-counted.
    """
    out: list[tuple[str, str, str | None, str]] = []

    tier_a = extract_dc_tier_a(text, denylist)
    for tok, label in tier_a.items():
        out.append(("dc_code", tok, "A", f"label:{label.strip()}"))

    for tok, why in extract_dc_tier_b(text, registry, denylist,
                                      exclude=set(tier_a)).items():
        out.append(("dc_code", tok, "B", why))

    for kind, cfg in patterns.items():
        grp = cfg.get("group", 0)
        for m in cfg["_re"].finditer(text or ""):
            out.append((kind, m.group(grp), None, f"pattern:{kind}"))
    return out


def run(run_id: str, *, con: sqlite3.Connection | None = None,
        patterns_path: Path = CONFIG, codes_path: Path = DC_CODES,
        denylist_path: Path = DC_DENYLIST) -> dict:
    """Extract over every loaded message. Idempotent: re-running replaces this run's rows."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    own = con is None
    con = con or store.connect()
    try:
        patterns = load_patterns(patterns_path)
        registry = load_code_list(codes_path)
        denylist = load_code_list(denylist_path)

        store.reset_stage(con, "extract", run_id)

        rows, tier_b_candidate_msgs, candidate_tokens = [], 0, set()
        msgs = con.execute("SELECT channel_id, message_id, text FROM messages").fetchall()
        for m in msgs:
            found = extract_text(m["text"] or "", patterns, registry, denylist)
            for kind, value, tier, matched_by in found:
                rows.append((run_id, m["channel_id"], m["message_id"], kind, value,
                             tier, matched_by))
            if not registry:
                cands = dc_tier_b_candidates(
                    m["text"] or "", denylist,
                    exclude=set(extract_dc_tier_a(m["text"] or "", denylist)))
                if cands:
                    tier_b_candidate_msgs += 1
                    candidate_tokens |= cands

        con.executemany(
            "INSERT OR IGNORE INTO entities "
            "(run_id, channel_id, message_id, kind, value, tier, matched_by) "
            "VALUES (?,?,?,?,?,?,?)", rows)
        con.commit()

        by_kind = {k: c for k, c in con.execute(
            "SELECT kind, COUNT(*) FROM entities WHERE run_id = ? GROUP BY kind", (run_id,))}
        by_tier = {t or "-": c for t, c in con.execute(
            "SELECT tier, COUNT(*) FROM entities WHERE run_id = ? AND kind='dc_code' "
            "GROUP BY tier", (run_id,))}

        stats = {
            "messages": len(msgs),
            "rows": len(rows),
            "by_kind": by_kind,
            "dc_by_tier": by_tier,
            "registry_size": len(registry),
            "registry_loaded": bool(registry),
            "tier_b_skipped": not registry,
            "tier_b_candidate_messages": tier_b_candidate_msgs,
            "tier_b_candidate_tokens": sorted(candidate_tokens),
        }
        con.execute(
            "INSERT OR REPLACE INTO stage_runs (run_id, stage, started_at, finished_at, "
            "rows_in, rows_out, llm_calls, cost_usd, config_hash, status) "
            "VALUES (?,?,?,?,?,?,0,0.0,?,?)",
            (run_id, "extract", started,
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             len(msgs), len(rows), f"registry={len(registry)}",
             "tier_b_skipped" if not registry else "ok"))
        con.commit()
        return stats
    finally:
        if own:
            con.close()


def tier_a_summary(con: sqlite3.Connection, run_id: str) -> dict:
    """Distinct Tier A codes with counts, plus likely typo pairs, for reconciling against the
    ops master when it lands. Tier A needs no registry, so these are the codes people actually
    typed next to a DC/Hub label — the best available evidence of what the real list contains."""
    counts = {v: c for v, c in con.execute(
        "SELECT value, COUNT(*) FROM entities WHERE run_id = ? AND kind='dc_code' "
        "AND tier='A' GROUP BY value ORDER BY COUNT(*) DESC", (run_id,))}
    registry = load_code_list(DC_CODES)
    codes = sorted(counts)
    suspects = []
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            if a.upper() != b.upper() and SequenceMatcher(None, a, b).ratio() >= 0.66:
                suspects.append((a, b))
    return {
        "counts": counts,
        "not_in_registry": sorted(c for c in counts if registry and c not in registry),
        "possible_typos": suspects,
    }
