#!/usr/bin/env python3
"""Probe Metabase API connectivity without needing an admin-issued API key.

Auth precedence (first one that is configured wins):
    1. METABASE_API_KEY   -> header  X-Api-Key: <key>
    2. METABASE_SESSION   -> header  X-Metabase-Session: <token>   (a live session
                             token, e.g. copied from the `metabase.SESSION` browser
                             cookie of an already-authenticated SSO/password login)
    3. METABASE_USER + METABASE_PASSWORD
                          -> POST {base}/api/session to mint a token, then use
                             X-Metabase-Session (fails for SSO-only accounts that
                             have no local password).

Config is env-first with a baked-file fallback that mirrors backend/scripts/load_env.sh:
a variable that is already set is never overwritten; otherwise we read it from
backend/data/metabase_*.txt (stripping CR/LF). Never reads a .env file.

    METABASE_URL       -> data/metabase_url.txt        (required, base URL)
    METABASE_API_KEY   -> data/metabase_api_key.txt
    METABASE_SESSION   -> data/metabase_session.txt
    METABASE_USER      -> data/metabase_user.txt
    METABASE_PASSWORD  -> data/metabase_password.txt
    METABASE_CARD_ID   -> data/metabase_card_id.txt    (optional; probe a saved card)

Set PSP_DATA to point at the data dir (default: <this file>/../data, i.e. backend/data).

Runs GET /api/database to confirm connectivity, then (if METABASE_CARD_ID is set)
POST /api/card/:id/query/json to confirm a card returns rows. Never prints the token,
password, api key, or cookie value (not even a fragment of it).

Timeouts:
    METABASE_TIMEOUT       -> connectivity timeout for /api/session + /api/database
                              (default 30s).
    METABASE_QUERY_TIMEOUT -> read timeout for the card query, which can be a real
                              (slow) SQL run, not just a connectivity check
                              (default 120s).

Usage:
    python metabase_probe.py
Exits 0 on success, nonzero on failure.

Dependencies: stdlib + requests + certifi only.
"""
from __future__ import annotations

import os
import sys

# Import third-party deps behind a clear, actionable message instead of a bare
# ModuleNotFoundError traceback if the environment is missing them.
try:
    import certifi
    import requests
except ImportError as _imp_err:  # pragma: no cover - environment setup guard
    sys.stderr.write(
        "ERROR: missing dependency "
        f"({_imp_err.name!r}). This probe needs 'requests' and 'certifi'.\n"
        "  Install with: python -m pip install requests certifi\n"
    )
    raise SystemExit(2)


def _int_env(var: str, default: int) -> int:
    """Read a positive-int env var, falling back to default on unset/invalid."""
    raw = os.environ.get(var)
    if not raw:
        return default
    try:
        val = int(raw.strip())
    except ValueError:
        return default
    return val if val > 0 else default


# --- HTTP session (mirrors backend/app/substrate/turso_http.py) ----------------
# One pooled HTTPS session reused for the whole process; certifi CA bundle for TLS.
_session = requests.Session()
_CA = certifi.where()
# Connectivity probes should be quick. The card query, however, can be a real (slow)
# SQL run, so it gets its own, longer read timeout — a slow-but-working card must not
# be reported as a failed probe.
_TIMEOUT = _int_env("METABASE_TIMEOUT", 30)  # seconds
_QUERY_TIMEOUT = _int_env("METABASE_QUERY_TIMEOUT", 120)  # seconds

# --- config: env-first, baked-file fallback (mirrors load_env.sh) --------------
# Default data dir is backend/data (this file lives in backend/scripts).
_DEFAULT_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
_DATA = os.environ.get("PSP_DATA", _DEFAULT_DATA)

