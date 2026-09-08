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


def _raw_copy(only: str | None = None) -> Path:
    """A writable, day-partitioned copy of the committed fixtures.

    `only` copies a single day, which the negative cases need: they rewrite a file down to one
    mutated record, and with the whole corpus present the other days would mask the effect.
    """
    d = Path(tempfile.mkdtemp(prefix="intake-raw-"))
    for f in sorted(FIXTURES.glob("*.ndjson")):
        if only is None or f.name == only:
            shutil.copy(f, d / f.name)
    return d


#: Records in the committed corpus — derived, never hardcoded. The fixtures grow as cases are
#: added, and an assertion of "3 rows" silently became a false failure the first time they did.
CORPUS_N = sum(1 for f in FIXTURES.glob("*.ndjson")
               for line in f.read_text(encoding="utf-8").splitlines() if line.strip())


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

    bad = _raw_copy("2026-09-04.ndjson")
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
        d = _raw_copy("2026-09-04.ndjson"); r = _first(d)
        r["message_id"] = value; _write_one(d, r)
        try:
            loadstage.load(d, run_id="neg", skip_validate=True)
            check(f"message_id as {label} is rejected", False, "it was LOADED")
        except loadstage.ContractError as e:
            check(f"message_id as {label} is rejected", "must be a string" in str(e))

    d = _raw_copy("2026-09-04.ndjson"); r = _first(d); r.pop("permalink"); _write_one(d, r)
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
    check("first load inserts the whole corpus", s1["inserted"] == CORPUS_N,
          f"{s1['inserted']} of {CORPUS_N} records")
    check("re-running the same directory is a no-op", s2["inserted"] == 0,
          f"{s2['already_present']} already present")

    con = store.connect()
    total = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    check(f"two loads leave {CORPUS_N} rows, not {CORPUS_N * 2}", total == CORPUS_N,
          f"{total} rows")

    orphans = con.execute(
        "SELECT COUNT(*) FROM messages c WHERE c.thread_ref IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM messages p WHERE p.channel_id = c.channel_id "
        " AND p.message_id = c.thread_ref)").fetchone()[0]
    replies = con.execute(
        "SELECT COUNT(*) FROM messages WHERE thread_ref IS NOT NULL").fetchone()[0]
    check("every reply joins its parent at full precision", orphans == 0 and replies > 0,
          f"{replies} replies, {orphans} orphaned — a float cast would orphan all of them")

    empty_with_files = con.execute(
        "SELECT COUNT(*) FROM messages WHERE TRIM(text) = '' AND has_media = 1").fetchone()[0]
    check("empty text WITH an attachment survives stage 1", empty_with_files == 1,
          "the content is only in the screenshot — stage 3 must never gate these")
    con.close()

    d = _raw_copy("2026-09-04.ndjson")
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
    d = _raw_copy("2026-09-04.ndjson")
    r = _first(d); r["attachments"], r["has_media"] = [], False; _write_one(d, r)
    blind = loadstage.load(d, run_id="blind", skip_validate=True)
    check("an attachment-blind export reports 0% coverage, not a silent pass",
          blind["attachment_coverage"] == 0.0,
          "attachments:[] + has_media:false satisfies the validator's agreement check")

    head("stage 4 — DC codes, tier A (label-anchored, NO registry needed)")
    from app.engine.algo.entities import (extract, extract_dc_tier_a,  # noqa: E402
                                          extract_dc_tier_b, dc_tier_b_candidates)
    from app.intake import extract as istage                            # noqa: E402
    REG = istage.load_code_list(istage.DC_CODES)
    DENY = istage.load_code_list(istage.DC_DENYLIST)

    def ta(t):
        return set(extract_dc_tier_a(t, DENY))

    def tb(t, reg=None):
        return set(extract_dc_tier_b(t, REG if reg is None else reg, DENY, exclude=ta(t)))

    # TOKEN SETS, never counts. Every regex error in this project produced a plausible count.
    for text, want in [
        ("DC Code: NXG", {"NXG"}),
        ("The *NQS DC landing time", {"NQS"}),
        ("HY9, IAM, VN4 hub not recive", {"HY9", "IAM", "VN4"}),
        ("DC Codes:\nNQS\nIQU\nUB1", {"NQS", "IQU", "UB1"}),
        ("DC _UB1_ pending", {"UB1"}),
        ("hub L9D and J93 down", {"L9D", "J93"}),
        ("DC R2F, K6L & T5X", {"R2F", "K6L", "T5X"}),
        ("LMDC PJ2/PJR", {"PJ2", "PJR"}),
        ("Hubs CKH and RW3", {"CKH", "RW3"}),
    ]:
        got = ta(text)
        check(f"tier A {text!r}", got == want, f"{sorted(got)}"
              + ("" if got == want else f"  WANT {sorted(want)}"))

    for text, why in [
        ("DC landing time is late", "the (?i) leak would yield LAN — 8 fictional hits of 18"),
        ("DC CODE IS MISSING", "_NOT_AN_ID stops the capture group yielding CODE"),
        ("DC code 3531 issue", "353 must not match inside 3531"),
        ("DC code: nxg", "the token class stays case-sensitive"),
    ]:
        got = ta(text)
        check(f"tier A rejects {text!r}", got == set(), why if not got else f"GOT {sorted(got)}")

    head("stage 4 — DC codes, tier B (registry REQUIRED)")
    check("registry loaded", len(REG) > 0, f"{len(REG)} codes")
    check("bare MX1 resolves (the cross-posted dedupe case)", tb("MX1 captain panel not working")
          == {"MX1"})
    check("'342 Tids' is REJECTED", tb("342 Tids are coming in Hardstop loss") == set(),
          "word-bounded, 3 chars, digit-bearing — only the registry rejects it")
    check("a digit-bearing heuristic WOULD have taken it",
          "342" in dc_tier_b_candidates("342 Tids are coming in Hardstop loss", DENY),
          "which is why digit-bearing is not a registry substitute")
    check("'897 Tids' is also rejected", tb("897 Tids are coming in Hardstop loss") == set(),
          "897 IS a real hub code — identical shape to 342, so shape cannot be the test")
    for tok, text in [("SIR", "SIR PLEASE CHECK"), ("RVP", "RVP pending"),
                      ("TID", "TID count is high")]:
        check(f"registry contaminant {tok} is denied", tok in REG and tb(text) == set(),
              "in the registry as a GENUINE hub code — membership is not sufficient evidence")
    check("no registry => tier B is SKIPPED, not guessed",
          extract_dc_tier_b("MX1 down", set(), DENY) == {}
          and extract_dc_tier_b("342 Tids", set(), DENY) == {})

    head("stage 4 — identifiers distinguished by exact length")
    line = "Registered mobile 9900000001, ticket 4788325630026, waybill VL0084870753799."
    pats = istage.load_patterns()
    for kind, want in [("mobile", {"9900000001"}), ("kapture_id", {"4788325630026"}),
                       ("waybill", {"VL0084870753799"})]:
        got = {v for k, v, _, _ in istage.extract_text(line, pats, set(), set()) if k == kind}
        check(f"{kind} does not match inside its neighbours", got == want, str(sorted(got)))

    head("the live conversation path must not have moved")
    check("extract('hub code is missing') still yields nothing",
          extract("hub code is missing")["hub_codes"] == [])
    check("extract('DC: BLR07 not working') still yields BLR07",
          extract("DC: BLR07 not working")["hub_codes"] == ["BLR07"])
    check("extract('hub NQS') still yields nothing (digit rule intact)",
          extract("hub NQS")["hub_codes"] == [],
          "the digit rule was measured on partner Hinglish and still holds there")

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
