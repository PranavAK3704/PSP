"""Positive-evidence scoring — "is this a partner/ops issue at all?" NO LLM, NO NETWORK.

Every gate before this one is NEGATIVE: it enumerates things to reject. Anything matching no
rejection rule was treated as an issue, which is how "can we go to play arena?" became a ticket.
You cannot enumerate every off-topic sentence a human might type, so the default is inverted
here — a message becomes an issue only when something POSITIVE says so.

── THE ONE RULE THAT DOES THE WORK ───────────────────────────────────────────────────────────
    Evidence requires an IDENTIFIER or an OPS NOUN.
    Grammar and urgency only MULTIPLY evidence that already exists; they never create it.

"can we go to play arena?" is a well-formed request: request grammar, a question mark, first
person plural. So is "can we get the payout released?". The grammar is identical. The difference
is that one of them names something we operate — so nouns and identifiers are the only sources
of evidence, and everything else is a multiplier on a non-zero base.

That is what makes this deterministic rather than a vocabulary arms race against off-topic chat.

── FOUR OUTCOMES, NOT TWO ────────────────────────────────────────────────────────────────────
    issue         — enough evidence; proceeds
    weak          — some evidence, below threshold; KEPT and flagged for review
    orphan        — a bare follow-up ("any update ??") with no evidence. NOT dropped: the brief
                    calls untreaded follow-ups "the real mess", and the context lives in a
                    message we failed to link. Routes to the review queue.
    not_an_issue  — no identifier, no ops noun. The play-arena case.

`weak` exists because the costs are asymmetric: a human glancing at a borderline message is
cheap, and a silently dropped real issue is the failure this whole pipeline exists to prevent.

── THIS IS THE ONE TIER WHERE A REAL ISSUE CAN DIE ───────────────────────────────────────────
So every decision — including every rejection — is written to the `evidence` table with its
score and each component that fired. `intake explain` prints them. The rejections are meant to
be sampled weekly and the threshold tuned against that sample. A threshold set by intuition and
never audited is how this fails quietly.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import store

CONFIG = Path(__file__).resolve().parents[2] / "config" / "evidence.yaml"

#: Slack markup carries no prose and would otherwise donate stray tokens to the lexicons.
_MARKUP = re.compile(r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>|<!subteam\^[A-Z0-9]+(?:\|[^>]*)?>|<[^>]+>")


def load_config(path: Path = CONFIG) -> dict:
    """Load and pre-compile. Every list is coerced to str — YAML 1.1 turns a bare `no`, `yes`,
    `on` or `off` into a boolean, which has already broken this codebase three times."""
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # Devanagari terms live in their own config keys so the list stays readable and its
    # provenance stays visible — it is a small guess, not a measured lexicon — but they score
    # identically, because "पेमेंट" and "payment" name the same thing and a partner should not
    # need to switch script to be heard.
    for key in ("ops_nouns", "problem_markers", "request_markers"):
        extra = spec.get(f"devanagari_{key}") or []
        if extra:
            spec[key] = list(spec.get(key) or []) + list(extra)

    for key in ("ops_nouns", "problem_markers", "request_markers", "urgency_markers",
                "followup_markers"):
        terms = [str(t).lower() for t in (spec.get(key) or [])]
        spec[key] = terms
        # Word-boundary match for alphanumeric terms; literal for punctuation like "??".
        #
        # NOT `\b`, which is defined through `\w` and therefore excludes Devanagari combining
        # marks: `\bपेमेंट\b` matches, but `\bनहीं\b` never does, because नहीं ends in the
        # anusvara ं (category Mn, not a word character) so the trailing boundary can never be
        # satisfied. The effect is silent and selective — Hindi terms ending in a consonant work,
        # ones ending in a matra or anusvara are invisible — so adding Devanagari vocabulary to
        # the config would have appeared to do nothing for half of it, with no error anywhere.
        #
        # The lookarounds below treat any Devanagari codepoint as part of a word, so a term is
        # still matched whole and not as a fragment of a longer one.
        pats = []
        for t in terms:
            pats.append(rf"(?<!{_WORDISH}){re.escape(t)}(?!{_WORDISH})"
                        if t[:1].isalnum() else re.escape(t))
        spec[f"_{key}_re"] = re.compile("|".join(pats), re.I) if pats else None
    return spec


#: What counts as "inside a word" for the boundary test above. `[^\W_]` is any alphanumeric,
#: and the Devanagari block is added explicitly because its combining marks are not alphanumeric.
_WORDISH = r"(?:[^\W_]|[\u0900-\u097F])"

_PUNCT_ONLY = re.compile(r"^[\W_]+$", re.UNICODE)


def _is_punctuation_only(text: str) -> bool:
    """The whole message is punctuation — "?", "??", "...". An orphan, not a rejection."""
    t = (text or "").strip()
    return bool(t) and bool(_PUNCT_ONLY.match(t))


def _hits(rx, text: str) -> list[str]:
    if not rx or not text:
        return []
    seen, out = set(), []
    for m in rx.finditer(text):
        v = m.group(0).lower()
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def score(text: str, entity_kinds: set[str] | None = None, cfg: dict | None = None) -> dict:
    """Score one message. Pure function — no I/O, no state, same input same output."""
    cfg = cfg or load_config()
    w = cfg.get("weights") or {}
    th = cfg.get("thresholds") or {}
    body = _MARKUP.sub(" ", text or "")
    kinds = set(entity_kinds or ())

    ops = _hits(cfg.get("_ops_nouns_re"), body)
    problem = _hits(cfg.get("_problem_markers_re"), body)
    request = _hits(cfg.get("_request_markers_re"), body)
    urgency = _hits(cfg.get("_urgency_markers_re"), body)
    followup = _hits(cfg.get("_followup_markers_re"), body)

    # ── SOURCES OF EVIDENCE. Only these two can make the base non-zero. ──────────────────────
    base = 0.0
    reasons = []
    if kinds:
        base += float(w.get("identifier", 1.0))
        reasons.append(f"identifier:{','.join(sorted(kinds))}")
    if ops:
        # Capped at two so a long message listing many nouns does not out-score a real
        # complaint that names one thing precisely.
        base += float(w.get("ops_noun", 0.6)) * min(len(ops), 2)
        reasons.append(f"ops:{','.join(ops[:3])}")

    # ── MULTIPLIERS. Applied ONLY on a non-zero base — this is the whole design. ─────────────
    total = base
    if base > 0:
        for name, hits, key in (("problem", problem, "problem"),
                                ("request", request, "request"),
                                ("urgency", urgency, "urgency")):
            if hits:
                total += float(w.get(key, 0.0)) * min(len(hits), 2)
                reasons.append(f"{name}:{','.join(hits[:2])}")
    elif request or problem or urgency:
        # Recorded so an audit can see WHY something was rejected despite looking like a
        # sentence about a problem. This is the play-arena line in the log.
        reasons.append("grammar_only:no_identifier_and_no_ops_noun")

    issue_at = float(th.get("issue", 1.0))
    weak_at = float(th.get("weak", 0.5))
    if total >= issue_at:
        decision = "issue"
    elif total >= weak_at:
        decision = "weak"
    elif followup or _is_punctuation_only(body):
        # A bare "?" is an orphan only when it IS the message. As a substring it matched every
        # question ever typed, which made "can we go to play arena?" an orphan instead of a
        # rejection — the exact bug this stage exists to fix.
        decision = "orphan"
        reasons.append(f"followup:{','.join(followup[:2])}" if followup
                       else "followup:punctuation_only")
    else:
        decision = "not_an_issue"

    return {"decision": decision, "score": round(total, 3),
            "ops": ops, "problem": problem, "request": request,
            "urgency": urgency, "followup": followup,
            "reasons": "; ".join(reasons) or "no positive signal"}


def run(run_id: str, *, con: sqlite3.Connection | None = None,
        config_path: Path = CONFIG) -> dict:
    """Score every non-gated message. Idempotent: re-running replaces this run's rows."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    own = con is None
    con = con or store.connect()
    try:
        cfg = load_config(config_path)
        store.reset_stage(con, "evidence", run_id)

        kinds: dict[str, set[str]] = {}
        for r in con.execute("SELECT channel_id, message_id, kind FROM entities WHERE run_id=?",
                             (run_id,)):
            kinds.setdefault(f"{r['channel_id']}\0{r['message_id']}", set()).add(r["kind"])

        rows, by_decision = [], {}
        msgs = con.execute(
            "SELECT m.channel_id, m.message_id, m.text, m.thread_ref "
            "FROM messages m LEFT JOIN message_flags f "
            "  ON f.run_id=? AND f.channel_id=m.channel_id AND f.message_id=m.message_id "
            "WHERE COALESCE(f.gated,0)=0", (run_id,)).fetchall()

        for m in msgs:
            # A threaded reply inherits its parent's issue by rule 1 of grouping, so it is not
            # asked to justify itself — otherwise "will update in 30 mins" would be rejected and
            # the thread would lose its own reply.
            if m["thread_ref"]:
                d = {"decision": "reply", "score": None, "reasons": "thread reply — inherits its "
                     "parent's issue", "ops": [], "problem": [], "request": [], "urgency": [],
                     "followup": []}
            else:
                d = score(m["text"], kinds.get(f"{m['channel_id']}\0{m['message_id']}"), cfg)
            rows.append((run_id, m["channel_id"], m["message_id"], d["decision"], d["score"],
                         d["reasons"], ",".join(d["ops"][:5]), ",".join(d["problem"][:5])))
            by_decision[d["decision"]] = by_decision.get(d["decision"], 0) + 1

        con.executemany(
            "INSERT OR REPLACE INTO evidence (run_id, channel_id, message_id, decision, score, "
            "reasons, ops_hits, problem_hits) VALUES (?,?,?,?,?,?,?,?)", rows)
        con.commit()

        stats = {
            "messages": len(msgs),
            "by_decision": by_decision,
            "rejected": by_decision.get("not_an_issue", 0),
            "orphans": by_decision.get("orphan", 0),
            "weak_kept_for_review": by_decision.get("weak", 0),
            "_note": "Every rejection is stored with its score and components. Sample them "
                     "weekly and tune the threshold against the sample, not against intuition.",
        }
        con.execute(
            "INSERT OR REPLACE INTO stage_runs (run_id, stage, started_at, finished_at, "
            "rows_in, rows_out, llm_calls, cost_usd, config_hash, status) "
            "VALUES (?,?,?,?,?,?,0,0.0,?,'ok')",
            (run_id, "evidence", started,
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             len(msgs), by_decision.get("issue", 0), str(config_path)))
        con.commit()
        return stats
    finally:
        if own:
            con.close()
