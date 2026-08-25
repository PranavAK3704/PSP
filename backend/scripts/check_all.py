"""Run every offline harness. NO LLM CALLS, NO NETWORK, AND NO WRITES TO THE REAL LEDGER.

    python scripts/check_all.py

One command before a demo or a deploy. Each harness is standalone and can be run alone; this
just runs them in dependency order and sums the verdict, because six separate commands is five
too many to remember under pressure.

The third clause in that first line is new and was earned the hard way: 702 of the concern
log's 1,014 rows were written by this script in one afternoon, into the same file the deck's
"Concerns Logged" tile counts. `contain()` here exports ONE tmpdir that every child inherits,
so the batch seeds ~4 MB of copies once rather than twelve times; each harness also calls
`contain()` itself, which is what protects a harness run on its own. See scripts/_contain.py.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))                 # for scripts._contain
from scripts._contain import contain                 # noqa: E402

HARNESSES = [
    ("check_phase1",     "data plane, spend ceiling, AWB lexer, history control"),
    # WAS NEVER IN THIS LIST. 12 KB of assertions on the data-plane boundary — the rule that no
    # model receives an identifier it was not already given — sitting unrun since it was written,
    # while `check_phase1` and a comment in `engine/dataplane.py` both pointed at it as though it
    # were covered. A harness that is not in this list does not exist.
    ("check_dataplane",  "no model receives an identifier it was not already given"),
    ("check_writes",     "nothing claims to have written anything"),
    ("check_op",         "Orders & Planning resolves on read verdicts"),
    ("check_verifier",   "verifier independence, never overclaimed"),
    ("check_connectors", "the endpoint registry, and that it calls nothing"),
    ("check_log10",      "typed scan timelines, UNKNOWN never reads as NO"),
    ("check_risk",       "at-risk derivation over real rows, and what it refuses to claim"),
    ("check_calibration", "the gate's confidence is a label, not a probability"),
    ("check_router",      "the deterministic pre-router, and its false-positive floor"),
    ("check_followups",   "follow-ups answered in scope, and never out of it"),
    ("check_followups_e2e", "the wiring: disposition -> chips -> tap/number, both transports"),
]


def main() -> int:
    contained = contain()
    print(f"state contained -> {contained}\n(mutable stores redirected; TURSO_* cleared; "
          f"every row written here is stamped source=harness)")
    results = []
    for name, blurb in HARNESSES:
        print(f"\n{'#' * 78}\n#  {name}  —  {blurb}\n{'#' * 78}")
        # NO env= — the child must INHERIT the contained os.environ (PSP_STATE_DIR,
        # PSP_HARNESS_DIR, PSP_CONCERN_SOURCE, and the absence of TURSO_*). Passing an
        # explicit env here would un-contain the entire batch without changing a single
        # assertion, which is the failure mode that produced those 702 rows.
        p = subprocess.run([sys.executable, str(HERE / f"{name}.py")],
                           capture_output=True, text=True)
        sys.stdout.write(p.stdout)
        if p.stderr.strip():
            sys.stderr.write(p.stderr)
        ok = p.returncode == 0
        tail = [l for l in p.stdout.strip().splitlines() if l.strip()]
        results.append((name, ok, tail[-1] if tail else "(no output)"))

    print(f"\n{'=' * 78}\nSUMMARY\n{'=' * 78}")
    for name, ok, verdict in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name:18} {verdict[:56]}")
    failed = [n for n, ok, _ in results if not ok]
    print()
    if failed:
        print(f"{len(failed)} harness(es) FAILED: {', '.join(failed)}")
        return 1
    print(f"ALL {len(results)} HARNESSES PASS — no API calls, no network, nothing written to backend/data.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
