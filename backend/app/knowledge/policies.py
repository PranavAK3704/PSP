"""Executable Policies (BRD §4.3 — the SOP Compiler's crown-jewel output).

An ExecutablePolicy is what the LLM compiles a plain-language SOP into ONCE, at
authoring time. At resolution time the engine EXECUTES it deterministically:
understand once (LLM) -> execute every time (code). The LLM never freehands a
money decision per case.

Checks are declarative predicates evaluated against the grounded Captain Context.
Each check names the evidence it reads, so every decision is auditable.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

_STORE = Path(__file__).resolve().parents[2] / "data" / "knowledge" / "policies.json"


@dataclass
class Check:
    id: str
    description: str          # human-readable ("loss attributable to partner?")
    reads: list[str]          # grounded-data fields this check consults (evidence)
    expect: str               # short statement of the passing condition


@dataclass
class ExecutablePolicy:
    id: str
    disposition: str          # disposition/theme this applies to
    version: str
    trigger: dict             # {keywords[], preconditions[]}
    required_evidence: list[str]   # source rows the SYSTEM fetches via queries
    checks: list[dict]        # list of Check dicts
    resolution: dict          # {action, params, cap_inr}
    escalation: dict          # {team, handover}
    partner_rights: list[str] # guardrails from the Partner Constitution
    source_sop_ref: str = ""
    compiled_by: str = "seed"
    # inputs the PARTNER must provide (drives the conversational slot-filling loop).
    required_inputs: list = field(default_factory=list)   # [{field,label,where}]
    identify_any: list = field(default_factory=list)       # any-one-of suffices to identify the case

    def to_dict(self) -> dict:
        return asdict(self)


# ── Seeded policies (compiled from the real SOP corpus) ──────────────────────
# The hardstop / wrong-debit policy is the hero. It encodes the losses_sla.csv
# TAT matrix + the HS_* scenario tree from valmo-l1-agent/input-bot.
_SEED: list[ExecutablePolicy] = [
    ExecutablePolicy(
        id="pol_hardstop_reversal",
        disposition="hardstop_loss",
        version="v1.3",
        trigger={
            "keywords": ["galat debit", "wrong debit", "loss marked", "hardstop",
                         "reversal", "reverse", "not connected", "debit"],
            "preconditions": ["captain contests a loss/debit marked against them"],
        },
        required_evidence=["ledger_debit", "scan_trail", "loss_event"],
        checks=[
            {"id": "evidence_present", "description": "All required source rows present",
             "reads": ["ledger_debit", "scan_trail", "loss_event"],
             "expect": "debit line, AWB scan trail and loss-marking event all pulled"},
            {"id": "attributable_to_partner",
             "description": "Is the loss attributable to the partner?",
             "reads": ["scan_trail", "loss_event"],
             "expect": "FAILS Valmo-side if an in-scan exists within TAT (partner connected on time)"},
            {"id": "within_reversal_cap",
             "description": "Debit amount within the auto-reversal money cap",
             "reads": ["ledger_debit"],
             "expect": "debit amount <= cap_inr"},
        ],
        resolution={"action": "reverse_debit", "params": {"idempotent": True}, "cap_inr": 5000},
        escalation={"team": "Losses & Debits (L2)",
                    "handover": "timeline, pulled rows, the check that could not be satisfied, "
                                "and the partner's strongest argument"},
        partner_rights=[
            "Presumption of good faith — no debit stands without evidence of partner fault",
            "True-cause attribution — cost attributed to Valmo/upstream, never defaulted onto partner",
            "Radical transparency — partner sees the same data and the reason",
            "Proportionality + downside caps — reversal bounded by cap_inr",
        ],
        source_sop_ref="input-bot/sop_knowledge#sop_hardstop_loss_HS_1_1 + losses_sla.csv",
        compiled_by="seed",
        identify_any=["awb", "amount_inr"],
        required_inputs=[
            {"field": "awb", "label": "the AWB or Bag ID of the disputed shipment",
             "where": "Captain Panel → Losses & Debits → tap the marked shipment"},
            {"field": "amount_inr", "label": "the debit amount (₹)",
             "where": "Captain Panel → Payments ledger"},
            {"field": "loss_date", "label": "the date the loss was marked",
             "where": "Captain Panel → Losses & Debits"},
        ],
    ),
    # NOTE: COD-pendency is deliberately NOT a money-moving Executable Policy. Per the
    # real Cash-Handover SOP (4.4.1), L1 does not auto-clear pendency — it verifies
    # (CMS/bank source, txn id, date, attachment → query check) and responds via template
    # or escalates. So COD is handled by the knowledge/answer path (search_sops), keeping
    # the engine's behaviour identical to the SOP. Only genuine autonomous money moves
    # (the hardstop wrong-debit reversal) are compiled as policies here.
]


# ── Living dispositions from the REAL loss taxonomy (gold.valmo_lost_awb_2k24 → reason_l1) ──
# Every reason_l1 the data emits becomes a disposition with a routing team. Only the
# categories the SNAPSHOT data can decide on its own are auto-reversible (they read the two
# real signals: facility_inscan = shipment scanned in at the facility → it connected;
# attribution_changed = the debit was already re-attributed). Everything else grounds the
# real record and escalates to the owning team with a fully-worked case — honest, since the
# SOP-specific evidence for those (e.g. shortage evidence-mail SLAs) isn't in this dataset yet.
#   action: reverse_debit = auto-reverse when a reversal signal is present & within cap
#           inform         = tell the captain the current state (no money move needed)
#           escalate       = always route to the team with the worked case
# caps are conservative safety limits pending the real refund SOPs (product owner to confirm).
_TAXONOMY = {
    # reason_l1 (data)              disposition key                team                             action          cap
    "hardstop":                    ("hardstop_loss",              "Losses & Debits (L2)",           "reverse_debit", 5000),
    "intransit":                   ("intransit_loss",             "Losses & Debits (L2)",           "reverse_debit", 5000),
    "dual_scan_mismatch":          ("dual_scan_mismatch",         "Losses & Debits (L2)",           "reverse_debit", 5000),
    "debit_revoked":               ("debit_revoked",              "Losses & Debits (L2)",           "inform",        None),
    "shipment_shortage":           ("shipment_shortage",          "Losses & Debits (L2)",           "escalate",      None),
    "bag_shortage":                ("bag_shortage",               "Losses & Debits (L2)",           "escalate",      None),
    "not_found":                   ("not_found",                  "Losses & Debits (L2)",           "escalate",      None),
    "wrong_rvp_pickup":            ("wrong_rvp_pickup",           "RVP / Returns (L2)",             "escalate",      None),
    "damage":                      ("damage",                     "Quality / QC (L2)",              "escalate",      None),
    "secondary_qc_fail":           ("secondary_qc_fail",          "Quality / QC (L2)",              "escalate",      None),
    "seller_dependency_sop_breached": ("seller_dependency",       "Seller Ops (L2)",                "escalate",      None),
    "pilot_shipment_lost_on_field":("pilot_lost_on_field",        "Losses & Debits (L2)",           "escalate",      None),
    "data platform issue":         ("data_platform_issue",        "Tech / Data Platform (L3)",      "escalate",      None),
    "sc_migration_issue":          ("sc_migration_issue",         "Tech / Data Platform (L3)",      "escalate",      None),
    "rto_vehicle_placement_issue": ("rto_vehicle_placement",      "RTO / Logistics (L2)",           "escalate",      None),
    "pc_product_missing":          ("pc_product_missing",         "Losses & Debits (L2)",           "escalate",      None),
    "others":                      ("other_loss",                 "Functional team (L2/L3)",        "escalate",      None),
}

_L1_LABEL = {
    "intransit_loss": "In-transit loss", "dual_scan_mismatch": "Dual-scan mismatch",
    "debit_revoked": "Debit already revoked", "shipment_shortage": "Shipment shortage",
    "bag_shortage": "Bag shortage", "not_found": "Shipment not found",
    "wrong_rvp_pickup": "Wrong RVP pickup", "damage": "Damage", "secondary_qc_fail": "Secondary QC fail",
    "seller_dependency": "Seller-dependency SOP breach", "pilot_lost_on_field": "Pilot lost on field",
    "data_platform_issue": "Data-platform issue", "sc_migration_issue": "SC migration issue",
    "rto_vehicle_placement": "RTO vehicle-placement issue", "other_loss": "Other loss",
    "pc_product_missing": "PC / product missing",
}


def _taxonomy_policy(disp: str, team: str, action: str, cap) -> ExecutablePolicy:
    label = _L1_LABEL.get(disp, disp)
    return ExecutablePolicy(
        id="pol_" + disp, disposition=disp, version="v1.0",
        trigger={"keywords": [disp.replace("_", " "), label.lower()],
                 "preconditions": [f"captain contests a {label.lower()} loss/debit"]},
        required_evidence=["loss_record"],
        checks=[
            {"id": "loss_record_present", "description": "The disputed AWB was found in the loss data",
             "reads": ["loss_record"], "expect": "a loss row for this AWB exists"},
            {"id": "reversal_signal", "description": "A data signal supports reversal",
             "reads": ["facility_inscan", "attribution_changed"],
             "expect": "facility in-scan present OR attribution already changed"},
        ],
        resolution={"action": action, "params": {"idempotent": True}, "cap_inr": cap},
        escalation={"team": team, "handover": "the real loss row + reversal signals + the captain's argument"},
        partner_rights=["Presumption of good faith", "True-cause attribution", "Radical transparency",
                        "Proportionality + downside caps", "Right to appeal + a human"],
        source_sop_ref="gold.valmo_lost_awb_2k24_v1 · reason_l1=" + disp, compiled_by="taxonomy",
        identify_any=["awb", "amount_inr"],
        required_inputs=[{"field": "awb", "label": "the AWB of the disputed shipment",
                          "where": "Captain Panel → Losses & Debits"}],
    )


# add every reason_l1 disposition (skip 'hardstop' — the hero policy above already owns it)
for _rl1, (_disp, _team, _action, _cap) in _TAXONOMY.items():
    if _disp == "hardstop_loss":
        continue
    _SEED.append(_taxonomy_policy(_disp, _team, _action, _cap))


def reason_l1_to_disposition(reason_l1: str) -> str:
    """Map a data reason_l1 value to our disposition key."""
    return _TAXONOMY.get((reason_l1 or "").strip(), ("other_loss",))[0]


def _load_all() -> dict[str, dict]:
    policies = {p.disposition: p.to_dict() for p in _SEED}
    if _STORE.exists():
        try:
            for p in json.loads(_STORE.read_text()):
                policies[p["disposition"]] = p
        except Exception:  # noqa: BLE001
            pass
    return policies


@lru_cache(maxsize=1)
def registry() -> dict[str, dict]:
    return _load_all()


def get_policy(disposition: str) -> dict | None:
    return registry().get(disposition)


def all_policies() -> list[dict]:
    return list(registry().values())
