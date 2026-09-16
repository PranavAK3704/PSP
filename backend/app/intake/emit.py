"""Stage 10 — turn issues into ticket drafts. NO LLM CALLS, NO NETWORK, NO TICKET CREATED.

This is the product: a ticket per conversation, derived from the flow of the conversation
rather than from someone remembering to raise one.

── WHY THIS IS A DRAFT TABLE AND NOT A CLIENT ────────────────────────────────────────────────
Phase 1 creates no tickets and calls no support backend. That constraint is kept literally:
there is exactly one sink implementation here, it writes to a local table, and nothing in this
module imports an HTTP library. What ships is the PAYLOAD and the SEAM — the two things that
have to be right before any sink exists, and the two things that are painful to retrofit.

It also makes the value visible with zero spend: "43 messages in, 23 issues, 19 tickets we
would raise, 4 held back and here is why" is the whole pitch, answerable today.

── THE IDEMPOTENCY KEY IS DERIVED FROM THE SOURCE ────────────────────────────────────────────
`sha256(source_system, channel_id, anchor_message_id)`. Not a uuid, not minted by the sink, not
a row id. The same Slack message yields the same key on every run, on every machine, through
every sink — so a retry, a re-run, or a second operator cannot double-create, and two sinks can
be reconciled against each other later.

This matters because the obvious first sink has none: `ledger/concern_log.append()` is an
unconditional list append with a server-minted `CNC-<uuid8>` and no uniqueness check, and there
is no `external_ref` field anywhere to dedupe on. Supplying the key from this side means a sink
can be made idempotent without the sink changing first.

── NOT EVERYTHING BECOMES A TICKET ───────────────────────────────────────────────────────────
A draft is written for every issue, and the ones that must not be raised carry `suppressed` and
a reason:

  · informational — weather and operational callouts. Deliberate comms, not tickets. Raising
    one produces a register nobody trusts.
  · duplicate — the far side of a cross-channel pair. The MX1 issue was posted in both channels
    47 seconds apart; two tickets on day one is exactly the failure this pipeline exists to
    stop. The duplicate is held back and points at its twin.
  · no_identifier — nothing to act on. A hub name and a complaint with no DC, mobile, waybill
    or ticket id is a conversation, not a workable ticket. Configurable, and off by default,
    because whether it is true depends on how the desk works.

── PORTABILITY ───────────────────────────────────────────────────────────────────────────────
`TicketSink` is the whole contract. A PSP sink, a Kapture sink, a Jira sink and a CSV sink are
all the same three lines. Nothing above this file knows which one is in use, which is what lets
the pipeline outlive any particular ticketing system.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from . import store

#: Entity kinds that make an issue actionable by someone who was not in the conversation.
ACTIONABLE = ("dc_code", "kapture_id", "waybill", "mobile", "pilot_id", "email")

_MARKUP = re.compile(r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>|<!(?:channel|here|everyone)>"
                     r"|<!subteam\^[A-Z0-9]+(?:\|[^>]*)?>|<([^>|]+)(?:\|[^>]*)?>")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class TicketDraft:
    """Everything a sink needs, and nothing that depends on which sink it is."""

    idempotency_key: str
    source_system: str
    source_id: str
    source_permalink: str | None
    title: str
    description: str
    raiser: str | None
    dc_code: str | None
    intent: str | None
    entity_tokens: dict = field(default_factory=dict)
    kapture_ticket_ids: list[str] = field(default_factory=list)
    reply_count: int = 0
    first_response_latency_s: float | None = None
    state: str = "NEW"
    duplicate_of: str | None = None
    suppressed: bool = False
    suppressed_reason: str | None = None
    occurrence_count: int = 1
    occurrences: list = field(default_factory=list)
    first_raised_at: str | None = None
    last_raised_at: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class TicketSink(Protocol):
    """The whole contract between this pipeline and whatever raises tickets.

    A real sink MUST treat `draft.idempotency_key` as the natural key: creating twice with the
    same key returns the first ticket's reference rather than making a second one. If the
    backend cannot do that, the sink stores the mapping itself — that is the sink's job, not
    the pipeline's.
    """

    name: str

    def create(self, draft: TicketDraft) -> str:
        """Create (or find) the ticket and return its external reference."""


class DryRunTicketSink:
    """The only sink in phase 1. Creates nothing, calls nothing, writes nowhere but the store.

    Deliberately not called a "stub": it produces the real payload and the real key, and its
    output is the artefact that shows what the pipeline would raise. The reference it returns is
    marked DRY- so it can never be mistaken for a ticket id in a spreadsheet.
    """

    name = "dry_run"

    def create(self, draft: TicketDraft) -> str:
        return f"DRY-{draft.idempotency_key[:12]}"


def idempotency_key(source_system: str, channel_id: str, message_id: str) -> str:
    """Stable across runs, machines and sinks — because it is a function of the source only."""
    return hashlib.sha256(
        f"{source_system}\0{channel_id}\0{message_id}".encode()).hexdigest()


def _plain(text: str) -> str:
    """Slack markup out, whitespace collapsed. Link markup keeps the URL, not the label."""
    return _WS.sub(" ", _MARKUP.sub(lambda m: m.group(1) or " ", text or "")).strip()


def make_title(text: str, dc_code: str | None, limit: int = 90) -> str:
    """A deterministic one-line title. No model: the raiser already wrote the summary.

    First sentence where there is one, otherwise a clean truncation. The DC code leads when it
    is known, because that is the first thing anyone triaging asks.
    """
    body = _plain(text)
    if not body:
        body = "(no text — see attachment)"
    first = re.split(r"(?<=[.!?])\s+|\n", body, maxsplit=1)[0].strip() or body
    if len(first) > limit:
        cut = first[:limit].rsplit(" ", 1)[0]
        first = (cut or first[:limit]).rstrip(",;:-") + "…"
    return f"[{dc_code}] {first}" if dc_code else first


def make_description(text: str, permalink: str | None, reply_count: int,
                     tokens: dict) -> str:
    """The raiser's own words, then the facts a triager needs. Still no model."""
    lines = [_plain(text) or "(no text — the content is in an attachment)", ""]
    if tokens:
        lines.append("Identifiers found: " + "; ".join(
            f"{k}={', '.join(v)}" for k, v in sorted(tokens.items()) if v))
    lines.append(f"Thread replies: {reply_count}")
    if permalink:
        lines.append(f"Source: {permalink}")
    return "\n".join(lines)


