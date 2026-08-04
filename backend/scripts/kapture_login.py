"""One-time HEADED Kapture login → seeds the session file so every later run is headless.

Kapture asks for an OTP after the password step, and only a human can supply it. So this runs a
VISIBLE browser, waits for you to type the OTP, then saves the authenticated storage state to
`kapture_session.json` (gitignored). `kapture_browse.pull_evidence(headless=True)` reuses it until
the session expires; re-run this when it does.

    cd backend && python scripts/kapture_login.py

Credentials come from KAPTURE_URL / KAPTURE_EMAIL / KAPTURE_PASSWORD, or from the gitignored
backend/data/kapture_creds.txt (KEY=VALUE lines). Nothing is printed except progress.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audit import kapture_browse as kb  # noqa: E402


def main() -> int:
    st = kb.status()
    if not st["playwright_installed"]:
        print("playwright is not installed:  pip install playwright && playwright install chromium")
        return 2
    if not st["configured"]:
        print(f"Kapture is not configured. Set KAPTURE_URL / KAPTURE_EMAIL / KAPTURE_PASSWORD, or "
              f"create backend/data/kapture_creds.txt (gitignored) with those KEY=VALUE lines.")
        return 2

    print(f"host: {st['base_url_host']}  (read-only audit evidence access)")
    if st["session_cached"]:
        print("a cached session already exists — this will refresh it")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)      # headed: you must see the OTP prompt
        context = browser.new_context()
        page = context.new_page()
        try:
            print("logging in… if an OTP is requested, type it in the browser window (5 min timeout)")
            kb.login(page, headless=False)
            print("login OK — session saved. Later runs can use headless=True.")
            return 0
        except kb.KaptureBrowseError as e:
            print(f"login failed [{e.code}]: {e}")
            return 1
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