# (env var name -> baked file basename), same convention as load_env.sh's set_from_file.
_FILE_FALLBACKS = {
    "METABASE_URL": "metabase_url.txt",
    "METABASE_API_KEY": "metabase_api_key.txt",
    "METABASE_SESSION": "metabase_session.txt",
    "METABASE_USER": "metabase_user.txt",
    "METABASE_PASSWORD": "metabase_password.txt",
    "METABASE_CARD_ID": "metabase_card_id.txt",
}


def _load(var: str) -> str | None:
    """Return env var if set (never overwritten), else the baked file's stripped
    contents, else None. Mirrors load_env.sh: a set var wins; file value is trimmed."""
    cur = os.environ.get(var)
    if cur:
        return cur
    fname = _FILE_FALLBACKS.get(var)
    if fname:
        path = os.path.join(_DATA, fname)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                val = fh.read().strip()  # strip CR/LF and surrounding whitespace
            if val:
                return val
        except (OSError, UnicodeDecodeError):
            pass
    return None


def _redact(secret: str | None) -> str:
    """Non-reversible presence indicator for a secret. Reveals ONLY whether it is set
    and its length — never any character of the actual value, not even a fragment."""
    if not secret:
        return "<unset>"
    return f"<set: {len(secret)} chars>"


class ProbeError(Exception):
    """A probe failure with an actionable, already-formatted message."""


# --- auth resolution -----------------------------------------------------------
def _mint_session_token(base: str, user: str, password: str) -> str:
    """POST /api/session with {username, password} -> session token (the `id` field).

    Raises ProbeError with SSO-aware guidance on failure. The password is sent only in
    the request body and is never included in any error message."""
    url = base + "/api/session"
    try:
        r = _session.post(
            url,
            json={"username": user, "password": password},
            timeout=_TIMEOUT,
            verify=_CA,
        )
    except requests.exceptions.Timeout:
        raise ProbeError(
            f"Timed out ({_TIMEOUT}s) POSTing {url}. Is the host reachable "
            "(on VPN / inside the corp network)?"
        )
    except requests.exceptions.SSLError as e:
        raise ProbeError(f"TLS error contacting {url}: {e}. Check the URL scheme/cert.")
    except requests.exceptions.ConnectionError as e:
        raise ProbeError(
            f"Could not connect to {url}: {e}. Check METABASE_URL and that you are "
            "on the network that can reach Metabase."
        )
    except requests.exceptions.RequestException as e:
        raise ProbeError(f"Request to {url} failed: {e}")

    if r.status_code in (400, 401):
        # Metabase returns 400/401 for bad creds AND for SSO-only accounts (no password).
        raise ProbeError(
            "Login rejected (HTTP {code}) by POST /api/session as user "
            "'{user}'.\n"
            "  - If this account signs in with Google/SAML SSO, it has NO local "
            "password, so username/password login cannot work. Instead, log in via "
            "the browser and copy the `metabase.SESSION` cookie value into "
            "METABASE_SESSION, or use METABASE_API_KEY.\n"
            "  - Otherwise the username or password is wrong.".format(
                code=r.status_code, user=user
            )
        )
    if r.status_code == 403:
        raise ProbeError(
            "HTTP 403 from POST /api/session — login is likely disabled or blocked "
            "(e.g. SSO-only instance, or too many attempts). Use METABASE_SESSION "
            "(browser cookie) or METABASE_API_KEY instead."
        )
    try:
        r.raise_for_status()
    except requests.exceptions.HTTPError as e:
        raise ProbeError(f"Unexpected error from POST /api/session: {e}")

    try:
        token = r.json().get("id")
    except ValueError:
        raise ProbeError("POST /api/session returned a non-JSON body; cannot read token.")
    if not token:
        raise ProbeError("POST /api/session succeeded but no 'id' token was returned.")
    return token


