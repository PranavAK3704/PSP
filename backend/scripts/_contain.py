"""Harness containment — keep test runs out of the ledger the demo counts.

WHY THIS EXISTS, measured: of the concern log's 1,014 rows, **702 were written in one
afternoon by `check_all.py`** and 462 of them carry the captain id `VLMO-CPT-4471`, which
matches no real partner. Every harness imported the app with `PSP_STATE_DIR` unset, so
`concern_log._STORE` resolved to `backend/data/concern_log.json` — the same file the deck's
"Concerns Logged" tile counts and the same file `/api/insights` averages resolution time over.

Archiving the log does NOT fix that. The next test run puts the rows back. Containment has to
land first, which is why this module exists before the archive script does.

── THE IMPORT-ORDER TRAP ────────────────────────────────────────────────────────────────────
`contain()` MUST run before the first `app.` import. `concern_log._STORE` is bound at module
import time by `durable_path("concern_log.json")`, which calls `state_path()` immediately —
setting `PSP_STATE_DIR` afterwards changes nothing at all, silently. Same for `trace_log`,
`llm/meter`, `audit/runner`, `audit/cpd`, `audit/rubric`, `knowledge/*` and `kt/engine`.

── WHY THE DIR IS SEEDED AND NOT EMPTY ──────────────────────────────────────────────────────
The state dir is not only mutable logs. It also holds AUTHORED content the harnesses read:
the SOP corpus (`kt_queue.json`, 584 KB), `governance_framework.json`, `blueprints.json`,
`audit_rubric.json`, `kapture_calibration.json`, `users.json`. Pointing PSP_STATE_DIR at an
empty tmpdir would not contain the harnesses, it would blind them — `check_calibration` would
read an absent kapture file and report a κ over n=0, which passes.

So the rule is by SIZE, not by a hand-kept list of names:
  · files < 5 MB  → COPIED. A write lands on the copy and is discarded with the tmpdir.
  · files ≥ 5 MB and directories → SYMLINKED. Those are `valmo.db` (539 MB), `tickets.db`
    (40 MB), `kapture_audits.json` (9 MB) and the fixture dirs — none of which are routed
    through PSP_STATE_DIR at all (`state_paths.py` says so explicitly: static corpus stays
    baked with the code), so their presence here is belt-and-braces, and a symlink is free.
  · `*.txt` → SYMLINKED regardless of size. Those are `llm_key.txt`, `turso_url.txt`,
    `turso_token.txt` — credentials, read only by `scripts/load_env.sh` and read from
    `backend/data` directly rather than through the state dir, so a copy buys nothing and
    duplicates a secret (mode 0600) into a tmpdir that nothing cleans up.

Size, not a name list, because a name list is exactly the thing that goes stale: add one more
`durable_path("something.json")` next month and a list quietly stops containing it, while the
size rule keeps working with no edit.

Copied, NOT emptied: containment is about writes not landing, not about hiding reads. A
harness that reads real history (`check_calibration` pairs machine vs human verdicts off the
concern log; `cpd` mines it for themes) must still see the real rows.

── AND THE MIRROR ───────────────────────────────────────────────────────────────────────────
TURSO_* is cleared. A contained run that still holds the mirror credentials writes its junk
rows straight into the durable KV table — which is WORSE than the local file, because the
local one is git-ignored and the mirror is what production reads back on boot. The local file
being contained while the mirror is not would have looked like a fix and been the opposite.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_REAL = Path(__file__).resolve().parents[1] / "data"
_BIG = 5 * 1024 * 1024


def contain(label: str = "harness") -> str:
    """Redirect mutable state to a seeded tmpdir. Returns the dir. Idempotent per process.

    `PSP_HARNESS_DIR` is exported so a batch (`check_all.py` spawns one child per harness)
    shares ONE dir: seeding costs ~4 MB of copies once instead of eleven times, and a harness
    that reads what an earlier one wrote still behaves as it does in a single process.
    """
    existing = os.environ.get("PSP_HARNESS_DIR")
    if existing and Path(existing).is_dir():
        os.environ["PSP_STATE_DIR"] = existing
        _blind_the_mirror(label)
        return existing

    d = tempfile.mkdtemp(prefix="psp-harness-")
    if _REAL.is_dir():
        for entry in _REAL.iterdir():
            dst = Path(d) / entry.name
            try:
                if entry.is_dir() or entry.suffix == ".txt" or entry.stat().st_size >= _BIG:
                    dst.symlink_to(entry)          # read-only by construction; free
                else:
                    shutil.copy2(entry, dst)       # writes land here and are discarded
            except OSError:
                pass                                # a seed we cannot place is not fatal
    os.environ["PSP_HARNESS_DIR"] = d
    os.environ["PSP_STATE_DIR"] = d
    _blind_the_mirror(label)
    return d


def _blind_the_mirror(label: str) -> None:
    for k in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        os.environ.pop(k, None)
    # Belongs here rather than in each harness: every row a harness writes is a harness row,
    # and `concern_log.append` reads this env var as its last resort before `unclassified`.
    os.environ["PSP_CONCERN_SOURCE"] = label
