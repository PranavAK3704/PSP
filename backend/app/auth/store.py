"""User store — <STATE_DIR>/users.json.

Each user: {email, name, role, salt, pw_hash}. role ∈ {author, approver, viewer}.
Passwords are NEVER stored in plaintext: pw_hash = pbkdf2_hmac('sha256', password,
salt, 200_000) with a per-user random 16-byte salt (hex). Reads that leave this
module (list_users / verify_password / seed return values) NEVER include salt/hash.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading

from ..state_paths import state_path
from ..durable_state import read_json, write_json

ROLES = {"author", "approver", "viewer"}
_ITERATIONS = 200_000
_lock = threading.Lock()


def _path() -> str:
    return state_path("users.json")


def _hash(password: str, salt_hex: str) -> str:
    salt = bytes.fromhex(salt_hex)
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS).hex()


def _make_user(email: str, name: str, role: str, password: str) -> dict:
    salt = os.urandom(16).hex()
    email = (email or "").strip().lower()
    return {
        "email": email,
        "name": (name or "").strip() or email,
        "role": role if role in ROLES else "viewer",
        "salt": salt,
        "pw_hash": _hash(password, salt),
    }


def _public(u: dict) -> dict:
    """Strip secrets — only ever expose email/name/role."""
    return {"email": u.get("email", ""), "name": u.get("name", ""),
            "role": u.get("role", "viewer")}


def _load() -> list[dict]:
    # Durable: reads Turso first (falls back to the local users.json when Turso is off).
    data = read_json("users.json", [])
    return data if isinstance(data, list) else []


def _save(users: list[dict]) -> None:
    # Durable: mirrors to Turso so accounts survive a free-tier restart.
    write_json("users.json", users)


def get_user(email: str) -> dict | None:
    """FULL user record (incl. salt/hash) — internal use only (auth checks)."""
    e = (email or "").strip().lower()
    for u in _load():
        if u.get("email") == e:
            return u
    return None


def list_users() -> list[dict]:
    """All users, secrets stripped — safe to return over the API."""
    return [_public(u) for u in _load()]


def create_user(email: str, name: str, role: str, password: str) -> dict:
    """Create a user. Raises ValueError on bad input / duplicate. Returns the public view."""
    email = (email or "").strip().lower()
    if not email or not password:
        raise ValueError("email and password are required")
    if role not in ROLES:
        raise ValueError(f"role must be one of {sorted(ROLES)}")
    with _lock:
        users = _load()
        if any(u.get("email") == email for u in users):
            raise ValueError("a user with that email already exists")
        u = _make_user(email, name, role, password)
        users.append(u)
        _save(users)
    return _public(u)


def verify_password(email: str, password: str) -> dict | None:
    """Return the public user view if the password matches, else None (constant-time compare)."""
    u = get_user(email)
    if not u or not u.get("salt") or not u.get("pw_hash"):
        return None
    if hmac.compare_digest(_hash(password or "", u["salt"]), u["pw_hash"]):
        return _public(u)
    return None


def seed_initial() -> None:
    """First-run seed: if the store is empty, create ONE approver.

    From INITIAL_ADMIN_EMAIL / INITIAL_ADMIN_PASSWORD when set; otherwise the
    documented default admin@valmo.local / valmo-admin (with a stderr WARNING to
    change it — this default is documented, not a secret)."""
    with _lock:
        if _load():
            return
        email = os.environ.get("INITIAL_ADMIN_EMAIL")
        password = os.environ.get("INITIAL_ADMIN_PASSWORD")
        if email and password:
            u = _make_user(email, "Initial Admin", "approver", password)
        else:
            u = _make_user("admin@valmo.local", "Valmo Admin", "approver", "valmo-admin")
            print("WARNING: seeding default approver admin@valmo.local / valmo-admin — "
                  "set INITIAL_ADMIN_EMAIL and INITIAL_ADMIN_PASSWORD and change this "
                  "before the pilot.", file=sys.stderr)
        _save([u])
