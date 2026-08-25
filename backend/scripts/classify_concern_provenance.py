"""ONE-TIME retroactive pass: stamp `source` on every pre-provenance concern row.

    python scripts/classify_concern_provenance.py            # dry run — reports, writes nothing
    python scripts/classify_concern_provenance.py --apply    # stamps the ledger (and the mirror)

WHY: the concern log is the substrate under "Concerns Logged", "Recovered" and
`avg_resolution_time`. `concern_log.append` now stamps every NEW row with its provenance, but
the 1,014 rows written before that field existed carry nothing, so any aggregate over them is a
number nobody can account for. This gives each of them a label — or admits it cannot.

── WHAT THIS DOES NOT DO: DELETE ─────────────────────────────────────────────────────────────
An append-only ledger you delete from is not an append-only ledger, and a test row is
provenance rather than shame — it is the evidence for what the platform was doing on a given
afternoon. So nothing is removed. Rows are STAMPED where they stand, aggregates filter on the
stamp, and the append-only view shows everything with a chip.

That also disposes of a problem the plan raised: "archive locally and 996 records return
mid-sentence from Turso". There is nothing to clear. `concern_log._STORE` is a `durable_path`,
so writing through it updates the local cache AND the mirrored KV row in one call — the local
file and the durable truth cannot disagree, which a delete-then-clear sequence could not
promise.

── THE THREE CERTAIN RULES ───────────────────────────────────────────────────────────────────
Each maps to exactly one writer, so it is a fact about the row and not a guess about it:

  1. `channel == "proactive"`                    -> monitor   (only monitor.py writes that)
  2. `channel == "l3"` or `resolves_concern_id`  -> l3        (only l3/platform.py writes those)
  3. `captain_id` absent from valmo.db           -> harness   (it is not a partner. 588 rows
     carry `VLMO-CPT-4471` / `-3310` / `-2290` / `CAP-DEMO` / `LZ5-CPT` / `CAP1` / `CAP-TEST`,
     which are seed ids from the old monitor and the harnesses; no such partner exists.)

── AND WHY EVERYTHING ELSE IS `unclassified`, INCLUDING 419 ROWS ON A REAL CAPTAIN ID ────────
The remaining rows sit on partner ids that DO resolve. They are some mixture of harness runs
(`check_op` and `check_followups_e2e` both drive `20020388788`), my own testing through the
bench, and a small number of real turns. After the fact those three are indistinguishable,
and the timing evidence — which is strong — is still evidence rather than identity:

  · 25 sessions of exactly 7 rows inside 0.2 seconds, each beginning with the intent "hello"
  · sessions of 14 / 16 / 21 / 28 / 42 rows, all inside 95 seconds
  · against one session of 3 rows over 18 seconds whose intent is real Hindi free text

Nobody logs 42 concerns in 94 seconds, so most of that bucket is machine-paced. The rule could
be "rows/second above a threshold -> harness" and would be right most of the time. It is not
used, because a plausible-looking inference presented as a label is the exact defect this pass
exists to remove — and the direction matters: the numbers on the deck are counts of PARTNER
traffic, so the honest failure is to under-claim. `unclassified` under-claims. It is reported,
with the burst evidence beside it, and it stays a question rather than becoming an answer.

The consequence is worth stating plainly: **after this runs, `partner` is zero.** The field did
not exist when those rows were written, so the ledger genuinely cannot say which were partners.
That is the true statement, and it is a better one to make in a room than a confident split —
it also means the operational numbers must be earned by running live turns before a demo.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# NOT contained, deliberately: this script's entire purpose is to mutate the real ledger.

from app.ledger import concern_log                      # noqa: E402
from app.state_paths import state_path                  # noqa: E402

_DB = ROOT / "data" / "valmo.db"


def real_partner_ids() -> set[str]:
    """Every partner id the loss ledger knows. A captain id outside this set is not a partner.

    Fails LOUD rather than returning an empty set: with no DB, rule 3 would silently classify
    every row on earth as `harness` — including the real ones — which is a worse outcome than
    refusing to run.
    """
    if not _DB.exists():
        raise SystemExit(f"refusing to classify: {_DB} is missing, so rule 3 cannot be evaluated")
    with sqlite3.connect(f"file:{_DB}?mode=ro", uri=True) as db:
        ids = {str(r[0]) for r in db.execute("SELECT DISTINCT partner_id FROM attribution")}
    if not ids:
        raise SystemExit("refusing to classify: attribution.partner_id is empty")
    return ids


def classify(row: dict, real: set[str]) -> tuple[str, str]:
    """(source, why). The three certain rules, then `unclassified`."""
    if row.get("channel") == "proactive":
        return "monitor", "channel=proactive — only monitor.py writes that"
    if row.get("channel") == "l3" or row.get("resolves_concern_id"):
        return "l3", "shadows another concern — only l3/platform.py writes that"
    cid = str(row.get("captain_id") or "")
    if cid not in real:
        return "harness", f"captain_id {cid!r} is not a partner in valmo.db"
    return "unclassified", "on a real partner id, but written before provenance existed"


def burst_evidence(rows: list[dict]) -> str:
    """One line of timing evidence for the unclassified bucket. Reported, never used to label."""
    def ts(r):
        try:
            return datetime.fromisoformat(r["logged_at"])
        except Exception:  # noqa: BLE001
            return None
    dated = sorted([r for r in rows if ts(r)], key=ts)
    if len(dated) < 2:
        return "too few timestamped rows to characterise"
    dense = 0
    for i, r in enumerate(dated):
        j = i
        while j < len(dated) and (ts(dated[j]) - ts(r)).total_seconds() <= 60:
            j += 1
        if j - i >= 5:      # 5+ concerns inside a minute for one captain
            dense += 1
    return (f"{dense} of {len(dated)} sit in a run of 5+ rows inside 60s "
            f"(machine-paced, but not labelled on that basis)")


def mirror_check() -> str:
    """Refuse a local-only apply when a mirror exists but is not loaded.

    THE FAILURE THIS PREVENTS is silent and total. `concern_log._STORE` is a `_DurablePath`:
    when TURSO_* is set it treats the mirror as the truth and the local file as a cache, so
    `_load()` -> `exists()` -> `_fetch()` returns the MIRRORED value and `read_text()` writes it
    back over the local file. Stamping 1,014 rows with no credentials in the environment would
    therefore print "APPLIED", pass a re-read (the process holds its own write in `_cache`), and
    be reverted the first time the app boots with the mirror configured.

    `data/turso_url.txt` existing while `$TURSO_DATABASE_URL` does not is exactly that state —
    the deployment mirrors, this shell does not. The fix is one line before running:

        source scripts/load_env.sh
    """
    have_env = bool(os.environ.get("TURSO_DATABASE_URL") and os.environ.get("TURSO_AUTH_TOKEN"))
    creds_on_disk = (ROOT / "data" / "turso_url.txt").exists()
    if have_env:
        return "mirror loaded — the stamp lands locally AND durably"
    if creds_on_disk:
        raise SystemExit(
            "REFUSING TO APPLY: data/turso_url.txt exists, so this deployment mirrors the ledger "
            "to Turso, but TURSO_DATABASE_URL / TURSO_AUTH_TOKEN are not in this environment.\n"
            "A local-only stamp would be silently reverted by the next durable read.\n"
            "  fix:  source scripts/load_env.sh   (then re-run with --apply)")
    return "no mirror configured anywhere — local file is the truth"


def main() -> int:
    apply = "--apply" in sys.argv
    real = real_partner_ids()
    store = Path(state_path("concern_log.json"))
    log = json.loads(store.read_text()) if store.exists() else []
    print(f"ledger: {store}\nrows:   {len(log)}\n")

    already = [r for r in log if r.get("source") in concern_log.SOURCES]
    print(f"already stamped: {len(already)} (left untouched — a stamped row is the writer's own "
          f"statement and outranks anything inferred here)\n")

    counts, out = Counter(), []
    why_examples: dict[str, str] = {}
    for r in log:
        if r.get("source") in concern_log.SOURCES:
            counts[r["source"]] += 1
            out.append(r)
            continue
        src, why = classify(r, real)
        counts[src] += 1
        why_examples.setdefault(src, why)
        out.append({**r, "source": src})

    print("classification")
    print("─" * 78)
    for src in concern_log.SOURCES:
        if not counts[src]:
            continue
        print(f"  {src:14} {counts[src]:5}   {why_examples.get(src, 'stamped by its writer')}")
    print()
    unc = [r for r in out if r.get("source") == "unclassified"]
    if unc:
        print(f"unclassified evidence: {burst_evidence(unc)}\n")

    if counts["partner"] == 0:
        print("NOTE: `partner` is zero. Nothing here can be shown to be partner traffic, because\n"
              "the field did not exist when these rows were written. Operational numbers have to\n"
              "be earned by running live turns — see the header.\n")

    if not apply:
        print("DRY RUN — nothing written. Re-run with --apply to stamp the ledger.")
        return 0

    print(mirror_check())

    # Snapshot before rewriting someone's ledger. Local-only and git-ignored: it is a safety
    # net for this one operation, not a second source of truth to keep in sync.
    snap = store.with_name(f"concern_log.pre-provenance-{datetime.now():%Y%m%d-%H%M%S}.json")
    snap.write_text(json.dumps(log, indent=1))
    print(f"snapshot -> {snap}")

    # Through `_STORE`, not the raw path: that is what carries the write into the Turso mirror
    # as well, so the durable truth and the local cache cannot disagree afterwards.
    concern_log._STORE.write_text(json.dumps(out, indent=1))
    # Re-read from the DURABLE path, not the local file, and with the process cache bypassed —
    # otherwise this "verification" just reads back the value this process itself cached, which
    # would confirm a write that never reached the mirror.
    from app import durable_state
    durable_state._cache.pop("concern_log.json", None)
    reread = json.loads(concern_log._STORE.read_text())
    stamped = sum(1 for r in reread if r.get("source") in concern_log.SOURCES)
    print(f"APPLIED — {stamped}/{len(reread)} rows carry a source, confirmed by an uncached "
          f"durable re-read.")
    return 0 if stamped == len(reread) else 1


if __name__ == "__main__":
    raise SystemExit(main())
