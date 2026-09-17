"""Pre-demo readiness check for the Slack side. READ ONLY — it posts nothing.

    python scripts/check_slack_ready.py

Run this before a demo. It answers the three things that actually go wrong on the day:

  1. Is the token alive, and does it still have ONLY read scopes?
  2. Which channels can the bot actually read? (An uninvited bot fails with `not_in_channel`,
     and a private channel is invisible until invited — it does not even appear in a listing.)
  3. Does each channel have recent traffic? A channel that is connected but silent looks
     exactly like one that is broken, and you do not want to discover which during the demo.

It prints the exact command to launch the dashboard on whatever it found.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.intake import slack_source  # noqa: E402

API = "https://slack.com/api"
OK, BAD, WARN = "  ok  ", "  XX  ", "  !!  "


def main() -> int:
    try:
        tok = slack_source.read_token()
    except slack_source.SlackError as e:
        print(f"{BAD} {e}")
        return 1
    H = {"Authorization": f"Bearer {tok}"}

    r = requests.get(f"{API}/auth.test", headers=H, timeout=20)
    d = r.json()
    if not d.get("ok"):
        print(f"{BAD} auth.test failed: {d.get('error')}")
        return 1
    print(f"{OK} token live — workspace '{d.get('team')}', bot '{d.get('user')}'")

    scopes = [s for s in (r.headers.get("X-OAuth-Scopes") or "").split(",") if s]
    writes = [s for s in scopes if "write" in s or "post" in s]
    print(f"{OK} {len(scopes)} scopes granted")
    if writes:
        # Not a failure, but it must never pass silently: the whole safety story for this
        # module is that the credential itself cannot post.
        print(f"{WARN} WRITE SCOPES PRESENT: {', '.join(writes)} — the read-only guarantee "
              f"now rests on code rather than on the token")
    else:
        print(f"{OK} zero write scopes — this credential cannot post, reply or react")

    chans, cur = [], None
    for _ in range(8):
        p = {"types": "public_channel,private_channel", "limit": 200,
             "exclude_archived": "true"}
        if cur:
            p["cursor"] = cur
        j = requests.get(f"{API}/users.conversations", headers=H, params=p, timeout=30).json()
        if not j.get("ok"):
            print(f"{BAD} users.conversations: {j.get('error')}")
            return 1
        chans += j.get("channels", [])
        cur = (j.get("response_metadata") or {}).get("next_cursor")
        if not cur:
            break
        time.sleep(0.3)

    if not chans:
        print(f"\n{BAD} the bot is not in any channel yet.")
        print("       In Slack, in each channel you want read:  /invite @psintake")
        return 1

    print(f"\n{OK} member of {len(chans)} channel(s). Checking each for recent traffic:\n")
    ready = []
    cutoff = time.time() - 7 * 86400
    for c in sorted(chans, key=lambda c: c.get("name", "")):
        j = requests.get(f"{API}/conversations.history", headers=H,
                         params={"channel": c["id"], "limit": 5}, timeout=30).json()
        if not j.get("ok"):
            print(f"{BAD} #{c.get('name'):<26} {c['id']:<14} {j.get('error')}")
            continue
        msgs = j.get("messages", [])
        recent = sum(1 for m in msgs if float(m.get("ts", 0)) > cutoff)
        kind = "private" if c.get("is_private") else "public"
        if not msgs:
            print(f"{WARN} #{c.get('name'):<26} {c['id']:<14} {kind}, readable but EMPTY")
        else:
            print(f"{OK} #{c.get('name'):<26} {c['id']:<14} {kind}, "
                  f"{len(msgs)} recent ({recent} in the last 7 days)")
        ready.append(c["id"])
        time.sleep(0.25)

    print(f"\n{'=' * 78}\nREADY. Launch the dashboard on everything the bot can read:\n")
    print(f"  .venv/bin/python -m app.intake.live_demo \\\n"
          f"      --channel {','.join(ready)} --since-now\n")
    print("  --since-now starts the feed empty from this moment. The app has no write scope so")
    print("  it cannot delete history; this is how a demo gets a clean feed without touching")
    print("  Slack. Drop it to replay everything the bot can see.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