def draft_for(issue: dict, anchor_text: str, *, source_system: str = "slack",
              require_identifier: bool = False,
              occurrences: list | None = None) -> TicketDraft:
    tokens = {k: v for k, v in json.loads(issue.get("entity_tokens_json") or "{}").items() if v}
    kapture = json.loads(issue.get("kapture_ticket_ids_json") or "[]")

    suppressed, reason = False, None
    if issue.get("informational"):
        suppressed, reason = True, (
            "informational — a weather or operational callout, deliberate comms rather than an "
            "issue. Raising it produces a register nobody trusts.")
    elif issue.get("duplicate_of"):
        # COUNTED, not discarded. The twin's ticket carries occurrence_count, and this row
        # says so explicitly -- "duplicate" reads as thrown away, which is what the earlier
        # wording implied and what the recurrence signal cannot afford.
        suppressed, reason = True, (
            f"counted_into {issue['duplicate_of']} — the same issue raised again. That "
            f"ticket's occurrence_count includes this one; nothing is discarded.")
    elif require_identifier and not any(tokens.get(k) for k in ACTIONABLE):
        suppressed, reason = True, (
            "no actionable identifier — no DC code, mobile, waybill or ticket id, so nobody who "
            "was not in the conversation can act on it.")

    return TicketDraft(
        idempotency_key=idempotency_key(source_system, issue["anchor_channel_id"],
                                        issue["anchor_message_id"]),
        source_system=source_system,
        source_id=f"{issue['anchor_channel_id']}/{issue['anchor_message_id']}",
        source_permalink=issue.get("permalink"),
        title=make_title(anchor_text, issue.get("dc_code")),
        description=make_description(anchor_text, issue.get("permalink"),
                                     issue.get("reply_count") or 0, tokens),
        raiser=issue.get("raiser_name") or issue.get("raiser_id"),
        dc_code=issue.get("dc_code"),
        intent=issue.get("intent"),
        entity_tokens=tokens,
        kapture_ticket_ids=kapture,
        reply_count=issue.get("reply_count") or 0,
        first_response_latency_s=issue.get("first_response_latency_s"),
        state=issue.get("state") or "NEW",
        duplicate_of=issue.get("duplicate_of"),
        suppressed=suppressed,
        suppressed_reason=reason,
        occurrence_count=max(1, len(occurrences or [])),
        occurrences=occurrences or [],
        first_raised_at=(occurrences or [{}])[0].get("at") if occurrences else None,
        last_raised_at=(occurrences or [{}])[-1].get("at") if occurrences else None,
    )


