"""The three helpers every harness had its own copy of.

── WHAT WAS ACTUALLY DUPLICATED, MEASURED ────────────────────────────────────────────────────
`FAILED` and `check()` were **byte-identical in eleven** harnesses. `head()` was not: six wrote
the rule as `'-' * 78` and three as `'─' * 78`, and two (`check_connectors`, `check_dataplane`)
had no `head` at all and inlined the separator. So the copies had already drifted in the one
place a copy can drift invisibly — output that nobody asserts.

Unified on the box-drawing rule, matching the `── … ──` banner style used throughout the app.
Safe to change: `check_all.py` decides PASS/FAIL from the child's **exit code** (`check_all.py:62`)
and only echoes its last line, so no separator is load-bearing.

── WHY MODULE-LEVEL STATE IS FINE HERE ───────────────────────────────────────────────────────
`FAILED` is shared mutable state, which would normally argue for a `Harness` object. It does not
here, for two reasons. `check_all.py` spawns **one child process per harness**, so a module-level
list is already per-harness in the only way that matters. And a class would rewrite every one of
the several hundred `check(...)` call sites into `H.check(...)` — a large mechanical diff across
the very scripts that verify everything else, buying nothing.

Callers mutate `FAILED` (`.append`) and read it; nothing rebinds it, so the imported name and
this module's name stay the same object.

── WHY NOT IN `_contain.py` ──────────────────────────────────────────────────────────────────
Every harness already imports that module, so putting these there would have cost no new import
line. But `_contain` has one job — make a harness unable to touch real state — and it is the
module you read when you need to trust that. Printing check results is unrelated, and a module
that does both is one nobody can skim for either.
"""
from __future__ import annotations

#: Labels of every failed check. A harness exits non-zero iff this is non-empty.
FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)


def head(n: str) -> None:
    print(f"\n{'─' * 78}\n{n}\n{'─' * 78}")
