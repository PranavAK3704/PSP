"""Stage 5 — assign messages to issues. NO LLM CALLS, NO NETWORK.

Strict priority: thread, then entity join, then unassigned. Every assignment records the rule
that made it, a confidence, and a human-readable reason, including the ones that fired no rule
at all — "why did this message land here" has to be answerable for every row, not just the
interesting ones.

── THE BIAS IS TOWARDS OVER-SPLITTING, AND IT IS DELIBERATE ──────────────────────────────────
A wrong merge sends the wrong DC the wrong answer and contaminates two issues at once. A wrong
split leaves two rows a human can join in the review sheet. Those are not symmetric, so
everything ambiguous splits.

The concrete expression of that is which tokens are allowed to join. See config/grouping.yaml:
a DC code identifies a PLACE, not an incident, and joining on it merges every issue a hub
raised in a week into whichever one came first. It is off by default and `evaluate` scores
both settings, so the choice is a measured number instead of a preference.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import store

CONFIG = Path(__file__).resolve().parents[2] / "config" / "grouping.yaml"


def load_config(path: Path = CONFIG) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _issue_id(channel_id: str, message_id: str) -> str:
    """An issue is named after its anchor. Deterministic, so a re-run produces the same ids and
    a golden label file stays valid across runs."""
    return f"ISS-{channel_id}-{message_id}"


def run(run_id: str, *, con: sqlite3.Connection | None = None,
        config_path: Path = CONFIG, join_weak: bool | None = None) -> dict:
    """Group every non-gated message. Idempotent.

    `join_weak=True` additionally joins on the tokens in `join_on_weak` (a DC code by default).
    `evaluate` calls both ways to price the difference.
    """
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    own = con is None
    con = con or store.connect()
    try:
        cfg = load_config(config_path)
        window = float(cfg.get("window_days", 7)) * 86400
        strong = list(cfg.get("join_on") or [])
        weak = list(cfg.get("join_on_weak") or []) if join_weak else []
        conf = cfg.get("confidence") or {}

        store.reset_stage(con, "group", run_id)

        # Non-gated messages in timestamp order. Order matters: an issue can only be joined to
        # after its anchor exists, which is what keeps the result deterministic.
        # THE QUALIFICATION GATE IS ENFORCED HERE, not merely reported. Without this filter
        # stage 2 is decorative: pilot_support_ams scores 11% and is excluded, and its sick-leave
        # messages still become issues. A channel that has been excluded contributes NOTHING
        # downstream.
        #
        # If stage 2 has not run for this run_id there is no qualification to enforce, and every
        # channel is grouped — an unqualified run is visibly unfiltered rather than silently
        # empty.
        qualified = {r[0] for r in con.execute(
            "SELECT channel_id FROM channel_qualification WHERE run_id = ? AND in_scope = 1",
            (run_id,))}
        scored = con.execute(
            "SELECT COUNT(*) FROM channel_qualification WHERE run_id = ?", (run_id,)
        ).fetchone()[0]

        scope_sql, scope_args = "", []
        if scored:
            if not qualified:
                scope_sql = " AND 1 = 0"
            else:
                scope_sql = " AND m.channel_id IN (%s)" % ",".join("?" * len(qualified))
                scope_args = sorted(qualified)

        # POSITIVE EVIDENCE gates which messages may ANCHOR an issue. A message that names
        # nothing we operate does not become a ticket just because no rejection rule matched it
        # — that default is what turned "can we go to play arena?" into one.
        #
        # `issue` and `weak` may anchor. `orphan` may not, but is NOT discarded: it lands
        # unassigned and goes to adjudication, because a bare "any update ??" is the case a
        # human most needs to see. `not_an_issue` is excluded entirely.
        #
        # If the evidence stage has not run there is nothing to enforce and every message may
        # anchor — an ungated run is visibly ungated rather than silently empty, the same
        # posture the qualification gate takes.
        ev_scored = con.execute(
            "SELECT COUNT(*) FROM evidence WHERE run_id = ?", (run_id,)).fetchone()[0]
        ev = {}
        if ev_scored:
            ev = {(r["channel_id"], r["message_id"]): r["decision"] for r in con.execute(
                "SELECT channel_id, message_id, decision FROM evidence WHERE run_id = ?",
                (run_id,))}

        msgs = con.execute(
            "SELECT m.channel_id, m.message_id, m.ts_epoch, m.thread_ref, m.author_id "
            "FROM messages m LEFT JOIN message_flags f "
            "  ON f.channel_id = m.channel_id AND f.message_id = m.message_id "
            " AND f.run_id = ? "
            "WHERE COALESCE(f.gated, 0) = 0" + scope_sql +
            " ORDER BY m.ts_epoch", (run_id, *scope_args)).fetchall()
        if ev:
            msgs = [m for m in msgs
                    if ev.get((m["channel_id"], m["message_id"]), "issue") != "not_an_issue"]

        tokens: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for r in con.execute(
                "SELECT channel_id, message_id, kind, value FROM entities WHERE run_id = ?",
                (run_id,)):
            tokens.setdefault((r["channel_id"], r["message_id"]), set()).add(
                (r["kind"], r["value"]))

        # anchor key -> issue_id, and the tokens/ts that issue currently carries.
        anchor_of: dict[tuple[str, str], str] = {}
        issue_tokens: dict[str, set[tuple[str, str]]] = {}
        issue_ts: dict[str, float] = {}
        rows, by_rule = [], {}

        for m in msgs:
            key = (m["channel_id"], m["message_id"])
            mine = tokens.get(key, set())
            issue_id = rule = reason = None
            confidence = 0.0

            # ── 1. thread ───────────────────────────────────────────────────────────────────
            if m["thread_ref"]:
                parent = (m["channel_id"], m["thread_ref"])
                if parent in anchor_of:
                    issue_id = anchor_of[parent]
                    rule, confidence = "thread_ref", float(conf.get("thread_ref", 1.0))
                    reason = f"thread_ref -> anchor {m['thread_ref']}"

            # ── 2. entity join, inside the window ───────────────────────────────────────────
            if issue_id is None and mine:
                for kinds, cname, label in (
                        (strong, "entity_join_strong", "strong"),
                        (weak, "entity_join_weak", "weak")):
                    if not kinds:
                        continue
                    cand = {(k, v) for k, v in mine if k in kinds}
                    if not cand:
                        continue
                    best = None
                    for iid, itoks in issue_tokens.items():
                        shared = cand & itoks
                        if shared and abs(m["ts_epoch"] - issue_ts[iid]) <= window:
                            # most recent wins — the nearest open issue, not the oldest
                            if best is None or issue_ts[iid] > issue_ts[best[0]]:
                                best = (iid, shared)
                    if best:
                        issue_id, shared = best
                        rule = f"entity_join_{label}"
                        confidence = float(conf.get(cname, 0.5))
                        reason = ("shares " + ", ".join(f"{k}={v}" for k, v in sorted(shared))
                                  + f" with {issue_id} within {cfg.get('window_days', 7)}d")
                        break

            # ── 3. a new issue, or unassigned ───────────────────────────────────────────────
            if issue_id is None:
                if m["thread_ref"]:
                    # A reply whose parent was gated or is absent: an orphan. NOT a new issue —
                    # stage 6 decides where it belongs, because guessing here would either
                    # invent an issue out of an "any update ??" or bolt it onto the wrong one.
                    rule, reason = "unassigned", "reply whose parent is gated or not in corpus"
                elif ev.get(key) == "orphan":
                    # No evidence and no parent — the context lives in a message we never
                    # linked. Handing it to stage 6 is right; inventing an issue for it is not.
                    rule, reason = "unassigned", "bare follow-up with no positive evidence"
                else:
                    issue_id = _issue_id(*key)
                    rule = "new_issue"
                    confidence = float(conf.get("thread_ref", 1.0))
                    reason = "top-level message with no prior match — new issue anchored here"
                    anchor_of[key] = issue_id
                    issue_tokens[issue_id] = set(mine)
                    issue_ts[issue_id] = m["ts_epoch"]

            if issue_id and rule != "new_issue":
                # A joined message contributes its tokens, so a thread can grow the identifier
                # set an issue is known by. It does NOT move the window anchor.
                issue_tokens.setdefault(issue_id, set()).update(mine)

            rows.append((run_id, m["channel_id"], m["message_id"], issue_id, rule,
                         confidence, reason,
                         datetime.now(timezone.utc).isoformat(timespec="seconds")))
            by_rule[rule] = by_rule.get(rule, 0) + 1

        con.executemany(
            "INSERT OR REPLACE INTO assignments (run_id, channel_id, message_id, issue_id, "
            "rule, confidence, reason, assigned_at) VALUES (?,?,?,?,?,?,?,?)", rows)
        con.commit()

        threshold = float(cfg.get("adjudicate_below", 0.5))
        to_adjudicate = con.execute(
            "SELECT COUNT(*) FROM assignments WHERE run_id = ? "
            "AND (issue_id IS NULL OR confidence <= ?)", (run_id, threshold)).fetchone()[0]

        stats = {
            "messages": len(msgs),
            "channels_in_scope": sorted(qualified) if scored else "qualify not run",
            "excluded_no_evidence": sum(1 for v in ev.values() if v == "not_an_issue"),
            "excluded_by_qualification": con.execute(
                "SELECT COUNT(*) FROM messages WHERE channel_id IN "
                "(SELECT channel_id FROM channel_qualification WHERE run_id=? AND in_scope=0)",
                (run_id,)).fetchone()[0],
            "issues": len(issue_tokens),
            "by_rule": by_rule,
            "join_weak_enabled": bool(weak),
            "unassigned": by_rule.get("unassigned", 0),
            "handed_to_stage_6": to_adjudicate,
        }
        con.execute(
            "INSERT OR REPLACE INTO stage_runs (run_id, stage, started_at, finished_at, "
            "rows_in, rows_out, llm_calls, cost_usd, config_hash, status) "
            "VALUES (?,?,?,?,?,?,0,0.0,?,'ok')",
            (run_id, "group", started, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             len(msgs), len(issue_tokens),
             f"window={cfg.get('window_days')},weak={bool(weak)}"))
        con.commit()
        return stats
    finally:
        if own:
            con.close()
