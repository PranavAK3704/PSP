"""Manual-audit round trip — let a HUMAN (or an assistant in a chat session) act as the judge
when no LLM endpoint is available, without ever faking an engine result.

    export:  python scripts/manual_audit.py export --limit 25 > batch.json
    import:  python scripts/manual_audit.py import verdicts.json

WHY: the audit engine needs an LLM to score. When the endpoint is down (or before a key is
procured), the alternative is not "guess" — it is to have a real judge read the transcript and
supply the verdicts, then score them with the SAME deterministic maths the engine uses.

WHAT IS AND ISN'T AUTOMATED HERE
  • Coverage (which SOP applies) is computed DETERMINISTICALLY by locate_sop — retrieval, no LLM.
    So the manual judge never has to guess it, and it matches the engine exactly.
  • Scoring is _score_audit — the same Pass/Fail + auto-fail-gate maths as the engine.
  • Only the JUDGEMENT (per-parameter verdict + rationale) comes from the human.

PROVENANCE IS MANDATORY. Every row written here is stamped `judge` (e.g. "claude-opus-manual")
and `judged_by: "manual"`, and lands under its own run_id. A manually-judged row must never be
mistakable for an engine row — that distinction is the whole point.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audit import kapture as k          # noqa: E402
from app.audit import kapture_rubric        # noqa: E402

CALIB = Path("/private/tmp/claude-502/-Users-pranav-akella-PSP/"
             "844cc15a-4a8b-45a8-8be0-2a7d707902d9/scratchpad/bau_calib.jsonl")


def _pending(limit: int) -> list[dict]:
    audited = set((k._load().get("tickets") or {}).keys())
    if not CALIB.exists():
        raise SystemExit(f"source batch not found: {CALIB}")
    rows = [json.loads(l) for l in CALIB.read_text().splitlines() if l.strip()]
    return [r for r in rows if r["ticket_id"] not in audited][:limit]


def do_export(limit: int) -> None:
    """Emit a compact batch: transcript + the applicable SOP + the exact rubric to judge against."""
    rubric = kapture_rubric.get_rubric()
    sop_index = k.build_sop_index()
    out = {
        "rubric_version": rubric.get("version"),
        "instructions": ("For each ticket return: per_dimension {key: {verdict: pass|fail|na, "
                         "rationale}}, overall_rationale, key_findings[]. Gates (zt_/fatal_) are "
                         "pass|fail only and must be RARE — fail only on transcript-evidenced "
                         "breach. Quality params may be 'na' when they genuinely do not apply."),
        "rubric": [{"key": d["key"], "label": d.get("label"), "points": d.get("weight"),
                    "tier": kapture_rubric.tier_of(d["key"]), "description": d.get("description")}
                   for d in rubric.get("dimensions", [])],
        "tickets": [],
    }
    for r in _pending(limit):
        cov = k.locate_sop(r["transcript"], sop_index)      # deterministic — no LLM
        sop = cov.get("sop") or {}
        out["tickets"].append({
            "ticket_number": r["ticket_id"],
            "transcript": r["transcript"],
            "coverage": {"covered": cov["covered"], "disposition": cov["disposition"],
                         "matched_sop_id": cov["matched_sop_id"],
                         "sop_title": sop.get("title"),
                         "sop_checks": [c.get("description") for c in (sop.get("checks") or [])]},
        })
    json.dump(out, sys.stdout, indent=1, ensure_ascii=False)


def do_import(path: str, judge: str, run_id: str) -> None:
    """Ingest judged verdicts → score with the engine's own maths → persist with provenance."""
    payload = json.loads(Path(path).read_text())
    verdicts = payload["verdicts"] if isinstance(payload, dict) and "verdicts" in payload else payload
    rubric = kapture_rubric.get_rubric()
    sop_index = k.build_sop_index()
    src = {r["ticket_id"]: r for r in
           (json.loads(l) for l in CALIB.read_text().splitlines() if l.strip())}

    written, skipped = 0, []
    store = k._load()
    for v in verdicts:
        tn = str(v.get("ticket_number", "")).strip()
        if not tn or tn not in src:
            skipped.append(tn or "<blank>")
            continue
        transcript = src[tn]["transcript"]
        cov = k.locate_sop(transcript, sop_index)
        # Coerce through the ENGINE's own coercion so a missing/invalid verdict is handled
        # identically (quality -> na, gate -> pass), then score with the engine's maths.
        pd = k._coerce_dims({"per_dimension": v.get("per_dimension") or {}}, rubric)
        sc = k._score_audit(pd, rubric)
        row = {
            "ticket_number": tn,
            "rubric_version": rubric.get("version"),
            "run_id": run_id,
            "judge": judge,                 # PROVENANCE — who actually made the call
            "judged_by": "manual",          # never mistakable for an engine row
            "per_dimension": pd,
            "composite": sc["composite"], "status": sc["status"],
            "quality_pct": sc["quality_pct"], "fired": sc["fired"],
            "covered": cov["covered"], "disposition": cov["disposition"],
            "coverage_score": cov["coverage_score"], "matched_sop_id": cov["matched_sop_id"],
            "sop_title": (cov.get("sop") or {}).get("title"),
            "coverage_candidates": cov.get("supporting") or [],
            "adherence": None, "per_check": [], "resolution_action_followed": "unknown",
            "key_findings": [k._redact(str(x)[:200]) for x in (v.get("key_findings") or [])][:6],
            "overall_rationale": k._redact(str(v.get("overall_rationale", ""))[:600]),
            "audited_at": k._now(),
            "transcript_excerpt": k._redact(transcript.strip()[:4000]),
        }
        store["tickets"][tn] = row
        written += 1
    k._write(store)
    print(f"imported {written} manual audits as judge={judge!r} run_id={run_id!r}")
    if skipped:
        print(f"  skipped {len(skipped)} unknown ticket numbers: {skipped[:5]}")
    s = k.scores()
    print(f"  dashboard now: {s['count']} audits | pass {s['pass_rate']}% | "
          f"avg quality {s['avg_quality_pct']} | not_audited {s.get('not_audited')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export"); e.add_argument("--limit", type=int, default=25)
    i = sub.add_parser("import")
    i.add_argument("path")
    i.add_argument("--judge", default="claude-opus-manual")
    i.add_argument("--run-id", default="manual_audit")
    a = ap.parse_args()
    if a.cmd == "export":
        do_export(a.limit)
    else:
        do_import(a.path, a.judge, a.run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
