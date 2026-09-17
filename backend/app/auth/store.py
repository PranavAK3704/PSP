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

from ..durable_state import read_json, write_json

#: `agent` works the intake ticket register and sees NOTHING else in PSP. It is deliberately
#: outside the viewer/author/approver ladder rather than below it: those three are about
#: authoring rights over SOPs and go-live, and an intake agent has none of them at any level.
#: Enforcement is in main.py — `_authed` excludes `agent`, so a new endpoint is closed to
#: agents by default and has to opt in.
ROLES = {"author", "approver", "viewer", "agent"}

#: The L3 team a user works in — orthogonal to `role`, which is about authoring rights.
#
# `role` answers "may this person publish an SOP"; `team` answers "whose queue is this person
# looking at". Conflating them was never an option: an approver in Payments and an approver in
# Losses need the same publishing rights and completely different inboxes.
#
# The empty string means UNASSIGNED and is the default, deliberately: it must be possible to have
# an account before someone decides which desk you sit at, and an unassigned user sees the whole
# queue rather than an empty one. Validated against `l3.platform.TEAM_SLA` at write time so a typo
# cannot create a team that no case can ever be routed to.
UNASSIGNED_TEAM = ""
_ITERATIONS = 200_000
_lock = threading.Lock()


def _hash(password: str, salt_hex: str) -> str:
    salt = bytes.fromhex(salt_hex)
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS).hex()


def _make_user(email: str, name: str, role: str, password: str, team: str = "") -> dict:
    salt = os.urandom(16).hex()
    email = (email or "").strip().lower()
    return {
        "email": email,
        "name": (name or "").strip() or email,
        "role": role if role in ROLES else "viewer",
        "team": _valid_team(team),
        "salt": salt,
        "pw_hash": _hash(password, salt),
    }


def teams() -> list[str]:
    """The canonical team list, read from the SLA table rather than re-declared here.

    One list. `l3.platform.TEAM_SLA` already has to name every team because it carries their SLA
    and escalation ladder, and a second copy in auth would drift the moment a team is added —
    with the failure mode being a user assigned to a team no case can route to."""
    from ..l3.platform import TEAM_SLA
    return sorted(TEAM_SLA)


def _valid_team(team: str | None) -> str:
    t = (team or "").strip()
    return t if t in set(teams()) else UNASSIGNED_TEAM


def _public(u: dict) -> dict:
    """Strip secrets — only ever expose email/name/role."""
    return {"email": u.get("email", ""), "name": u.get("name", ""),
            "role": u.get("role", "viewer"), "team": u.get("team", UNASSIGNED_TEAM)}


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


def create_user(email: str, name: str, role: str, password: str, team: str = "") -> dict:
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
        u = _make_user(email, name, role, password, team)
        users.append(u)
        _save(users)
    return _public(u)


def set_team(email: str, team: str) -> dict:
    """Assign (or clear, with "") a user's L3 team. Returns the public view."""
    email = (email or "").strip().lower()
    t = _valid_team(team)
    if team and not t:
        raise ValueError(f"unknown team {team!r} — must be one of {teams()}")
    with _lock:
        users = _load()
        for u in users:
            if u.get("email") == email:
                u["team"] = t
                _save(users)
                return _public(u)
    raise ValueError("no such user")


def set_role(email: str, role: str) -> dict:
    """Change a user's role. Returns the public view.

    Separate from create so fixing a wrong role does not mean deleting and recreating an
    account — which for a service account means rotating its password too.

    ── THE LAST-APPROVER GUARD ───────────────────────────────────────────────────────────────
    Role is the only thing that lets anyone administer this system. Demoting the final approver
    leaves an instance nobody can add a user to, change a role in, or approve anything on, and
    the only way back is editing the store by hand on the server. So that one move is refused.
    It is not a permission check — an approver is *allowed* to do this — it is a check that the
    system remains administrable afterwards.

    Note that role lives in the SIGNED TOKEN, so unlike a team change this takes effect at the
    user's next LOGIN, not their next request. Their current session keeps the old role until it
    expires. That is the price of not trusting the client for role, and it is the right trade.
    """
    email = (email or "").strip().lower()
    if role not in ROLES:
        raise ValueError(f"role must be one of {sorted(ROLES)}")
    with _lock:
        users = _load()
        target = next((u for u in users if u.get("email") == email), None)
        if target is None:
            raise ValueError("no such user")
        if target.get("role") == "approver" and role != "approver":
            approvers = sum(1 for u in users if u.get("role") == "approver")
            if approvers <= 1:
                raise ValueError(
                    "refusing to demote the only approver — nobody would be able to administer "
                    "this instance afterwards. Make someone else an approver first.")
        target["role"] = role
        _save(users)
        return _public(target)


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
