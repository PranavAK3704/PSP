"""Stage 2 — the channel qualification gate. A first-class deliverable, not an allowlist.

── WHAT THIS EXISTS TO PREVENT ───────────────────────────────────────────────────────────────
`pilot_support_ams` (C0AKEL49PEF) is in the export and is NOT an escalation channel — it is the
support team's own internal channel: sick-leave requests, daily Kapture closure summaries,
product announcements. Only ~11% of its top-level messages carry any identifier, against ~56%
in firefighters. Pointed at it, this pipeline would raise support tickets for people requesting
a day off.

A hardcoded allowlist would prevent that once. A gate prevents it every time a channel is
added — which is the actual risk, because WhatsApp and #valmo-mm-am-escalations are next.

── A CHANNEL IS EXCLUDED WITH THE NUMBER ATTACHED ────────────────────────────────────────────
Never "excluded because it is not an escalation channel". Always "excluded: identifier rate
11.2% against a threshold of 30%". The number is what makes the decision reviewable, and what
makes it possible to be wrong about it later on purpose.

── THE SAMPLED ESTIMATE IS THE ONLY LLM CALL BEFORE STAGE 6 ──────────────────────────────────
`sampled_issue_share` asks a model, over a SAMPLE, what fraction of top-level messages are
genuinely partner/hub issues. It is the one judgement no regex can make, and it is sampled
rather than exhaustive precisely because it is a sanity check on a channel, not a per-message
label. With no LLM configured the gate still runs and reports every deterministic metric, and
the field is None — a channel is never silently qualified on a missing number.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import store

CONFIG = Path(__file__).resolve().parents[2] / "config" / "channels.yaml"

IDENTIFYING = ("dc_code", "kapture_id", "waybill", "mobile", "pilot_id", "email")


def load_config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def run(run_id: str, *, con: sqlite3.Connection | None = None,
        config_path: Path = CONFIG, sampler=None) -> dict:
    """Score every channel present in the store. `sampler(channel_id, texts) -> float|None`
    supplies the Claude-estimated issue share; omitted means the field stays None."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    own = con is None
    con = con or store.connect()
    try:
        cfg = load_config(config_path)
        thr = cfg.get("thresholds") or {}
        threshold = float(thr.get("identifier_rate", 0.30))
        min_sample = int(thr.get("min_sample", 30))
        declared = {c["id"]: c for c in (cfg.get("channels") or [])}
        sample_n = int((cfg.get("thresholds") or {}).get("sample_size", 30))

        store.reset_stage(con, "qualify", run_id)
        rows, report = [], {}

        for ch in con.execute("SELECT DISTINCT channel_id, channel_name FROM messages"):
            cid, cname = ch["channel_id"], ch["channel_name"]
            total = con.execute("SELECT COUNT(*) FROM messages WHERE channel_id=?",
                                (cid,)).fetchone()[0]
            top = con.execute(
                "SELECT m.channel_id, m.message_id, m.text, m.reply_count, m.author_id "
                "FROM messages m LEFT JOIN message_flags f ON f.run_id=? "
                "  AND f.channel_id=m.channel_id AND f.message_id=m.message_id "
                "WHERE m.channel_id=? AND m.thread_ref IS NULL "
                "  AND COALESCE(f.gated,0)=0", (run_id, cid)).fetchall()

            with_id = 0
            for m in top:
                n = con.execute(
                    "SELECT COUNT(*) FROM entities WHERE run_id=? AND channel_id=? "
                    "AND message_id=? AND kind IN (%s)" % ",".join("?" * len(IDENTIFYING)),
                    (run_id, cid, m["message_id"], *IDENTIFYING)).fetchone()[0]
                with_id += bool(n)
            id_rate = round(with_id / len(top), 4) if top else 0.0

            weather = con.execute(
                "SELECT COALESCE(SUM(f.informational=1),0) FROM messages m "
                "JOIN message_flags f USING(channel_id, message_id) "
                "WHERE f.run_id=? AND m.channel_id=? AND m.thread_ref IS NULL "
                "AND f.gated=0", (run_id, cid)).fetchone()[0]
            weather_share = round(weather / len(top), 4) if top else 0.0

            counts = sorted(m["reply_count"] for m in top)

            def pctl(p):
                return counts[min(int(len(counts) * p), len(counts) - 1)] if counts else 0

            authors: dict[str, int] = {}
            for m in top:
                authors[m["author_id"]] = authors.get(m["author_id"], 0) + 1
            top_authors = sorted(authors.items(), key=lambda kv: -kv[1])[:5]

            sampled = None
            if sampler is not None and top:
                sampled = sampler(cid, [m["text"] for m in top[:sample_n]])

            decl = declared.get(cid)
            in_scope = bool(decl and decl.get("in_scope"))
            if decl is None:
                reason = (f"not declared in {config_path.name}: identifier rate "
                          f"{id_rate:.1%} on {len(top)} substantive top-level messages "
                          f"(threshold {threshold:.0%}). Add it explicitly to opt in.")
            elif in_scope and len(top) < min_sample:
                # Not enough evidence to overrule a human. An identifier rate over a handful of
                # messages is noise, and excluding on it silently drops every real issue in the
                # channel — which is exactly what happened the first time this ran live.
                reason = (f"{decl.get('reason', '')} — insufficient_sample: only {len(top)} "
                          f"substantive top-level messages, below the {min_sample} needed to "
                          f"judge an identifier rate. The declaration stands "
                          f"(rate so far {id_rate:.1%})")
            elif in_scope and id_rate < threshold:
                in_scope = False
                reason = (f"declared in scope BUT identifier rate {id_rate:.1%} is below the "
                          f"{threshold:.0%} threshold on {len(top)} messages "
                          f"(>= {min_sample} sampled) — the gate overrides the declaration")
            else:
                reason = (f"{decl.get('reason', '(no reason given)')} — identifier rate "
                          f"{id_rate:.1%} on {len(top)} substantive top-level messages")

            rows.append((run_id, cid, cname, total, len(top), id_rate, weather_share,
                         float(pctl(0.5)), float(pctl(0.9)), json.dumps(top_authors),
                         sampled, len(top[:sample_n]) if sampler else 0, threshold,
                         1 if in_scope else 0, reason,
                         datetime.now(timezone.utc).isoformat(timespec="seconds")))
            report[cname or cid] = {
                "total_records": total, "substantive_toplevel": len(top),
                "identifier_rate": id_rate, "weather_share": weather_share,
                "reply_count_p50": pctl(0.5), "reply_count_p90": pctl(0.9),
                "top_authors": top_authors, "sampled_issue_share": sampled,
                "in_scope": in_scope, "reason": reason,
            }

        con.executemany(
            "INSERT OR REPLACE INTO channel_qualification (run_id, channel_id, channel_name, "
            "total_records, substantive_toplevel, identifier_rate, weather_share, "
            "reply_count_p50, reply_count_p90, top_authors_json, sampled_issue_share, "
            "sample_n, threshold, in_scope, reason, computed_at) "
            "VALUES (" + ",".join("?" * 16) + ")", rows)
        con.execute(
            "INSERT OR REPLACE INTO stage_runs (run_id, stage, started_at, finished_at, "
            "rows_in, rows_out, llm_calls, cost_usd, config_hash, status) "
            "VALUES (?,?,?,?,?,?,?,0.0,?,'ok')",
            (run_id, "qualify", started,
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             len(rows), sum(r[13] for r in rows), 1 if sampler else 0, str(config_path)))
        con.commit()
        return {"channels": report, "threshold": threshold, "min_sample": min_sample,
                "in_scope": [k for k, v in report.items() if v["in_scope"]],
                "excluded": {k: v["reason"] for k, v in report.items() if not v["in_scope"]},
                "sampled_issue_share_available": sampler is not None}
    finally:
        if own:
            con.close()
