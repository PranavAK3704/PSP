"""FastAPI auth dependencies — server-side role enforcement.

current_user  : reads `Authorization: Bearer <token>` (or `X-Auth-Token`), verifies
                the signature + expiry, returns {email, role}; raises 401 otherwise.
require_role  : dependency factory → 403s unless the caller's role is allowed.
                An approver implicitly satisfies any author-level check (approver ≥ author).
The client is NEVER trusted for role — the role is read from the signed token only.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from . import tokens


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-auth-token")


def current_user(request: Request) -> dict:
    """Authenticated user {email, role} from the bearer token, or HTTP 401."""
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="authentication required")
    payload = tokens.verify(token)
    if not payload:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return {"email": payload.get("email"), "role": payload.get("role")}


def require_role(*roles: str):
    """Dependency that 403s unless current_user's role is in `roles`
    (approver also satisfies an 'author' requirement)."""
    allowed = set(roles)

    def dep(user: dict = Depends(current_user)) -> dict:
        role = user.get("role")
        if role in allowed or (role == "approver" and "author" in allowed):
            return user
        raise HTTPException(status_code=403,
                            detail="requires role: " + " or ".join(sorted(allowed)))

    return dep
