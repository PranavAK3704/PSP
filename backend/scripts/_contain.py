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
  · every `*.json` → COPIED, at any size. A write lands on the copy and dies with the tmpdir.
  · directories, `*.db` and `*.txt` → SYMLINKED. Those are `valmo.db` (539 MB), `tickets.db`
    (40 MB), the fixture dirs, and the credential files — none of which are routed through
    PSP_STATE_DIR at all (`state_paths.py` says so explicitly: static corpus and `*.db` stay
    baked with the code), so their presence here is belt-and-braces and a symlink is free. A
    copy of a credential would also duplicate a 0600 secret into a tmpdir nothing cleans up.

── THIS RULE WAS FIRST WRITTEN BY SIZE, AND THAT WAS BACKWARDS ──────────────────────────────
The first version copied files under 5 MB and symlinked everything above it, on the reasoning
that big files are the static ones. Every store this module exists to protect is APPEND-ONLY
and therefore GROWS: `traces.json` is already 2.5 MB and `kapture_audits.json` is 9.4 MB. The
first mutable store to cross the threshold would have been silently symlinked, converting
`contain()` from a redirect into a PASSTHROUGH — harness writes landing in the real
`backend/data` while `check_all.py` went on printing "nothing written to backend/data". The
byte-identical verification would have kept passing right up to the day it stopped.

So the rule is by KIND, not size: the state dir serves JSON, so all JSON is copied. Copying
~14 MB once per batch is a rounding error next to a containment guarantee that expires on a
file-size threshold nobody is watching.

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
import sys
import tempfile
from pathlib import Path

_REAL = Path(__file__).resolve().parents[1] / "data"
_PREFIX = "psp-harness-"


def contain(label: str = "harness", provider: str = "localdb") -> str:
    """Redirect mutable state to a seeded tmpdir. Returns the dir. Idempotent per process.

    `PSP_HARNESS_DIR` is exported so a batch (`check_all.py` spawns one child per harness)
    shares ONE dir: seeding costs ~4 MB of copies once instead of eleven times, and a harness
    that reads what an earlier one wrote still behaves as it does in a single process.

    ── `provider` PINS PSP_DATA_PROVIDER, and it has to ──────────────────────────────────────
    Which account provider is active decides whether a captain EXISTS, and `_run_turn` checks the
    captain before anything else — so an unknown one ends the turn with an `error` event and no
    reply. A harness inheriting that choice from the developer's shell is a harness whose result
    depends on who ran it.

    It was inherited, and nobody noticed because nothing set it locally: everything got the code
    default `demo`. The moment `load_env.sh` started exporting `localdb` (which is what
    render.yaml has always deployed), `check_followups_e2e` began failing on turn one — its
    captain `VLMO-CPT-4471` is a SEED captain, real under `demo` and absent under `localdb`.

    The dependency is real and per-harness, measured across all fourteen:

        localdb   check_phase1, check_dataplane, check_op, check_risk   (real loss ledger)
        demo      check_followups_e2e                                   (seed captains)
        either    the remaining nine

    So each harness declares what it needs. The default is `localdb` because that is what
    render.yaml deploys — a harness should be wrong in the same direction as production, not in
    whichever direction the shell happened to be pointing.
    """
    # Reuse ONLY a directory this module made. The first version accepted anything that passed
    # `is_dir()`, which meant a single stale or inherited `PSP_HARNESS_DIR` — including one
    # pointing at `backend/data` itself — silently turned containment into a no-op, AND skipped
    # seeding, so the harnesses would have read an unseeded dir and quietly tested nothing.
    # Both halves of that are checked here: the name must carry our prefix, and the marker file
    # must be present, which only the seeding branch below writes.
    existing = os.environ.get("PSP_HARNESS_DIR")
    if existing:
        p = Path(existing)
        if (p.is_dir() and p.name.startswith(_PREFIX)
                and (p / ".psp-harness").exists()):
            os.environ["PSP_STATE_DIR"] = existing
            # The provider is pinned on BOTH paths. `check_all` seeds the dir once and every
            # child then takes this reuse branch — so setting it only where the dir is created
            # would leave all fourteen children inheriting the shell again, which is the exact
            # bug this argument exists to close.
            os.environ["PSP_DATA_PROVIDER"] = provider
            _blind_the_mirror(label)
            return existing
        # Do not raise and do not obey it — mint a fresh contained dir and say why.
        print(f"[contain] ignoring PSP_HARNESS_DIR={existing!r}: not a dir this module seeded",
              file=sys.stderr)

    d = tempfile.mkdtemp(prefix="psp-harness-")
    if _REAL.is_dir():
        for entry in _REAL.iterdir():
            dst = Path(d) / entry.name
            try:
                if entry.suffix == ".json":
                    shutil.copy2(entry, dst)       # writes land here and are discarded
                else:
                    dst.symlink_to(entry)          # dirs, *.db, *.txt — never written here
            except OSError:
                pass                                # a seed we cannot place is not fatal
    (Path(d) / ".psp-harness").write_text("seeded by scripts/_contain.py\n")
    os.environ["PSP_HARNESS_DIR"] = d
    os.environ["PSP_STATE_DIR"] = d
    os.environ["PSP_DATA_PROVIDER"] = provider
    _blind_the_mirror(label)
    return d


def _blind_the_mirror(label: str) -> None:
    for k in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        os.environ.pop(k, None)
    # ── AND THE ROUTER CONFIG, for the same reason the mirror is blinded ─────────────────────
    # A harness asserts what the router does at a GIVEN mode, and it sets that mode itself. An
    # inherited `PSP_PREROUTER*` from the shell silently overrides the mode under test.
    #
    # MEASURED, not hypothetical: `scripts/load_env.sh` exports PSP_PREROUTER=shadow and
    # PSP_PREROUTER_GREETING=on, and `run.sh` sources it — so the documented dev flow is a
    # shell where these are already set. In that shell check_router, check_phase1 and
    # check_followups_e2e all FAILED, because router.route()'s `off` fast path is skipped when
    # ANY per-tier override is present, so "off consults NOTHING" could not hold. Three green
    # harnesses read as three red ones, on a config the harness never chose.
    #
    # Cleared by PREFIX, not by name: the per-tier override is PSP_PREROUTER_<TIER>, so a new
    # tier would otherwise reintroduce this the day it is registered.
    for k in [k for k in os.environ if k.startswith("PSP_PREROUTER")]:
        os.environ.pop(k, None)
    # Belongs here rather than in each harness: every row a harness writes is a harness row.
    #
    # FORCE, not a fallback. As a last-resort default this leaked: `_provenance` resolved
    # explicit-argument first, and `/api/whatsapp/webhook` asserts `source="partner"`
    # server-side — so `check_followups_e2e.py`, which drives exactly that route, stamped its
    # rows `partner`. That is the one label that must only ever come from a real handset, and a
    # test run was producing it. Inside a contained run the truth is unconditional: whatever the
    # code under test believes it is, it is a harness.
    os.environ["PSP_CONCERN_SOURCE"] = label
    os.environ["PSP_CONCERN_SOURCE_FORCE"] = "1"
