"""Authentication + role-based access control (stdlib only).

  store  — users.json (pbkdf2 hashed passwords, per-user salt) + seed_initial()
  tokens — compact HMAC-signed session tokens (12h expiry)
  deps   — FastAPI current_user / require_role dependencies
"""
from __future__ import annotations

from . import deps, store, tokens
from .deps import current_user, require_role

__all__ = ["store", "tokens", "deps", "current_user", "require_role"]