def resolve_auth(base: str) -> tuple[dict[str, str], str]:
    """Return (auth_headers, description) using the documented fallback order.

    Never returns or logs the secret value itself."""
    api_key = _load("METABASE_API_KEY")
    if api_key:
        return {"X-Api-Key": api_key}, "API key (X-Api-Key)"

    token = _load("METABASE_SESSION")
    if token:
        return {"X-Metabase-Session": token}, "session token (X-Metabase-Session)"

    user = _load("METABASE_USER")
    password = _load("METABASE_PASSWORD")
    if user and password:
        token = _mint_session_token(base, user, password)
        return (
            {"X-Metabase-Session": token},
            f"session minted via POST /api/session as {user}",
        )
    if user and not password:
        # SSO-only accounts commonly have a username but no local password; make the
        # remediation explicit instead of silently falling through to "no credentials".
        raise ProbeError(
            f"METABASE_USER is set (as '{user}') but METABASE_PASSWORD is not. "
            "If this account uses Google/SAML SSO it has no local password — log in "
            "via the browser and copy the `metabase.SESSION` cookie value into "
            "METABASE_SESSION, or use METABASE_API_KEY. Otherwise set METABASE_PASSWORD."
        )

    raise ProbeError(
        "No usable credentials found. Set ONE of:\n"
        "  - METABASE_API_KEY\n"
        "  - METABASE_SESSION (a live session token / metabase.SESSION cookie value)\n"
        "  - METABASE_USER and METABASE_PASSWORD\n"
        "via env vars or backend/data/metabase_*.txt."
    )


# --- API calls -----------------------------------------------------------------
def _explain_status(resp: requests.Response, what: str) -> None:
    """Raise ProbeError with actionable guidance for common auth/permission codes."""
    code = resp.status_code
    if code == 401:
        raise ProbeError(
            f"HTTP 401 Unauthorized on {what}. The credential is missing/expired/"
            "invalid. Session tokens expire (default ~14 days, MAX_SESSION_AGE) — "
            "re-copy the metabase.SESSION cookie or re-run with user/password. "
            "For an API key, confirm it is active."
        )
    if code == 403:
        raise ProbeError(
            f"HTTP 403 Forbidden on {what}. You authenticated, but your account/"
            "group lacks permission for this resource. Ask an admin to grant your "
            "group data access, or use a card/database you already have access to."
        )
    try:
        resp.raise_for_status()
    except requests.exceptions.HTTPError as e:
        body = (resp.text or "")[:300]
        raise ProbeError(f"HTTP error on {what}: {e}\n  body: {body}")


def _get(base: str, path: str, headers: dict[str, str]) -> requests.Response:
    url = base + path
    try:
        return _session.get(url, headers=headers, timeout=_TIMEOUT, verify=_CA)
    except requests.exceptions.Timeout:
        raise ProbeError(f"Timed out ({_TIMEOUT}s) on GET {path}.")
    except requests.exceptions.SSLError as e:
        raise ProbeError(f"TLS error on GET {path}: {e}. Check the URL scheme/cert.")
    except requests.exceptions.ConnectionError as e:
        raise ProbeError(f"Could not connect on GET {path}: {e}")
    except requests.exceptions.RequestException as e:
        raise ProbeError(f"Request failed on GET {path}: {e}")


def _post(
    base: str, path: str, headers: dict[str, str], timeout: int
) -> requests.Response:
    url = base + path
    try:
        # Empty JSON body; the query endpoints accept it. Content-Type set via json=.
        return _session.post(url, headers=headers, json={}, timeout=timeout, verify=_CA)
    except requests.exceptions.Timeout:
        raise ProbeError(
            f"Timed out ({timeout}s) on POST {path}. The query may be slow; raise "
            "METABASE_QUERY_TIMEOUT (seconds) or run the query directly for large "
            "results."
        )
    except requests.exceptions.SSLError as e:
        raise ProbeError(f"TLS error on POST {path}: {e}. Check the URL scheme/cert.")
    except requests.exceptions.ConnectionError as e:
        raise ProbeError(f"Could not connect on POST {path}: {e}")
    except requests.exceptions.RequestException as e:
        raise ProbeError(f"Request failed on POST {path}: {e}")


