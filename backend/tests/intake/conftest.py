"""Pytest fixtures for the intake module.

`INTAKE_DB` is set PER TEST to a tmp_path. That is not just tidiness — `app/intake/store.py`
resolves its path inside `db_path()` rather than binding it at import time, precisely so a test
can redirect it after the module is imported. Binding a store path at module level is the trap
`scripts/_contain.py` documents: a harness that imports before containing writes to the real
file and still passes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))          # backend/ is not an installable package


@pytest.fixture()
def intake_db(tmp_path, monkeypatch):
    """A fresh, isolated store for one test. Yields the path."""
    p = tmp_path / "intake.db"
    monkeypatch.setenv("INTAKE_DB", str(p))
    return p


@pytest.fixture()
def con(intake_db):
    from app.intake import store
    c = store.connect()
    yield c
    c.close()


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return BACKEND / "data" / "intake" / "fixtures"
