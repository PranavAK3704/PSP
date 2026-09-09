"""Stage 3 — the noise gate and the informational classifier. NO LLM CALLS, NO NETWORK.

Two separate axes, deliberately not one:

  gated         — carries no author intent. Joins, leaves, bare acks, "thanks", "+1", emoji.
  informational — carries real intent, but is not a ticket. Weather and operational callouts.

Collapsing them would be wrong in both directions. A weather callout is not noise: someone
chose to tell the channel that deliveries will slip, and suppressing it as junk loses a third
of lm-ams. But it is not an issue either, and raising a ticket for rain produces a register
nobody trusts.

── THE EXCEPTION THAT MATTERS ────────────────────────────────────────────────────────────────
An empty-text message WITH an attachment is never gated. ~5% of lm-ams records are exactly
that — the issue is entirely inside a screenshot. Any emptiness rule that does not carve this
out deletes real issues while the filtered count looks healthy.

── WHY THE INFORMATIONAL SHARE IS A RANGE ────────────────────────────────────────────────────
The count is sensitive to where the line is drawn, so one number would be false precision:

  strict — the FIRST topic keyword in the message is a weather term. An unambiguous callout.
  loose  — a weather term appears anywhere.
  borderline = loose - strict, and every one is listed by message_id rather than summarised.

The message that forced this reads "Due to festival manpower absenteeism today only 40% FE
reported, and rain since morning is adding to it." It is a manpower message that mentions rain.
Counting it either way silently moves the share by a percentage point or two, so it is reported
as neither.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import store

CONFIG = Path(__file__).resolve().parents[2] / "config" / "noise.yaml"

#: Slack markup that carries no prose: user, channel, subteam and link markup.
_MARKUP = re.compile(r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>|<!(?:channel|here|everyone)>"
                     r"|<!subteam\^[A-Z0-9]+(?:\|[^>]*)?>|<[^>]+>")
#: Everything that is not a letter, digit or space. Emoji fall out here, and so does the
#: punctuation that turns "ok." into something an `exact` rule would otherwise miss.
_NON_PROSE = re.compile(r"[^\w\s]|_", re.UNICODE)


def load_rules(path: Path = CONFIG) -> dict:
    """Load the rules, coercing every `exact` entry to a string.

    YAML 1.1 parses bare `yes`, `no`, `on`, `off`, `y` and `n` as BOOLEANS, so an unquoted
    `yes` in an ack list arrives as Python `True` and the gate dies on `.lower()`. The config
    quotes them, and this coerces as well — the failure is silent-looking (a config edit, a
    crash three stages later) and the fix costs one line.
    """
    spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # Coerce EVERY list in every rule, not just `exact`. This trap has now bitten three times
    # in this file -- once in bare_ack, once in the ack_phrase vocabulary (`yes` -> True, which
    # crashed on .lower()), and it will bite the next person who adds a list. Coercing by shape
    # rather than by field name is the only version that stays fixed.
    for rule in spec.get("rules") or []:
        for k, v in list(rule.items()):
            if isinstance(v, list):
                rule[k] = [str(e) for e in v]
    for k, v in list((spec.get("informational") or {}).items()):
        if isinstance(v, list):
            spec["informational"][k] = [str(e) for e in v]
    return spec


def normalise(text: str) -> str:
    """Lowercase, strip Slack markup, drop punctuation and emoji, collapse whitespace.

    This is what the `exact` rules match against, and it is why they can be short: "Okk!!" and
    "ok" and ":ok:" all normalise to the same string, so the config does not need every spelling
    of every ack.
    """
    t = _MARKUP.sub(" ", text or "")
    t = _NON_PROSE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def is_mention_only(text: str) -> bool:
    """Nothing but mention markup — a bare @-ping with no message."""
    return bool((text or "").strip()) and not _MARKUP.sub(" ", text).strip()


def classify(text: str, subtype: str | None, has_media: bool, rules: dict) -> dict:
    """One message. Returns the gate decision and the informational decision."""
    out = {"gated": False, "gate_rule": None,
           "informational": False, "informational_rule": None, "borderline": False}

    raw = text or ""
    norm = normalise(raw)

    # THE EXCEPTION, checked before anything else so no later rule can override it.
    empty_with_media = not raw.strip() and has_media
    if empty_with_media:
        out["gate_rule"] = "kept:empty_text_with_attachment"
    elif subtype in set(rules.get("subtypes") or []):
        out.update(gated=True, gate_rule=f"subtype:{subtype}")
    else:
        for rule in rules.get("rules") or []:
            name = rule.get("name", "?")
            if norm and norm in {e.lower() for e in (rule.get("exact") or [])}:
                out.update(gated=True, gate_rule=name)
                break
            if rule.get("mention_only") and is_mention_only(raw):
                out.update(gated=True, gate_rule=name)
                break
            if rule.get("emoji_only") and raw.strip() and not norm and not has_media:
                out.update(gated=True, gate_rule=name)
                break
            vocab = rule.get("all_words_in")
            if vocab and norm:
                words = norm.split()
                cap = int(rule.get("max_words", 7))
                if len(words) <= cap and all(w in {v.lower() for v in vocab} for w in words):
                    out.update(gated=True, gate_rule=name)
                    break

    info = rules.get("informational") or {}
    # Weather and announcements are the SAME axis: deliberate operational comms that are not
    # tickets. They are two lists only so the report can say which kind, and so the weather
    # share -- the figure the brief quotes at 20-39% -- stays separately countable.
    weather = [w.lower() for w in (info.get("weather_terms") or [])]
    announce = [a.lower() for a in (info.get("announcement_terms") or [])]
    competing = [c.lower() for c in (info.get("competing_terms") or [])]
    low = raw.lower()

    def first_hit(terms):
        best = None
        for t in terms:
            i = low.find(t)
            if i >= 0 and (best is None or i < best[0]):
                best = (i, t)
        return best

    w, c, a = first_hit(weather), first_hit(competing), first_hit(announce)
    if w:
        out["informational"] = True
        if c and c[0] < w[0]:
            # A non-weather topic is mentioned FIRST — loose only.
            out.update(informational_rule=f"loose:{w[1]}(after {c[1]})", borderline=True)
        else:
            out["informational_rule"] = f"strict:{w[1]}"
    elif a:
        # An announcement marker is a PHRASE, not a word, so it carries its own confidence and
        # does not need the first-topic test weather needs.
        out.update(informational=True, informational_rule=f"announcement:{a[1]}")
    return out


def run(run_id: str, *, con: sqlite3.Connection | None = None,
        config_path: Path = CONFIG) -> dict:
    """Gate every loaded message. Idempotent: re-running replaces this run's flags."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    own = con is None
    con = con or store.connect()
    try:
        rules = load_rules(config_path)
        store.reset_stage(con, "gate", run_id)

        rows, gated_by_rule, borderline_ids = [], {}, []
        strict = loose = 0
        msgs = con.execute(
            "SELECT channel_id, message_id, text, subtype, has_media, thread_ref "
            "FROM messages").fetchall()
        for m in msgs:
            d = classify(m["text"], m["subtype"], bool(m["has_media"]), rules)
            rows.append((run_id, m["channel_id"], m["message_id"],
                         1 if d["gated"] else 0, d["gate_rule"],
                         1 if d["informational"] else 0, d["informational_rule"],
                         1 if d["borderline"] else 0))
            if d["gated"]:
                gated_by_rule[d["gate_rule"]] = gated_by_rule.get(d["gate_rule"], 0) + 1
            if d["informational"]:
                loose += 1
                if d["borderline"]:
                    borderline_ids.append(f"{m['channel_id']}/{m['message_id']}")
                else:
                    strict += 1

        con.executemany(
            "INSERT OR REPLACE INTO message_flags (run_id, channel_id, message_id, gated, "
            "gate_rule, informational, informational_rule, informational_borderline) "
            "VALUES (?,?,?,?,?,?,?,?)", rows)
        con.commit()

        # The informational share is quoted over SUBSTANTIVE TOP-LEVEL messages, which is the
        # denominator the 20-39% figure uses — not over every record, and not over replies.
        top = con.execute(
            "SELECT COUNT(*) FROM messages m JOIN message_flags f USING(channel_id, message_id)"
            " WHERE f.run_id = ? AND m.thread_ref IS NULL AND f.gated = 0", (run_id,)
        ).fetchone()[0]
        top_strict, top_loose = con.execute(
            "SELECT COALESCE(SUM(f.informational = 1 AND f.informational_borderline = 0), 0),"
            "       COALESCE(SUM(f.informational = 1), 0) "
            "FROM messages m JOIN message_flags f USING(channel_id, message_id) "
            "WHERE f.run_id = ? AND m.thread_ref IS NULL AND f.gated = 0", (run_id,)
        ).fetchone()

        stats = {
            "messages": len(msgs),
            "gated": sum(gated_by_rule.values()),
            "gated_by_rule": gated_by_rule,
            "kept_empty_text_with_attachment": sum(
                1 for r in rows if r[4] == "kept:empty_text_with_attachment"),
            "substantive_toplevel": top,
            "informational_strict": top_strict,
            "informational_loose": top_loose,
            "informational_share_range": (
                round(top_strict / top, 4) if top else 0.0,
                round(top_loose / top, 4) if top else 0.0),
            "borderline": borderline_ids,
        }
        con.execute(
            "INSERT OR REPLACE INTO stage_runs (run_id, stage, started_at, finished_at, "
            "rows_in, rows_out, llm_calls, cost_usd, config_hash, status) "
            "VALUES (?,?,?,?,?,?,0,0.0,?,'ok')",
            (run_id, "gate", started, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             len(msgs), stats["gated"], str(config_path)))
        con.commit()
        return stats
    finally:
        if own:
            con.close()
