"""Shipment-risk adapter — the at-risk cohort, derived from the real loss ledger.

Layout mirrors `adapters/growth/`: `contract` for the mirrored upstream vocabulary, `ladder` for
the pure severity function, `derive` for rows → payload, `connector` for the env seam.
"""
from .connector import RiskConnector, mode          # noqa: F401
from .contract import CATEGORIES, HEADINGS, RISK_ENDPOINTS, STRINGS   # noqa: F401