def run(run_id: str, *, con: sqlite3.Connection | None = None,
        sink: TicketSink | None = None, require_identifier: bool = False) -> dict:
    """Draft a ticket for every issue. Idempotent, and creates nothing.

    Passing a real sink is how phase 2 turns this on — nothing else changes.
    """
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    own = con is None
    con = con or store.connect()
    try:
        sink = sink or DryRunTicketSink()
        store.reset_stage(con, "emit", run_id)

        issues = con.execute(
            "SELECT i.*, m.text AS anchor_text FROM issues i "
            "JOIN messages m ON m.channel_id = i.anchor_channel_id "
            "               AND m.message_id = i.anchor_message_id "
            "WHERE i.run_id = ? ORDER BY m.ts_epoch", (run_id,)).fetchall()

        # ── OCCURRENCES ─────────────────────────────────────────────────────────────────────
        # An occurrence is a distinct RAISING of an issue, and it arrives two ways:
        #   1. a separate issue that dedupe linked back here (cross-channel, or re-raised)
        #   2. another top-level message that entity-join absorbed into this issue -- which is
        #      the five-months-apart case, now reachable because person-identifying tokens
        #      join over 180 days rather than 7
        # Both are the same event to a human: "they raised this again". Counting only the
        # first would under-report exactly the pattern the register exists to expose.
        linked: dict[str, list[str]] = {}
        for r in con.execute("SELECT issue_id, duplicate_of FROM duplicates WHERE run_id = ?",
                             (run_id,)):
            linked.setdefault(r["duplicate_of"], []).append(r["issue_id"])

        raisings: dict[str, list[dict]] = {}
        for r in con.execute(
                "SELECT a.issue_id, m.channel_id, m.message_id, m.ts_iso, m.permalink, "
                "       m.channel_name "
                "FROM assignments a JOIN messages m USING(channel_id, message_id) "
                "WHERE a.run_id = ? AND a.issue_id IS NOT NULL AND m.thread_ref IS NULL "
                "ORDER BY m.ts_epoch", (run_id,)):
            raisings.setdefault(r["issue_id"], []).append(
                {"channel": r["channel_name"], "source_id":
                 f"{r['channel_id']}/{r['message_id']}", "at": r["ts_iso"],
                 "permalink": r["permalink"]})

        def occurrences_for(issue_id: str) -> list[dict]:
            out = list(raisings.get(issue_id, []))
            for dup in linked.get(issue_id, []):
                out.extend(raisings.get(dup, []))
            return sorted(out, key=lambda o: o["at"] or "")

        if not issues:
            # A silent zero here reads exactly like "nothing qualified", which is a different
            # and much less alarming statement than "stage 7 has not run for this run_id".
            registered = con.execute(
                "SELECT COUNT(*) FROM stage_runs WHERE run_id=? AND stage='register'",
                (run_id,)).fetchone()[0]
            return {"issues": 0, "would_create": 0, "suppressed": 0,
                    "suppressed_by_reason": {}, "distinct_idempotency_keys": 0,
                    "sink": getattr(sink, "name", type(sink).__name__),
                    "tickets_actually_created": 0,
                    "_warning": (f"no issues for run_id {run_id!r} — "
                                 + ("the register is empty for this run"
                                    if registered else
                                    "stage 7 (register) has not been run for it"))}

        rows, by_reason, keys = [], {}, set()
        created = 0
        for r in issues:
            issue = dict(r)
            d = draft_for(issue, issue.pop("anchor_text"),
                          require_identifier=require_identifier,
                          occurrences=occurrences_for(issue["issue_id"]))
            keys.add(d.idempotency_key)
            ref = None
            if not d.suppressed:
                ref = sink.create(d)
                created += 1
            else:
                by_reason[d.suppressed_reason.split(" —")[0]] = by_reason.get(
                    d.suppressed_reason.split(" —")[0], 0) + 1
            rows.append((
                run_id, issue["issue_id"], d.idempotency_key, d.source_system, d.source_id,
                d.source_permalink, d.title, d.description, d.raiser, d.dc_code, d.intent,
                json.dumps(d.entity_tokens, sort_keys=True),
                json.dumps(d.kapture_ticket_ids), d.reply_count, d.first_response_latency_s,
                d.state, d.duplicate_of, 1 if d.suppressed else 0, d.suppressed_reason,
                getattr(sink, "name", type(sink).__name__), ref,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                d.occurrence_count, json.dumps(d.occurrences),
                d.first_raised_at, d.last_raised_at))

        con.executemany(
            "INSERT OR REPLACE INTO ticket_drafts (run_id, issue_id, idempotency_key, "
            "source_system, source_id, source_permalink, title, description, raiser, dc_code, "
            "intent, entity_tokens_json, kapture_ticket_ids_json, reply_count, "
            "first_response_latency_s, state, duplicate_of, suppressed, suppressed_reason, "
            "sink, external_ref, emitted_at, occurrence_count, occurrences_json, "
            "first_raised_at, last_raised_at) VALUES (" + ",".join("?" * 26) + ")", rows)
        con.commit()

        stats = {
            "issues": len(issues),
            "would_create": created,
            "suppressed": len(issues) - created,
            "suppressed_by_reason": by_reason,
            "distinct_idempotency_keys": len(keys),
            "repeat_issues": sum(1 for r in rows if (r[22] or 1) > 1),
            "max_occurrences": max([(r[22] or 1) for r in rows], default=0),
            "sink": getattr(sink, "name", type(sink).__name__),
            "tickets_actually_created": 0,
            "_note": "Phase 1 creates nothing. The dry-run sink writes drafts to the store and "
                     "returns DRY- references so they can never be mistaken for ticket ids.",
        }
        con.execute(
            "INSERT OR REPLACE INTO stage_runs (run_id, stage, started_at, finished_at, "
            "rows_in, rows_out, llm_calls, cost_usd, config_hash, status) "
            "VALUES (?,?,?,?,?,?,0,0.0,?,'ok')",
            (run_id, "emit", started, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             len(issues), created, stats["sink"]))
        con.commit()
        return stats
    finally:
        if own:
            con.close()
