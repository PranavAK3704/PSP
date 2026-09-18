"""The three things that must be true for the deployment to be doing its job.

    python scripts/check_live.py

Intent detection, polling, acknowledgement. Each of the three has failed silently at least once
— classification was off for a day because an index upload reported success and did not persist;
polling never started because one env var was missing; acknowledgement reported "sent" while
every message died in TLS. In all three the symptom was the same: nothing new appeared, and
nothing said why.

So this asks the deployment directly and prints a reason for every "no". It reads only; it
creates nothing and sends nothing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OK, BAD, WARN = "  ok  ", "  XX  ", "  !!  "


def main() -> int:
    url_file = ROOT / "data" / "psp_url.txt"
    if not url_file.exists():
        print(f"{BAD} {url_file} not found")
        return 1
    url = url_file.read_text().strip().rstrip("/")

    login = os.environ.get("PSP_ADMIN")
    if not login:
        cred = ROOT / "data" / "psp_login.txt"
        login = cred.read_text().strip() if cred.exists() else ""
    if ":" not in login:
        print(f"{BAD} no credentials — set $PSP_ADMIN=email:password or fill data/psp_login.txt")
        return 1
    email, pw = login.split(":", 1)

    try:
        r = requests.post(f"{url}/api/auth/login", json={"email": email, "password": pw},
                          timeout=90)
    except Exception as e:                                                # noqa: BLE001
        print(f"{BAD} cannot reach {url}: {type(e).__name__}: {e}")
        return 1
    if r.status_code != 200:
        print(f"{BAD} login refused (HTTP {r.status_code}) for {email}")
        return 1
    h = {"Authorization": f"Bearer {r.json()['token']}"}
    print(f"       {url}\n")
    warned = 0

    p = requests.get(f"{url}/api/intake/poller", headers=h, timeout=90).json()
    t = requests.get(f"{url}/api/intake/tickets", headers=h, timeout=90).json()
    x = requests.get(f"{url}/api/intake/exemplars", headers=h, timeout=90).json()
    failed = 0

    # ── 1. INTENT DETECTION ──────────────────────────────────────────────────────────────────
    # "Loaded" is not enough: the index has twice been present in one container's cache and
    # absent from the store, so the only trustworthy signal is a poll that actually classified.
    if not x.get("loaded"):
        print(f"{BAD} INTENT DETECTION — no exemplar index"
              + (f" ({x['error']})" if x.get("error") else ""))
        print("       fix: python -m app.intake.cli push-exemplars")
        failed += 1
    elif p.get("classified") is False:
        print(f"{BAD} INTENT DETECTION — index has {x['count']} exemplars but the last poll did "
              f"not classify: {p.get('classify_note')}")
        failed += 1
    elif p.get("classified") is None:
        print(f"{WARN} INTENT DETECTION — {x['count']} exemplars loaded, no poll has completed "
              f"yet since the last restart")
        warned += 1
    else:
        uncategorised = sum(1 for k in t.get("tickets", []) if not k.get("disposition"))
        print(f"{OK} INTENT DETECTION — {x['count']} exemplars, last poll: "
              f"{p.get('classify_note')}")
        if uncategorised:
            print(f"       {uncategorised} ticket(s) awaiting a human category (NOVEL)")

    # ── 2. POLLING ───────────────────────────────────────────────────────────────────────────
    if not p.get("running"):
        print(f"{BAD} POLLING — not running: {p.get('start_reason')}")
        for k, v in (p.get("env") or {}).items():
            if v == "MISSING":
                print(f"       ${k} is MISSING")
        failed += 1
    elif not p.get("polls"):
        print(f"{WARN} POLLING — started, no cycle completed yet "
              f"({p.get('start_reason')})")
        warned += 1
    else:
        print(f"{OK} POLLING — {p['polls']} poll(s), last {p.get('last_at')}, "
              f"{p.get('messages')} messages / {p.get('issues')} issues last cycle")
        if p.get("last_error"):
            print(f"{WARN}   last error: {str(p['last_error'])[:110]}")

    # ── 3. ACKNOWLEDGEMENT ───────────────────────────────────────────────────────────────────
    tickets = t.get("tickets", [])
    sent = [k for k in tickets if k.get("acknowledged_at")]
    errs = [k for k in tickets if k.get("acknowledge_error")]
    mode = (p.get("env") or {}).get("INTAKE_NOTIFY", "?")
    if mode.startswith("(default") or mode == "off":
        print(f"{BAD} ACKNOWLEDGEMENT — off ($INTAKE_NOTIFY={mode})")
        failed += 1
    elif mode == "email-dry":
        print(f"{WARN} ACKNOWLEDGEMENT — DRY RUN, nothing is being sent "
              f"($INTAKE_NOTIFY=email-dry)")
        warned += 1
    elif errs and not sent:
        print(f"{BAD} ACKNOWLEDGEMENT — every send failed: {errs[0]['acknowledge_error'][:90]}")
        failed += 1
    elif errs:
        print(f"{WARN} ACKNOWLEDGEMENT — {len(sent)} sent, {len(errs)} failing: "
              f"{errs[0]['acknowledge_error'][:80]}")
        warned += 1
    elif sent:
        print(f"{OK} ACKNOWLEDGEMENT — {len(sent)} of {len(tickets)} ticket(s) acknowledged")
    else:
        print(f"{WARN} ACKNOWLEDGEMENT — on, but nothing acknowledged yet")
        warned += 1

    # A warning is not "ok". Saying ALL THREE OK while four acknowledgements were failing is
    # the same class of lie as a ticket showing "needs a category" when nothing is classifying.
    verdict = (f"{failed} BROKEN" if failed
               else f"{warned} needs attention" if warned
               else "ALL THREE OK")
    print(f"\n       tickets: {t.get('total', 0)}   {verdict}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
