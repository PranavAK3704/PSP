"""Promote a compiled SOP into an EXECUTABLE policy.

THE GAP THIS CLOSES
The SOP Compiler and the executable-policy registry are two different stores, and nothing
joined them:

  · `sop_compiler.compile_sop()` → `_persist_sop()` → `kt_queue.json`. That is the RETRIEVAL
    corpus: the model can find the SOP and read it as prose. `policy_exec` never sees it.
  · `policy_exec.execute()` → `policies.get_policy(disposition)` → `registry()`, which is
    `_SEED` overlaid with `data/knowledge/policies.json`.

`policies.json` did not exist, so the overlay looked like a dead seam. It is not dead — it
works — but `registry()` is `lru_cache`d and never invalidated, so a write only took effect
across a process restart. That combination is why 8 approved `load_planning` SOPs could sit in
the corpus while `policies.get_policy("load_planning")` returned `None`, and `apply_policy`
consequently returned confidence 0.0 and defaulted the escalation to "Losses & Debits (L2)" —
the wrong team for a load question.

WHAT THIS SCRIPT ADDS TO A COMPILED POLICY
Two fields the compiler's own schema does not emit but `ExecutablePolicy` declares
(`policies.py:35-36`), because they drive the conversational loop rather than the SOP text:
  `required_inputs`  [{field, label, where}] — what the captain must supply
  `identify_any`     [str] — any one of these identifies the case

Usage
  python scripts/promote_policy.py --file compiled.json
  python scripts/promote_policy.py --file compiled.json --identify-any hub --dry-run
  python scripts/promote_policy.py --list
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The keys `ExecutablePolicy` needs to be constructible. The compiler emits all of these; a
# hand-authored policy must too. Checked here rather than trusted, because a policy missing
# `escalation` silently routes a captain to the wrong team.
REQUIRED = ("id", "disposition", "version", "trigger", "required_evidence", "checks",
            "resolution", "escalation")


def _load(path: Path) -> dict:
    data = json.loads(path.read_text())
    # Accept either a bare policy or the compile endpoint's `{policy, gaps, ...}` envelope, so
    # a response saved straight off /api/sop/compile can be promoted without hand-editing.
    if isinstance(data, dict) and "policy" in data and isinstance(data["policy"], dict):
        return data["policy"]
    return data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="compiled policy JSON (bare, or a /api/sop/compile response)")
    ap.add_argument("--identify-any", default="",
                    help="comma-separated fields, any one of which identifies the case")
    ap.add_argument("--required-input", action="append", default=[],
                    metavar="field:label:where", help="repeatable, e.g. hub:Hub / DC code:profile")
    ap.add_argument("--evidence", default="",
                    help=("comma-separated MACHINE-checkable required_evidence names, replacing "
                          "the compiler's. The original is kept as required_evidence_authored."))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true", help="show the current registry and exit")
    a = ap.parse_args()

    from app.knowledge import policies as pol

    if a.list or not a.file:
        reg = pol.registry()
        store = pol.store_path()
        print(f"executable policies: {len(reg)}   (overlay file: {store}"
              f"{'' if Path(store).exists() else ' — ABSENT'})")
        for disp in sorted(reg):
            p = reg[disp]
            action = (p.get("resolution") or {}).get("action", "?")
            team = (p.get("escalation") or {}).get("team", "?")
            print(f"  {disp:24} {p.get('id', '?'):26} {action:16} → {team}"
                  f"{'   [' + p.get('compiled_by', '') + ']' if p.get('compiled_by') else ''}")
        return 0 if a.list else 1

    src = Path(a.file)
    if not src.exists():
        print(f"no such file: {src}")
        return 1
    policy = _load(src)

    missing = [k for k in REQUIRED if not policy.get(k)]
    if missing:
        print(f"cannot promote — the policy is missing: {', '.join(missing)}")
        return 1
    if not (policy.get("escalation") or {}).get("team"):
        # governance.check_conformance treats this as a HIGH mandate, and for good reason: a
        # policy with no team is a policy that routes a captain nowhere.
        print("cannot promote — escalation.team is empty; the concern would route nowhere")
        return 1

    # ── translate required_evidence to names an executor can actually satisfy ──────────
    # The compiler writes DOCUMENTATION-grade evidence names — it will happily list
    # "GET /v1/.../your-metrics" and "levers.rto_performance (current value + target)" as
    # required evidence, because that is what the SOP prose says. But `gate.evaluate()` does a
    # literal set difference against `decision["evidence_present"]`, so a documentation name is
    # a name no executor can ever satisfy — and the only ways out are a permanently-blocked
    # gate or the `present = required_evidence` shortcut that makes the check unfailable.
    #
    # So the translation happens HERE, once, visibly, and the authored list is preserved rather
    # than discarded. This is the seam between "what a human should verify" and "what code can".
    if a.evidence:
        machine = [e.strip() for e in a.evidence.split(",") if e.strip()]
        authored = policy.get("required_evidence") or []
        policy["required_evidence_authored"] = authored
        policy["required_evidence"] = machine
        print(f"  evidence   translated {len(authored)} authored -> {len(machine)} machine-checkable")
        for e in machine:
            print(f"               + {e}")
        print("             (authored list preserved as required_evidence_authored)")

    # The two loop-driving fields the compiler does not emit.
    if a.identify_any:
        policy["identify_any"] = [f.strip() for f in a.identify_any.split(",") if f.strip()]
    policy.setdefault("identify_any", [])
    req_in = []
    for spec in a.required_input:
        bits = (spec.split(":") + ["", ""])[:3]
        req_in.append({"field": bits[0], "label": bits[1] or bits[0], "where": bits[2]})
    if req_in:
        policy["required_inputs"] = req_in
    policy.setdefault("required_inputs", [])
    policy.setdefault("partner_rights", [])
    policy.setdefault("source_sop_ref", str(src.name))
    policy.setdefault("compiled_by", "sop_compiler")

    disp = policy["disposition"]
    existing = pol.get_policy(disp)
    print(f"promoting {policy['id']} → disposition '{disp}'"
          + (f"  (REPLACES {existing['id']})" if existing else "  (new)"))
    print(f"  action     {(policy.get('resolution') or {}).get('action')}"
          f"   cap ₹{(policy.get('resolution') or {}).get('cap_inr')}")
    print(f"  team       {(policy.get('escalation') or {}).get('team')}")
    print(f"  checks     {len(policy.get('checks') or [])}")
    print(f"  evidence   {policy.get('required_evidence')}")
    print(f"  identify   {policy.get('identify_any')}")
    if a.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    store = Path(pol.store_path())
    store.parent.mkdir(parents=True, exist_ok=True)
    current = []
    if store.exists():
        try:
            current = json.loads(store.read_text())
            if not isinstance(current, list):
                current = []
        except Exception:  # noqa: BLE001
            current = []
    # Replace by disposition — the overlay is keyed on it in `_load_all`, so two entries for one
    # disposition would make which policy wins depend on file order.
    current = [p for p in current if p.get("disposition") != disp] + [policy]
    store.write_text(json.dumps(current, indent=1, ensure_ascii=False) + "\n")

    n = pol.invalidate()
    got = pol.get_policy(disp)
    ok = bool(got) and got.get("id") == policy["id"]
    print(f"\nwrote {store}  ({len(current)} overlay polic{'y' if len(current) == 1 else 'ies'})")
    print(f"registry now {n} policies; get_policy({disp!r}) → {got.get('id') if got else None}")
    if not ok:
        print("PROMOTION DID NOT TAKE EFFECT — the registry does not serve it")
        return 1
    print("verified: the executable registry serves the promoted policy in-process.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
