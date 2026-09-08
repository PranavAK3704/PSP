"""Assertions for the Slack intake pipeline. NO LLM CALLS, NO NETWORK.

    python scripts/check_intake.py

Stages 1 and 3-5 of this pipeline are pure functions over SQLite by design: if an accuracy
problem is fixable with a better regex it does not go to a model. So everything this harness
covers is deterministic, and a failure here is a real regression rather than a sampling wobble.

── WHY THIS SETS $INTAKE_DB ──────────────────────────────────────────────────────────────────
`contain()` redirects the JSON stores, but it CANNOT contain a SQLite file: `_contain.py`
copies `*.json` and SYMLINKS directories, `*.db` and `*.txt` (_contain.py:127), on the stated
assumption that `.db` files are static and read-only. The intake store is the first writable
one in this repo, so without the explicit `$INTAKE_DB` override below this harness would write
straight through the symlink into the real `backend/data/intake/intake.db` — the same class of
leak that put 702 junk rows in the real concern log.

`app/intake/store.py` resolves its path inside `db_path()` rather than at import time
specifically so this override works after the import.

Exit code 0 = clean.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts._contain import contain; contain()   # MUST precede every `app.` import  # noqa: E402
from scripts._harness import FAILED, check, head  # noqa: E402

# The .db cannot be contained by contain() — see the module docstring.
_TMP = tempfile.mkdtemp(prefix="intake-harness-")
os.environ["INTAKE_DB"] = str(Path(_TMP) / "intake.db")

from app.intake import loadstage, store  # noqa: E402

FIXTURES = ROOT / "data" / "intake" / "fixtures"


def _raw_copy() -> Path:
    """A writable, day-partitioned copy of the committed fixtures."""
    d = Path(tempfile.mkdtemp(prefix="intake-raw-"))
    for f in sorted(FIXTURES.glob("*.ndjson")):
        shutil.copy(f, d / f.name)
    return d


def _first(raw: Path) -> dict:
    f = sorted(raw.glob("*.ndjson"))[0]
    return json.loads(f.read_text().split("\n")[0])


def _write_one(raw: Path, rec: dict) -> None:
    sorted(raw.glob("*.ndjson"))[0].write_text(json.dumps(rec) + "\n")


def main() -> int:
    head("containment — the harness must not be able to touch the real store")
    real = ROOT / "data" / "intake" / "intake.db"
    check("$INTAKE_DB points into a tmpdir", str(store.db_path()).startswith(tempfile.gettempdir()),
          str(store.db_path()))
    check("the real store is not the target", store.db_path() != real)

    head("schema")
    con = store.connect()
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check("every table present", {"messages", "entities", "assignments", "issues", "duplicates",
                                  "llm_cache", "adjudications", "stage_runs"} <= tables,
          f"{len(tables)} tables")
    pk = [r[1] for r in con.execute("PRAGMA table_info(messages)") if r[5]]
    check("messages PK is (channel_id, message_id)", pk == ["channel_id", "message_id"],
          " + ".join(pk))
    check("llm_cache is NOT run-scoped (a re-run must cost nothing)",
          "run_id" not in [r[1] for r in con.execute("PRAGMA table_info(llm_cache)")])
    con.close()

    head("stage 1 — the contract gate")
    raw = _raw_copy()
    if loadstage.validator_available():
        check("validate.js PASSes the committed fixtures", "PASS" in loadstage.run_validator(raw))
    else:
        check("validate.js skipped (node absent)", True,
              "the JS validator is the exporter's own gate and is not reimplemented in Python")

    bad = _raw_copy()
    rec = _first(bad); rec["ts_iso"] = "2026-09-04T00:46:11Z"      # UTC Z — field-map.md forbids
    _write_one(bad, rec)
    if loadstage.validator_available():
        try:
            loadstage.load(bad, run_id="gate")
            check("UTC 'Z' ts_iso is rejected", False, "it was LOADED")
        except loadstage.ContractError:
            con = store.connect()
            n = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            con.close()
            check("UTC 'Z' ts_iso is rejected and nothing is loaded", n == 0, f"{n} rows")

    head("stage 1 — load-time invariants")
    for label, value in [("float", 1788482771.760339), ("int", 1788482771), ("null", None)]:
        d = _raw_copy(); r = _first(d); r["message_id"] = value; _write_one(d, r)
        try:
            loadstage.load(d, run_id="neg", skip_validate=True)
            check(f"message_id as {label} is rejected", False, "it was LOADED")
        except loadstage.ContractError as e:
            check(f"message_id as {label} is rejected", "must be a string" in str(e))

    d = _raw_copy(); r = _first(d); r.pop("permalink"); _write_one(d, r)
    try:
        loadstage.load(d, run_id="neg", skip_validate=True)
        check("a missing v1.1 key is rejected", False, "it was LOADED")
    except loadstage.ContractError as e:
        check("a missing v1.1 key is rejected", "missing keys" in str(e))

    try:
        loadstage.load(sorted(raw.glob("*.ndjson"))[0], run_id="neg", skip_validate=True)
        check("a single FILE is rejected (the gate needs the corpus)", False, "it was accepted")
    except loadstage.ContractError as e:
        check("a single FILE is rejected (the gate needs the corpus)", "not a directory" in str(e))

    head("stage 1 — idempotency and immutability")
    s1 = loadstage.load(raw, run_id="r1", skip_validate=True)
    s2 = loadstage.load(raw, run_id="r1", skip_validate=True)
    check("first load inserts", s1["inserted"] == 3, f"{s1['inserted']} rows")
    check("re-running the same directory is a no-op", s2["inserted"] == 0,
          f"{s2['already_present']} already present")

    con = store.connect()
    total = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    check("two loads leave 3 rows, not 6", total == 3, f"{total} rows")

    joins = con.execute(
        "SELECT c.message_id AS child, p.message_id AS parent FROM messages c "
        "JOIN messages p ON p.channel_id = c.channel_id AND p.message_id = c.thread_ref "
        "WHERE c.thread_ref IS NOT NULL").fetchall()
    check("thread_ref joins its parent at full precision",
          [(r["child"], r["parent"]) for r in joins]
          == [("1788483900.114201", "1788482771.760339")],
          "a float cast would break this")

    empty_with_files = con.execute(
        "SELECT COUNT(*) FROM messages WHERE TRIM(text) = '' AND has_media = 1").fetchone()[0]
    check("empty text WITH an attachment survives stage 1", empty_with_files == 1,
          "the content is only in the screenshot — stage 3 must never gate these")
    con.close()

    d = _raw_copy()
    loadstage.load(d, run_id="c1", skip_validate=True)
    r = _first(d); original = r["text"]; r["text"] = original + " (changed)"; _write_one(d, r)
    s = loadstage.load(d, run_id="c2", skip_validate=True)
    check("a changed re-export is REPORTED, not swallowed", bool(s["conflicts"]),
          str(s["conflicts"]))
    con = store.connect()
    kept = con.execute("SELECT text FROM messages WHERE message_id = ?",
                       (r["message_id"],)).fetchone()[0]
    con.close()
    check("messages is immutable — the stored row is unchanged", kept == original)

    head("what the contract gate CANNOT see")
    check("attachment coverage is recorded as a number", "attachment_coverage" in s1,
          f"{s1['attachment_coverage']:.0%} of fixture records carry files")
    d = _raw_copy(); r = _first(d); r["attachments"], r["has_media"] = [], False; _write_one(d, r)
    blind = loadstage.load(d, run_id="blind", skip_validate=True)
    check("an attachment-blind export reports 0% coverage, not a silent pass",
          blind["attachment_coverage"] == 0.0,
          "attachments:[] + has_media:false satisfies the validator's agreement check")

    print(f"\n{'=' * 78}")
    if FAILED:
        print(f"FAILED {len(FAILED)}:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("INTAKE VERIFIED — contract gate enforced, load idempotent, key precision kept.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
