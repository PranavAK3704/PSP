"""Stage 1 — read the Slack export NDJSON, gate it on the contract, write it to SQLite.

── THE CONTRACT GATE RUNS FIRST, AND IT IS NOT OPTIONAL ──────────────────────────────────────
`tools/validate.js` is the deliverable gate the export is produced against — the same script
the person building the exporter runs before handing a file over. The loader shells out to it
and refuses to insert anything if it exits non-zero. Re-implementing those checks in Python
would create a second, drifting definition of the contract; running the original means there
is exactly one.

It takes a DIRECTORY, not a file, and it must see the WHOLE corpus in one pass: its orphan
`thread_ref` check is corpus-level, so validating day-files one at a time would fail every
reply whose parent sits in yesterday's file. It also derives each record's IST day from
`message_id` and checks it against a `YYYY-MM-DD` in the filename, so `data/raw/` is expected
to be day-partitioned.

── WHAT THE GATE CANNOT SEE ──────────────────────────────────────────────────────────────────
A PASS is necessary, not sufficient. `has_media` is only checked for AGREEMENT with
`attachments`, so an export whose tool could not read files at all — every `attachments: []`
and every `has_media: false` — passes cleanly while being silently lossy. That is a known
property of the 467-record portion. `load()` therefore records per-file attachment coverage in
`stage_runs` so the emptiness is visible as a number rather than being discovered later by a
noise gate that drops screenshot-only issues.

── message_id IS A STRING, AND THE FAILURE IS LOUD ───────────────────────────────────────────
Slack's `ts` is a decimal string and it is the primary key. Parsed as a float it loses
precision and stops joining to `thread_ref`. `json.loads` will happily hand back a float if an
upstream tool ever emits one unquoted, so the type is asserted per record and the load aborts.
The validator checks this too; the duplication is deliberate, because this loader is also
pointed at fixtures that were not produced by the exporter.

── RE-RUNNING IS A NO-OP ─────────────────────────────────────────────────────────────────────
`messages` is immutable. Inserts are `INSERT OR IGNORE` on the composite PK, so loading the
same directory twice leaves the row count unchanged. A key that reappears with DIFFERENT
content is not silently ignored, though: that means the export changed under a run, which is a
data problem worth seeing, so it is counted and reported as `conflicts`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import store

#: The 26 keys of schema v1.1, in the canonical order `tools/schema.json` defines.
V11_KEYS = [
    "schema_version", "source", "workspace_id", "channel_id", "channel_name", "message_id",
    "ts_epoch", "ts_iso", "author_id", "author_name", "author_email", "author_is_bot",
    "text", "subtype", "thread_ref", "is_thread_parent", "reply_count", "mentions",
    "channel_mention", "subteam_mentions", "attachments", "has_media", "reactions",
    "permalink", "edited_ts", "fetched_at",
]

_REPO = Path(__file__).resolve().parents[3]
_VALIDATOR = _REPO / "tools" / "validate.js"
_SCHEMA = _REPO / "tools" / "schema.json"

#: Columns holding a JSON-encoded list, stored as TEXT so the store stays hand-queryable
#: with plain SQL rather than needing a JSON1 build.
_JSON_COLS = {
    "mentions": "mentions_json",
    "subteam_mentions": "subteam_mentions_json",
    "attachments": "attachments_json",
    "reactions": "reactions_json",
}


class ContractError(RuntimeError):
    """The export failed `tools/validate.js`, or a record violated a load-time invariant."""


def validator_available() -> bool:
    """Node and the validator are both present. Harnesses skip the gate when they are not."""
    return shutil.which("node") is not None and _VALIDATOR.exists()


def run_validator(raw_dir: Path) -> str:
    """Run the contract gate over the whole directory. Raises ContractError on a non-zero exit."""
    if not validator_available():
        raise ContractError(
            f"tools/validate.js needs node on PATH (node={shutil.which('node')!r}, "
            f"validator_exists={_VALIDATOR.exists()}). Pass skip_validate=True only when you "
            f"have run the gate elsewhere.")
    p = subprocess.run(
        ["node", str(_VALIDATOR), str(raw_dir), "--schema", str(_SCHEMA)],
        capture_output=True, text=True)
    if p.returncode != 0:
        raise ContractError(
            f"validate.js FAILED on {raw_dir} (exit {p.returncode}) — this is not a "
            f"deliverable and nothing was loaded.\n{p.stdout}\n{p.stderr}")
    return p.stdout


def _row(rec: dict, src_file: str, loaded_at: str) -> tuple:
    mid = rec.get("message_id")
    if not isinstance(mid, str):
        raise ContractError(
            f"{src_file}: message_id must be a string, got {type(mid).__name__} "
            f"({mid!r}) — a float cast destroys the key and breaks every thread join.")

    missing = [k for k in V11_KEYS if k not in rec]
    if missing:
        raise ContractError(f"{src_file}: record {mid} missing keys: {', '.join(missing)}")

    def js(key: str) -> str:
        # sort_keys so a re-export that reorders a list does not look like a content change
        # to the conflict check below.
        return json.dumps(rec.get(key) or [], sort_keys=True, separators=(",", ":"))

    return (
        rec["channel_id"], mid, rec.get("schema_version"), rec.get("source"),
        rec.get("workspace_id"), rec.get("channel_name"),
        float(rec["ts_epoch"]), rec["ts_iso"],
        rec.get("author_id"), rec.get("author_name"), rec.get("author_email"),
        1 if rec.get("author_is_bot") else 0,
        rec.get("text") or "", rec.get("subtype"),
        rec.get("thread_ref"), 1 if rec.get("is_thread_parent") else 0,
        int(rec.get("reply_count") or 0),
        js("mentions"), 1 if rec.get("channel_mention") else 0, js("subteam_mentions"),
        js("attachments"), 1 if rec.get("has_media") else 0, js("reactions"),
        rec.get("permalink"), rec.get("edited_ts"), rec.get("fetched_at"),
        src_file, loaded_at,
    )


_COLS = [
    "channel_id", "message_id", "schema_version", "source", "workspace_id", "channel_name",
    "ts_epoch", "ts_iso", "author_id", "author_name", "author_email", "author_is_bot",
    "text", "subtype", "thread_ref", "is_thread_parent", "reply_count",
    "mentions_json", "channel_mention", "subteam_mentions_json",
    "attachments_json", "has_media", "reactions_json",
    "permalink", "edited_ts", "fetched_at", "src_file", "loaded_at",
]
#: Everything except the PK and the two load-provenance columns — the fields a re-export could
#: legitimately change, and therefore the ones the conflict check compares.
_CONTENT_COLS = [c for c in _COLS if c not in ("channel_id", "message_id", "src_file", "loaded_at")]


def read_ndjson(raw_dir: Path) -> list[tuple[dict, str]]:
    """Every record in the directory, paired with its source filename. Blank lines are skipped."""
    out: list[tuple[dict, str]] = []
    files = sorted(p for p in raw_dir.iterdir()
                   if p.suffix in (".ndjson", ".jsonl") and not p.name.endswith(".tmp"))
    for f in files:
        for i, line in enumerate(f.read_text(encoding="utf-8").split("\n"), start=1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise ContractError(f"{f.name}:{i} not valid JSON: {e}") from e
            if not isinstance(rec, dict):
                raise ContractError(f"{f.name}:{i} record is not a JSON object")
            out.append((rec, f.name))
    return out


def load(raw_dir: str | Path, *, run_id: str, con: sqlite3.Connection | None = None,
         skip_validate: bool = False) -> dict:
    """Load a day-partitioned NDJSON directory into `messages`. Idempotent.

    Returns counts: files, records, inserted, already_present, conflicts, and the attachment
    coverage that the contract gate cannot itself detect.
    """
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        raise ContractError(f"{raw_dir} is not a directory — validate.js reads a directory, "
                            f"and the corpus-level checks need the whole corpus at once.")

    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    validator_out = "" if skip_validate else run_validator(raw_dir)

    own = con is None
    con = con or store.connect()
    try:
        records = read_ndjson(raw_dir)
        loaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        rows = [_row(rec, src, loaded_at) for rec, src in records]

        before = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

        # Conflict check BEFORE inserting: a key already present with different content means
        # the export changed underneath us. OR IGNORE would hide that.
        conflicts = []
        for r in rows:
            prior = con.execute(
                f"SELECT {', '.join(_CONTENT_COLS)} FROM messages "
                f"WHERE channel_id = ? AND message_id = ?", (r[0], r[1])).fetchone()
            if prior is None:
                continue
            new = dict(zip(_COLS, r))
            if any(prior[c] != new[c] for c in _CONTENT_COLS):
                conflicts.append(f"{r[0]}/{r[1]}")

        con.executemany(
            f"INSERT OR IGNORE INTO messages ({', '.join(_COLS)}) "
            f"VALUES ({', '.join('?' * len(_COLS))})", rows)
        con.commit()
        after = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

        with_files = sum(1 for rec, _ in records if rec.get("attachments"))
        stats = {
            "files": len({src for _, src in records}),
            "records": len(records),
            "inserted": after - before,
            "already_present": len(rows) - (after - before),
            "conflicts": conflicts,
            "records_with_attachments": with_files,
            "attachment_coverage": round(with_files / len(records), 4) if records else 0.0,
            "validator": "skipped" if skip_validate else "passed",
            "validator_output": validator_out.strip(),
        }

        con.execute(
            "INSERT OR REPLACE INTO stage_runs "
            "(run_id, stage, started_at, finished_at, rows_in, rows_out, llm_calls, cost_usd, "
            " config_hash, status) VALUES (?,?,?,?,?,?,0,0.0,?,?)",
            (run_id, "load", started, datetime.now(timezone.utc).isoformat(timespec="seconds"),
             len(records), stats["inserted"], str(raw_dir),
             "conflicts" if conflicts else "ok"))
        con.commit()
        return stats
    finally:
        if own:
            con.close()
