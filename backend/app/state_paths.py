"""Durable-state path resolver.

The MUTABLE JSON stores (users, blueprints, kt_queue, audit rubric/audits, traces,
concern log, cpd) must survive a redeploy. In production (Render) they live on a
persistent disk mounted at $PSP_STATE_DIR (e.g. /data); locally, PSP_STATE_DIR is
unset so they default to the current backend/data dir — identical to before.

STATIC corpus (data/knowledge/, data/samples/, valmo*.db, *.txt) is NOT routed
here — it stays baked with the code / read from Turso in prod.
"""
from __future__ import annotations

import os
from pathlib import Path

# Default = the existing backend/data dir (state_paths.py lives at backend/app/).
_DEFAULT_DIR = Path(__file__).resolve().parents[1] / "data"


def state_dir() -> str:
    """The durable-state directory ($PSP_STATE_DIR or backend/data), created if absent."""
    d = os.environ.get("PSP_STATE_DIR") or str(_DEFAULT_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def state_path(name: str) -> str:
    """Absolute path to a mutable state file under the durable-state directory."""
    return os.path.join(state_dir(), name)
