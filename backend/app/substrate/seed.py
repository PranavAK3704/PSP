"""Seed data for the mock data substrate.

Stand-in Log10 / payments / losses / cash data following the real schemas so the
engine exercises real logic. Replaced by live Metabase/Log10 connectors in prod.

Captains:
  • VLMO-CPT-4471 (Rajesh)  — DEFAULT, CLEAN account. No disputable debits, so
    general / ID-block / "why losses" / tampered flows never surface a debit.
    Carries a shipment approaching a D5 breach for the monitoring demo.
  • VLMO-CPT-2290 (Anita)   — a COD pendency (payments path).
  • VLMO-CPT-3310 (Vikram)  — the WRONG-DEBIT REVERSAL demo captain: a debit whose
    scan trail proves an in-scan within TAT -> reversal (BRD §4.7 hero).
"""

SEED = {
    # ── DEFAULT: clean account ──────────────────────────────────────────────
    "VLMO-CPT-4471": {
        "profile": {
            "captain_id": "VLMO-CPT-4471", "name": "Rajesh Kumar",
            "hub": "DEL-DC-014", "hub_name": "Delhi Narela DC",
            "since": "2023-08-11", "tier": "Gold", "language": "hi-IN",
            "cash_position_inr": 3200, "open_concerns": 0,
        },
        "ledger": [
            {"id": "CR-77120", "type": "credit", "amount_inr": 8600, "date": "2026-06-25",
             "reason": "weekly_payout", "status": "posted", "narration": "Weekly delivery payout"},
        ],
        "scans": {},
        "losses": [],
        # one shipment approaching a D5 breach → the monitoring demo
        "shipments": [
            {"awb": "VL0092240881", "leg": "LM", "direction": "Forward", "status": "At Facility",
             "inscan_date": "2026-06-27", "hub": "DEL-DC-014", "days_since_inscan": 4,
             "manifest_scan": None, "on_correct_manifest_path": False,
             "note": "Not manifested; D5 hardstop breach imminent"},
            {"awb": "VL0092240882", "leg": "LM", "direction": "Forward", "status": "Out For Delivery",
             "inscan_date": "2026-06-30", "hub": "DEL-DC-014", "days_since_inscan": 1,
             "manifest_scan": "2026-06-30T10:00:00", "on_correct_manifest_path": True},
        ],
        "cash": {"cms_assigned": True, "cms_name": "CMS-DEL-07", "cod_pendency_inr": 0, "deposits": []},
    },

    # ── COD pendency ────────────────────────────────────────────────────────
    "VLMO-CPT-2290": {
        "profile": {
            "captain_id": "VLMO-CPT-2290", "name": "Anita Sharma",
            "hub": "BLR-DC-006", "hub_name": "Bengaluru Peenya DC",
            "since": "2024-02-02", "tier": "Silver", "language": "en-IN",
            "cash_position_inr": 0, "open_concerns": 1,
        },
        "ledger": [
            {"id": "CR-66001", "type": "credit", "amount_inr": 7400, "date": "2026-06-25",
             "reason": "weekly_payout", "status": "posted", "narration": "Weekly delivery payout"},
        ],
        "scans": {},
        "losses": [],
        "shipments": [],
        "cash": {"cms_assigned": True, "cms_name": "CMS-BLR-03", "cod_pendency_inr": 6500,
                 "deposits": [{"txn_id": "CMS-TXN-55821", "amount_inr": 6500, "date": "2026-06-28",
                               "cms_name": "CMS-BLR-03", "reconciled": False}]},
    },

    # ── WRONG-DEBIT REVERSAL demo (the §4.7 hero) ─────────────────────────────
    "VLMO-CPT-3310": {
        "profile": {
            "captain_id": "VLMO-CPT-3310", "name": "Vikram Singh",
            "hub": "PUN-DC-021", "hub_name": "Pune Chakan DC",
            "since": "2023-05-19", "tier": "Gold", "language": "hi-IN",
            "cash_position_inr": 1500, "open_concerns": 1,
        },
        "ledger": [
            {"id": "DBT-55901", "type": "debit", "amount_inr": 1860, "date": "2026-06-30",
             "reason": "hardstop_loss", "awb": "VL0093310077", "status": "posted",
             "narration": "Loss debit — shipment not connected (hardstop D5)"},
            {"id": "CR-55880", "type": "credit", "amount_inr": 9100, "date": "2026-06-25",
             "reason": "weekly_payout", "status": "posted", "narration": "Weekly delivery payout"},
        ],
        "scans": {
            "VL0093310077": {
                "awb": "VL0093310077", "leg": "LM", "direction": "Forward",
                "inscan_date": "2026-06-25", "hub": "PUN-DC-021",
                "events": [
                    {"scan": "INWARD_SCAN", "at": "2026-06-25T08:40:00", "node": "PUN-DC-021", "ok": True},
                    {"scan": "MANIFEST_SCAN", "at": "2026-06-27T17:20:00", "node": "PUN-DC-021", "ok": True},
                    {"scan": "OUT_FOR_DELIVERY", "at": "2026-06-28T07:30:00", "node": "PUN-DC-021", "ok": True},
                ],
                "connected_within_tat": True,
                "last_status": "In_Transit",
                "hardstop_sop_followed": True,
            },
        },
        "losses": [
            {"awb": "VL0093310077", "loss_type": "hardstop", "attributed_node": "PUN-DC-021",
             "loss_date": "2026-06-30", "reason_l1": "not_connected_within_sla",
             "amount_recovered_from_fe": False, "marked_by": "auto_sla_job"},
        ],
        "shipments": [],
        "cash": {"cms_assigned": True, "cms_name": "CMS-PUN-02", "cod_pendency_inr": 0, "deposits": []},
    },
}

# SLA matrix (mirrors losses_sla.csv): TAT windows by leg/direction.
SLA = {
    ("FM", "Forward"): {"within": 3, "hardstop": 5, "lost": 6},
    ("FM", "RTO"): {"within": 3, "hardstop": 5, "lost": 6},
    ("LM", "Forward"): {"within": 5, "hardstop": 7, "lost": 8},
    ("LM", "RTO"): {"within": 3, "hardstop": 5, "lost": 6},
}
