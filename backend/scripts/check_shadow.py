#!/usr/bin/env python3
"""What shadow mode has actually produced — the report that did not exist.

── WHY THIS SCRIPT HAD TO BE WRITTEN BEFORE ANY TIER COULD BE FLIPPED ────────────────────────
`router.route()` in SHADOW computes a tier's verdict, stores the withheld reply as
`trace["shadow_reply"]`, and returns None so the LLM answers instead. The whole point is to
collect a number the offline corpus cannot give us: 81.6% of tickets are WhatsApp and the repo
holds none of that message text, so absorption is unmeasurable from files.

`scripts/load_env.sh` then said: "flip PSP_PREROUTER_FOLLOWUP=on once the shadow diffs have been
read." Nothing read them. No script anywhere loaded `traces.json` back and compared shadow
against the LLM — so the condition on the flip could never be satisfied, and 27 authored,
sourced, phrasing-tested nodes stayed withheld indefinitely while captains paid the measured
Rs 4.54 a turn for answers the repo already had.

This script is that missing half. It is a MEASUREMENT, not a gate: it always exits 0. A
threshold here would only invite tuning the threshold — same reasoning as check_retrieval.py.

── AND IT REPORTS AN EMPTY DENOMINATOR AS EMPTY ──────────────────────────────────────────────
The first thing it found was that shadow had produced ZERO comparisons: 9 turns carried a
firstpass event, all in shadow, none with a `shadow_reply`. An empty result printed as "0% of 0"
looks like a working measurement of a working system. It is not — it means nobody has driven a
turn that a tier would have answered, and the honest report says so.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

from scripts._contain import contain; contain()   # MUST precede every `app.` import — see _contain.py
from scripts._harness import head   # noqa: E402

from app.durable_state import durable_path   # noqa: E402


def _firstpass(rec: dict) -> dict | None:
    """The prerouter's own trace, as conversation.py persists it: a `firstpass` event whose
    `data` IS the router's trace dict. Last one wins — Pipeline.jsx collapses by node id the
    same way, so this reads the turn the same way the UI does."""
    ev = [e for e in (rec.get("events") or []) if e.get("node") == "firstpass"]
    return (ev[-1].get("data") or {}) if ev else None


def main() -> int:
    traces = durable_path("traces.json").read_json({})
    concerns = {c.get("id"): c for c in durable_path("concern_log.json").read_json([])
                if isinstance(c, dict)}

    head("[1] the denominator — turns the router actually saw")
    seen = [(cid, _firstpass(rec)) for cid, rec in traces.items() if isinstance(rec, dict)]
    seen = [(cid, d) for cid, d in seen if d is not None]
    print(f"       {len(traces)} concerns with a persisted trace")
    print(f"       {len(seen)} of them carry a firstpass event (the router ran)")
    if not seen:
        print("\n       NOTHING TO REPORT. No persisted turn shows the router running at all.")
        print("       Not a pass: it means no comparison exists, so no flip can cite one.")
        return 0

    modes: dict[str, int] = {}
    for _cid, d in seen:
        modes[str(d.get("mode"))] = modes.get(str(d.get("mode")), 0) + 1
    print(f"       by mode: {modes}")

    head("[2] absorption — turns a tier answered without a model")
    fired = [(cid, d) for cid, d in seen if d.get("fired")]
    tiers: dict[str, int] = {}
    for _cid, d in fired:
        t = str((d.get("fired") or {}).get("tier"))
        tiers[t] = tiers.get(t, 0) + 1
    pct = 100.0 * len(fired) / len(seen)
    print(f"       {len(fired)} of {len(seen)} turns absorbed  ({pct:.1f}%)  {tiers or '{}'}")
    print(f"       NOTE: this is absorption over TRACED turns only. It is not the ticket-level")
    print(f"       rate — the corpus has no WhatsApp text, which is why shadow exists at all.")

    head("[3] the diff shadow mode exists to produce")
    withheld = [(cid, d) for cid, d in seen if d.get("shadow_reply")]
    if not withheld:
        print(f"       0 of {len(seen)} traced turns carry a `shadow_reply`.")
        print()
        print("       So shadow mode has produced NO comparisons. The instruction in")
        print("       load_env.sh — 'flip once the shadow diffs have been read' — had no data")
        print("       behind it, and could not acquire any without someone driving a turn a")
        print("       tier would have answered. That is why followup/glossary were flipped on")
        print("       the strength of check_followups.py's 83 phrasings instead of on this.")
        print()
        print("       When a shadowed tier does fire, each case prints below as:")
        print("         tier · would-have-said   vs   what the captain actually got")
        return 0

    print(f"       {len(withheld)} withheld replies to compare\n")
    agree = 0
    for cid, d in withheld:
        shadow_txt = str(d.get("shadow_reply") or "").strip()
        actual = str((concerns.get(cid) or {}).get("reply") or "").strip()
        tier = str(d.get("shadow_tier") or (d.get("fired") or {}).get("tier") or "?")
        # Deliberately NOT a similarity score. Two correct Hinglish answers to the same question
        # share few tokens, so a number here would read as disagreement and be wrong. A human
        # reads these; the script's job is to put them side by side and count only the exact
        # question of whether the LLM said anything at all.
        same = bool(actual) and shadow_txt[:60].lower() in actual.lower()
        agree += 1 if same else 0
        print(f"       {cid}  tier={tier}  {'(LLM echoed it)' if same else ''}")
        print(f"         shadow: {shadow_txt[:150]}")
        print(f"         actual: {(actual or '(no reply recorded)')[:150]}")
        print()
    print(f"       {agree} of {len(withheld)} withheld replies appear verbatim in what was sent.")
    print("       Read the rest by eye — a low count here is NOT evidence of disagreement,")
    print("       only that the model phrased it differently.")
    return 0


if __name__ == "__main__":
    print("=" * 78)
    print("SHADOW MODE — what it has collected")
    print("=" * 78)
    rc = main()
    print()
    print("=" * 78)
    # check_all echoes a child's LAST stdout line as its summary verdict, so the last line has
    # to be the verdict — a rule of `=` told the reader nothing.
    print("SHADOW REPORTED — a measurement, not a gate; always exits 0.")
    sys.exit(rc)
