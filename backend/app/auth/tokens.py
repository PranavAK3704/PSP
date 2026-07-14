"""Compact HMAC-signed session tokens (stdlib only — no pyjwt).

token = b64url(json(payload)) + "." + b64url(hmac_sha256(SECRET, first_part))
payload = {"email", "role", "exp"}   exp = now + 12h

verify() returns the payload dict iff the signature matches AND exp is in the
future, else None. SECRET comes from env AUTH_SECRET; if unset we fall back to a
fixed dev string and warn ONCE on stderr (never for production — set AUTH_SECRET).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time

_TTL_SECONDS = 12 * 3600
# Documented dev fallback — NOT a secret. Production MUST set AUTH_SECRET.
_DEV_SECRET = "psp-dev-auth-secret-change-me"
_warned = False


def _secret() -> bytes:
    global _warned
    s = os.environ.get("AUTH_SECRET")
    if not s:
        if not _warned:
            print("WARNING: AUTH_SECRET is not set — using an insecure dev fallback. "
                  "Set AUTH_SECRET in production so tokens can't be forged.", file=sys.stderr)
            _warned = True
        s = _DEV_SECRET
    return s.encode()


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sig(body: str) -> str:
    return _b64u(hmac.new(_secret(), body.encode("ascii"), hashlib.sha256).digest())


def sign(payload: dict) -> str:
    """Sign an arbitrary payload dict into a compact token."""
    body = _b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return body + "." + _sig(body)


def make_token(email: str, role: str) -> str:
    """Session token for a user — carries email, role and a 12h expiry."""
    return sign({"email": email, "role": role, "exp": time.time() + _TTL_SECONDS})


def verify(token: str) -> dict | None:
    """Return the payload dict if the signature is valid AND not expired, else None."""
    if not token or "." not in token:
        return None
    body, _, sig = token.partition(".")
    if not hmac.compare_digest(sig, _sig(body)):
        return None
    try:
        payload = json.loads(_b64u_decode(body))
    except Exception:  # noqa: BLE001
        return None
    exp = payload.get("exp") if isinstance(payload, dict) else None
    if not isinstance(exp, (int, float)) or time.time() > exp:
        return None
    return payload
