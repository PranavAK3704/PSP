"""LocalDbProvider — the resolution engine's captain context, from the REAL loss ledger.

This is the third account-data provider, alongside:
  • DemoDataProvider (mock_connectors.py) — three hand-written captains. Perfect for a smoke
    test, useless as evidence: the engine cannot be wrong about a captain who does not exist.
  • PrismProvider (prism_provider.py) — the sanctioned production path, blocked on a client
    token and five confirmed table names.

It exists to close the gap between those two. The engine already grounded ONE thing in real
data — a disputed AWB, via loss_db.get_loss_by_awb on the money path — but everything the
model knew ABOUT THE CAPTAIN came from seed. So "why was I debited on VL…" was answered from
the real ledger while "show me my debits" was answered from fiction, in the same conversation.
This provider serves both from `backend/data/valmo.db` (or the same tables on Turso), keyed on
real `partner_id`, with no Meesho access required.

── WHAT IS DELIBERATELY MISSING ──────────────────────────────────────────────────────────────
The loss export contains no captain NAME, TIER, LANGUAGE, COD pendency, or shipment state. The
seed provider had all five because a person typed them. This provider returns those keys EMPTY
rather than plausible, because a realistic-looking name attached to a real partner_id is not a
harmless placeholder — it is a fabrication wearing real provenance, and every consumer here
(the engine prompt, the trace, the concern log) would carry it forward as fact. Callers already
treat these as optional: tools.py reads profile.get("name") and captain_context composes
shipments from Log10, not from here.

Consequence worth stating plainly: with this provider a COD/cash question has NO grounded
answer, and the engine should escalate rather than guess. That is the correct behaviour for a
question this dataset cannot answer, and it is visible instead of silently wrong.
"""
from __future__ import annotations

from .. import loss_db


class LocalDbProvider:
    source = "valmo.db loss-attribution ledger (real rows)"

    # get_losses takes only the captain id — `attribution` is partner-keyed directly, so unlike
    # PrismProvider there is no hub-code indirection to negotiate.
    accepts_hub_code = False

    # ── configuration / health ──
    def configured(self) -> bool:
        return loss_db.available()

    def status(self) -> dict:
        """Non-throwing probe for /api/health."""
        return {"provider": "localdb", "ok": loss_db.available(),
                "db_source": loss_db.source(),
                "detail": "" if loss_db.available()
                          else "no loss DB — set TURSO_* or provide backend/data/valmo.db"}

    # ── Captain-Context accessors (same contract as DemoDataProvider) ──
    def get_profile(self, captain_id: str) -> dict:
        """Hub + debit history for a real partner id. Empty dict when the id is unknown, which
        makes captain_context.get_context() return {} — the honest "we don't know this captain"
        path, rather than an empty shell that reads as a captain with no problems."""
        return loss_db.partner_profile(captain_id)

    def get_ledger(self, captain_id: str) -> list[dict]:
        """Debits and reversal credits. Debit-side only: the export has no payout rows, so a
        payout credit here would have to be invented."""
        return loss_db.partner_ledger(captain_id)

    def get_losses(self, captain_id: str) -> list[dict]:
        return loss_db.partner_losses(captain_id)

    def get_summary(self, captain_id: str) -> dict:
        """Counts and totals over the captain's FULL debit history, aggregated in SQL.

        This is the value the engine sends to the model instead of rows (see
        engine/dataplane.py). Two reasons it is computed here rather than by summing
        get_losses() in Python: the row reader is capped at 200 rows so a Python sum would
        silently under-report a heavier partner, and read_captain_summary prefers the
        materialised `captain_summary` table, which turns two Turso round-trips per turn into
        one indexed lookup. It falls back to computing live, so the table is an optimisation
        and never a dependency.
        """
        return loss_db.read_captain_summary(captain_id)

    def get_cash(self, captain_id: str) -> dict:
        """EMPTY BY DESIGN — valmo.db holds no COD pendency or CMS deposit data. Returning
        {"cod_pendency_inr": 0} would be a lie shaped like a fact: zero pendency is a specific,
        checkable claim about a partner's cash position, and the engine would quote it."""
        return {}

    def get_shipments(self, captain_id: str) -> list[dict]:
        """Not this provider's data. Shipment/manifest state comes from Log10 (nexus in
        production); captain_context composes it separately and does not call this."""
        return []

    def get_scans(self, captain_id: str, awb: str) -> dict | None:
        return None    # scans are Log10's, same as get_shipments

    def known_captains(self) -> list[str]:
        """Real partner ids carrying enough history to be worth a conversation."""
        return loss_db.known_partners()
