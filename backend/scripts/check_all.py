"""Run every offline harness. NO LLM CALLS, NO NETWORK.

    python scripts/check_all.py

One command before a demo or a deploy. Each harness is standalone and can be run alone; this
just runs them in dependency order and sums the verdict, because six separate commands is five
too many to remember under pressure.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

HARNESSES = [
    ("check_phase1",     "data plane, spend ceiling, AWB lexer, history control"),
    ("check_writes",     "nothing claims to have written anything"),
    ("check_op",         "Orders & Planning resolves on read verdicts"),
    ("check_verifier",   "verifier independence, never overclaimed"),
    ("check_connectors", "the endpoint registry, and that it calls nothing"),
    ("check_log10",      "typed scan timelines, UNKNOWN never reads as NO"),
    ("check_calibration", "the gate's confidence is a label, not a probability"),
    ("check_router",      "the deterministic pre-router, and its false-positive floor"),
]


def main() -> int:
    results = []
    for name, blurb in HARNESSES:
        print(f"\n{'#' * 78}\n#  {name}  —  {blurb}\n{'#' * 78}")
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
    print(f"ALL {len(results)} HARNESSES PASS — no API calls, no network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
