"""Is PSP ready to receive tickets? Answers the question the sink's error cannot.

    python scripts/check_psp_ready.py                       # check the bot account only
    python scripts/check_psp_ready.py you@meesho.com        # also list users (prompts for pw)

The sink can only report "PSP rejected these credentials", because a login failure looks
identical whether the account is missing, the password is wrong, or the account was wiped by a
restart. This tells the three apart:

  · Is PSP reachable, and is it running the build that has the intake routes?
  · Does the bot account exist, and with which role?
  · Does its password actually work?

Give it your own approver email as an argument and it will list the users, which is the only
way to see whether the account exists at all. The password is read from a prompt or $PSP_ADMIN_PW
and is never written anywhere.
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.intake import sinks  # noqa: E402

OK, BAD, WARN = "  ok  ", "  XX  ", "  !!  "


def main() -> int:
    try:
        url = sinks._read(sinks.PSP_URL_FILE, "put PSP's base URL there").rstrip("/")
    except sinks.SinkError as e:
        print(f"{BAD} {e}")
        return 1
    print(f"       PSP at {url}\n")

    try:
        h = requests.get(f"{url}/api/health", timeout=60)
    except Exception as e:                                                # noqa: BLE001
        print(f"{BAD} cannot reach PSP: {type(e).__name__}: {e}")
        return 1
    if h.status_code != 200:
        print(f"{BAD} /api/health returned HTTP {h.status_code}")
        return 1
    print(f"{OK} reachable")

    # 401 means the route exists and is gated. 404 means this build predates the intake work.
    r = requests.get(f"{url}/api/intake/tickets", timeout=60)
    if r.status_code == 404:
        print(f"{BAD} /api/intake/tickets is 404 — this deployment is running older code. "
              f"Push and let it redeploy.")
        return 1
    print(f"{OK} intake routes deployed (HTTP {r.status_code} unauthenticated, as expected)")

    try:
        cred = sinks._read(sinks.PSP_LOGIN_FILE, "put email:password there").strip()
        bot_email, bot_pw = cred.split(":", 1)
    except (sinks.SinkError, ValueError) as e:
        print(f"{BAD} {e}")
        return 1
    print(f"       bot account: {bot_email}")

    b = requests.post(f"{url}/api/auth/login",
                      json={"email": bot_email, "password": bot_pw}, timeout=60)
    bot_ok = b.status_code == 200
    if bot_ok:
        role = (b.json().get("user") or {}).get("role")
        print(f"{OK} bot logs in — role {role!r}")
        if role != "agent":
            print(f"{WARN} role is {role!r}, not 'agent'. It will work, but the pipeline would "
                  f"hold more access than it needs. Change it in Team admin → Members.")
        t = requests.post(f"{url}/api/intake/tickets",
                          headers={"Authorization": f"Bearer {b.json()['token']}"},
                          json={"idempotency_key": "readiness-probe",
                                "title": "readiness probe — safe to delete"}, timeout=60)
        print(f"{OK if t.status_code == 200 else BAD} bot can create a ticket "
              f"(HTTP {t.status_code}) — 'readiness-probe' is harmless and idempotent")
    else:
        print(f"{BAD} bot login refused (HTTP {b.status_code}). Cause is one of: the account "
              f"does not exist, the password differs from the file, or a restart wiped the "
              f"user store.")

    # Only an approver can list users, and that is the only way to tell "missing" from "wrong
    # password" — so it needs a human credential and is opt-in.
    admin = sys.argv[1] if len(sys.argv) > 1 else None
    if not admin:
        if not bot_ok:
            print(f"\n{WARN} Re-run with your own email to see whether the account exists:\n"
                  f"       python scripts/check_psp_ready.py you@meesho.com")
        return 0 if bot_ok else 1

    pw = os.environ.get("PSP_ADMIN_PW") or getpass.getpass(f"password for {admin}: ")
    a = requests.post(f"{url}/api/auth/login", json={"email": admin, "password": pw}, timeout=60)
    if a.status_code != 200:
        print(f"{BAD} your login failed too (HTTP {a.status_code}) — so the problem is not the "
              f"bot account. Check the URL is the instance you are looking at in the browser.")
        return 1
    hdr = {"Authorization": f"Bearer {a.json()['token']}"}
    u = requests.get(f"{url}/api/auth/users", headers=hdr, timeout=60)
    if u.status_code != 200:
        print(f"{WARN} could not list users (HTTP {u.status_code}) — needs an approver.")
        return 1

    users = u.json().get("users", [])
    print(f"\n{OK} {len(users)} user(s) on this instance:")
    for x in users:
        mark = " <- the bot" if x["email"].lower() == bot_email.lower() else ""
        print(f"         {x['email']:<34} {x.get('role','?'):<10}{mark}")
    if not any(x["email"].lower() == bot_email.lower() for x in users):
        print(f"\n{BAD} {bot_email} is NOT on this instance. Create it in Team admin → add "
              f"member, role Agent, with the password from {sinks.PSP_LOGIN_FILE}.")
        return 1
    if not bot_ok:
        print(f"\n{BAD} the account exists but its password does not match the file. Either fix "
              f"the file, or recreate the account with that password.")
        return 1
    print(f"\n{OK} ready — run the pipeline with --sink psp")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