def probe_databases(base: str, headers: dict[str, str]) -> int:
    """GET /api/database and print each db as: id | name | engine. Returns count."""
    resp = _get(base, "/api/database", headers)
    _explain_status(resp, "GET /api/database")
    try:
        payload = resp.json()
    except ValueError:
        raise ProbeError("GET /api/database returned a non-JSON body.")

    # Metabase may return a bare list or {"data": [...], "total": N} depending on version.
    if isinstance(payload, dict):
        dbs = payload.get("data", [])
    else:
        dbs = payload
    if not isinstance(dbs, list):
        raise ProbeError("GET /api/database returned an unexpected shape.")

    print(f"Databases ({len(dbs)}):")
    print("  id | name | engine")
    for db in dbs:
        if not isinstance(db, dict):
            continue
        print(f"  {db.get('id')} | {db.get('name')} | {db.get('engine')}")
    return len(dbs)


def probe_card(base: str, headers: dict[str, str], card_id: str) -> None:
    """POST /api/card/:id/query/json; print row count + first row's key names only.

    Uses the longer query timeout — a saved card can wrap a genuinely slow SQL query,
    and a slow-but-working card should not be reported as a failed probe."""
    path = f"/api/card/{card_id}/query/json"
    resp = _post(base, path, headers, _QUERY_TIMEOUT)
    _explain_status(resp, f"POST {path}")
    try:
        payload = resp.json()
    except ValueError:
        raise ProbeError(f"POST {path} returned a non-JSON body.")

    # /query/json returns a flat array of row objects keyed by column display name.
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        # Some builds/errors return an envelope; surface a Metabase error if present.
        if payload.get("status") == "failed" or "error" in payload:
            err = payload.get("error") or payload.get("message") or "unknown error"
            raise ProbeError(f"Card {card_id} query failed: {str(err)[:300]}")
        data = payload.get("data")
        rows = data.get("rows", []) if isinstance(data, dict) else []
    else:
        raise ProbeError(f"POST {path} returned an unexpected shape.")

    print(f"Card {card_id}: {len(rows)} row(s).")
    if rows:
        first = rows[0]
        if isinstance(first, dict):
            keys = list(first.keys())
        elif isinstance(first, list):
            # envelope form: pull column names from data.cols if available
            cols = []
            if isinstance(payload, dict):
                data = payload.get("data")
                if isinstance(data, dict):
                    cols = [c.get("name") for c in data.get("cols", []) if isinstance(c, dict)]
            keys = cols or [f"col{i}" for i in range(len(first))]
        else:
            keys = []
        print(f"  columns: {keys}")


# --- orchestration -------------------------------------------------------------
def run_probe() -> None:
    base = _load("METABASE_URL")
    if not base:
        raise ProbeError(
            "METABASE_URL is required. Set it via env or backend/data/metabase_url.txt "
            "(e.g. https://metabase.example.internal)."
        )
    base = base.rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ProbeError(
            f"METABASE_URL must start with http:// or https:// (got: {base!r})."
        )

    headers, how = resolve_auth(base)
    # Redacted diagnostics only — never the secret itself, not even a fragment.
    secret_val = next(iter(headers.values()), None)
    print(f"Metabase: {base}")
    print(f"Auth: {how}  [credential {_redact(secret_val)}]")

    n = probe_databases(base, headers)
    if n == 0:
        print("  (no databases visible to this credential)")

    card_id = _load("METABASE_CARD_ID")
    if card_id:
        probe_card(base, headers, card_id)
    else:
        print("METABASE_CARD_ID not set; skipping card query probe.")

    print("OK: probe succeeded.")


def main() -> int:
    try:
        run_probe()
    except ProbeError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — last-resort guard; never leak secrets in trace
        # Secrets travel only in headers / the /api/session request body, neither of
        # which requests echoes into exception text (it reports URL + status only), so
        # printing type + message here cannot disclose a credential.
        print(f"\nUNEXPECTED ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
