"""Minimal Turso / libSQL client over the HTTP pipeline API (/v2/pipeline).

Uses `requests` (+ certifi for TLS) — robust everywhere (local macOS + Linux container),
no WebSocket/native deps. Values use Hrana encoding; we store everything as TEXT so encode/
decode stays trivial.
"""
from __future__ import annotations

import certifi
import requests

# One pooled HTTPS session for the whole process — reuses the TCP connection + TLS session
# across queries instead of a fresh handshake each call (~100ms saved per query).
_session = requests.Session()
_CA = certifi.where()


def _endpoint(url: str) -> str:
    return url.replace("libsql://", "https://").rstrip("/") + "/v2/pipeline"


def _arg(v):
    return {"type": "null"} if v is None else {"type": "text", "value": str(v)}


def _cell(c):
    return None if c.get("type") == "null" else c.get("value")


def execute(url: str, token: str, sql: str, args=()) -> list[dict]:
    body = {"requests": [{"type": "execute", "stmt": {"sql": sql, "args": [_arg(a) for a in args]}},
                         {"type": "close"}]}
    r = _session.post(_endpoint(url), headers={"Authorization": "Bearer " + token},
                      json=body, timeout=30, verify=_CA)
    r.raise_for_status()
    res = r.json()["results"][0]
    if res.get("type") != "ok":
        raise RuntimeError(str(res.get("error", res))[:200])
    result = res["response"]["result"]
    cols = [c["name"] for c in result["cols"]]
    return [dict(zip(cols, [_cell(c) for c in row])) for row in result["rows"]]


def batch(url: str, token: str, stmts, timeout: int = 120) -> None:
    """Run many (sql, args) statements in ONE HTTP round-trip, wrapped in a single
    transaction so the whole batch commits once (1000 autocommits → 1 = ~100x faster)."""
    reqs = [{"type": "execute", "stmt": {"sql": "BEGIN", "args": []}}]
    reqs += [{"type": "execute", "stmt": {"sql": s, "args": [_arg(a) for a in ar]}} for (s, ar) in stmts]
    reqs += [{"type": "execute", "stmt": {"sql": "COMMIT", "args": []}}]
    reqs.append({"type": "close"})
    r = _session.post(_endpoint(url), headers={"Authorization": "Bearer " + token},
                      json={"requests": reqs}, timeout=timeout, verify=_CA)
    r.raise_for_status()
    for res in r.json()["results"]:
        if res.get("type") != "ok":
            raise RuntimeError(str(res.get("error", res))[:200])
