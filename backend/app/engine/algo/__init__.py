"""Deterministic tiers — the pre-router and its matchers.

Importing this package installs the shipped tiers into the router. Kept here rather than at the
bottom of router.py so that a harness can import `router` alone and exercise it with an empty
registry — proving the frame is inert independently of the tiers plugged into it.
"""
from . import router as _router          # noqa: F401

_router._install_default_tiers()
