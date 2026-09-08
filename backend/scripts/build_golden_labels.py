"""Generate golden labels for the SYNTHETIC fixtures so `evaluate` runs on day one.

    python scripts/build_golden_labels.py

── WHAT THIS IS AND IS NOT ───────────────────────────────────────────────────────────────────
These are labels for the fixtures in backend/data/intake/fixtures/ — messages this repo wrote,
whose correct grouping is known by construction. They exist so `evaluate` has something to
score before any human labelling happens, and so the metric code itself can be tested.

THEY ARE NOT A MEASUREMENT OF THE REAL CHANNELS. A score against synthetic labels says the
pipeline agrees with the fixture author, which is a test, not evidence. The real numbers need
hand labels over the real firefighters and 28-Aug records, appended to the same CSV — the
format is identical, and rows for real messages simply sit alongside these.

Generated rows are marked `synthetic` in the `source` column so a mixed file can be scored
either way, and so a real-data number is never quoted with fixture rows folded into it.

── THE GROUND TRUTH ENCODED HERE ─────────────────────────────────────────────────────────────
  · every substantive top-level message anchors its own issue
  · a threaded reply belongs to its parent's issue
  · the two NQS messages 26h apart are TWO issues — they share only a hub
  · the MX1 cross-post is TWO issues, one per channel, linked as duplicates rather than merged
  · gated messages (joins, acks) belong to no issue and are omitted
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "data" / "intake" / "fixtures"
OUT = ROOT / "data" / "intake" / "golden" / "labels.csv"

#: text pattern -> intent id. Hand-authored against the fixture texts, which is the honest
#: description: a human decided each of these, and the file records who/what by `source`.
INTENT_RULES: list[tuple[str, str]] = [
    (r"daily closure|dc closure", "dc_closure"),
    (r"landing time", "line_haul_delay"),
    (r"line haul", "line_haul_delay"),
    (r"vehicle held", "vehicle_held"),
    (r"load not received", "load_not_received"),
    (r"negative payout", "negative_payout"),
    (r"invoices? not generating", "invoice_not_generating"),
    (r"payout not reflecting|payout not received", "payout_not_received"),
    (r"captain panel|login failing|otp not received", "captain_panel_login"),
    (r"pilot onboarding|onboarding sop", "pilot_onboarding"),
    (r"hardstop loss|tids", "rto_loss_revocation"),
    (r"festival manpower|absenteeism", "manpower_shortage"),
    (r"rainfall|heavy rain|^rain", "weather_callout"),
    (r"tat breach|rto shipments", "rto_loss_revocation"),
    (r"rvp pendency|drs not closing", "UNMAPPED"),
]

GATED_EXACT = {"noted, thanks"}


def intent_for(text: str) -> str:
    low = (text or "").lower()
    for pat, intent in INTENT_RULES:
        if re.search(pat, low):
            return intent
    return "UNMAPPED"


def main() -> int:
    records = []
    for f in sorted(FIXTURES.glob("*.ndjson")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    records.sort(key=lambda r: r["ts_epoch"])

    # anchor key -> issue id, matching app/intake/group.py's deterministic naming so a label
    # file stays valid across runs.
    anchors: dict[tuple[str, str], str] = {}
    rows = []
    for r in records:
        key = (r["channel_id"], r["message_id"])
        if r["subtype"] in ("channel_join", "channel_leave"):
            continue
        if (r["text"] or "").strip().lower() in GATED_EXACT:
            continue

        if r["thread_ref"]:
            parent = (r["channel_id"], r["thread_ref"])
            issue = anchors.get(parent)
            if issue is None:
                continue                      # orphan: no ground-truth issue to attach to
            intent = ""                       # intent is a property of the issue, not a reply
        else:
            issue = f"ISS-{r['channel_id']}-{r['message_id']}"
            anchors[key] = issue
            intent = intent_for(r["text"])

        rows.append({"channel_id": r["channel_id"], "message_id": r["message_id"],
                     "issue_id": issue, "intent": intent, "source": "synthetic"})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["channel_id", "message_id", "issue_id", "intent",
                                           "source"])
        w.writeheader()
        w.writerows(rows)

    issues = {r["issue_id"] for r in rows}
    intents: dict[str, int] = {}
    for r in rows:
        if r["intent"]:
            intents[r["intent"]] = intents.get(r["intent"], 0) + 1
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rows)} labelled messages, {len(issues)} issues")
    for k, v in sorted(intents.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<24} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
