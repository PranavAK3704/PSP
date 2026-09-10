"""Extract a labelled (partner text -> disposition) corpus from backend/data/kapture_audits.json.

    python scripts/build_corpus_from_audits.py            # build + report
    python scripts/build_corpus_from_audits.py --holdout  # also write a 20% held-out split

── WHY THIS FILE IS THE FOUNDATION ───────────────────────────────────────────────────────────
It is the only corpus in this repo pairing a partner's OWN WORDS with a disposition label:
1,814 real support tickets, each carrying `transcript_excerpt` in the form

    PARTNER MESSAGE (VOC): <what the partner actually wrote>
    AGENT RESOLUTION:      <what the agent did>

Neither the intake brief nor the first pass through this repo mentioned it. Everything the
classifier tier does — exemplar matching, threshold calibration, held-out scoring, and the
NOVEL rate that measures taxonomy coverage — is built on this file.

── THE LABELS ARE SILVER, NOT GOLD, AND THAT CHANGES WHAT THE NUMBER MEANS ────────────────────
`disposition` is carried from a concern the engine itself classified, and `app/audit/runner.py`
calls a model on the deep tier. So the headline figure

    14 dispositions cover 1,566/1,814 = 86.3%,  NOVEL 248/1,814 = 13.7%

measures "the existing classifier assigned a bucket 86.3% of the time" — NOT "14 dispositions
truly cover 86.3% of partner reality". Those are different claims and only the first is
supported. A human pass over a stratified sample (~800 rows) is what turns it into the second.

Every row therefore carries `label_provenance: "silver"`. When a human confirms one it becomes
"gold", and `evaluate` reports the two separately so a silver-only score is never quoted as
though a person had checked it.

── PII ───────────────────────────────────────────────────────────────────────────────────────
The source file is already redacted upstream — names and numbers appear as <name>, <num> and
similar placeholders. `--check-pii` re-scans for anything that leaked through, because "already
redacted" is a claim worth verifying before a corpus is committed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "data" / "kapture_audits.json"
OUT_DIR = ROOT / "data" / "intake" / "corpus"
OUT = OUT_DIR / "dispositions.json"
HOLDOUT = OUT_DIR / "dispositions_holdout.json"

#: The excerpt is one string with two labelled halves. Only the first is the partner's voice;
#: the agent's resolution is what a classifier must NOT see, or it learns to read the answer.
_VOC = re.compile(r"PARTNER MESSAGE \(VOC\):(.*?)(?:AGENT RESOLUTION:|$)", re.S)

#: Residual-PII detectors. The source is redacted upstream; this verifies rather than assumes.
_LEAKS = {
    "mobile": re.compile(r"(?<![A-Za-z0-9])[6-9][0-9]{9}(?![A-Za-z0-9])"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
}
#: Placeholders the upstream scrubber leaves behind — presence of these is GOOD.
_PLACEHOLDER = re.compile(r"<(name|num|phone|email|address)>", re.I)


def partner_text(excerpt: str) -> str | None:
    """The partner's half of the transcript, or None when the marker is absent."""
    m = _VOC.search(excerpt or "")
    if not m:
        return None
    t = re.sub(r"\s+", " ", m.group(1)).strip()
    return t or None


def build() -> list[dict]:
    tickets = json.loads(SRC.read_text(encoding="utf-8"))["tickets"]
    rows = []
    for tid, r in tickets.items():
        text = partner_text(str(r.get("transcript_excerpt", "")))
        if not text:
            continue
        disp = r.get("disposition") or "NOVEL"
        rows.append({
            # A stable id derived from the text, so the same message is the same row across
            # rebuilds even if the source file is re-exported in a different order.
            "id": hashlib.sha256(text.encode()).hexdigest()[:16],
            "ticket_number": r.get("ticket_number") or tid,
            "text": text,
            "disposition": disp,
            "is_novel": disp == "NOVEL",
            "covered": bool(r.get("covered")),
            "coverage_score": r.get("coverage_score"),
            "matched_sop_id": r.get("matched_sop_id"),
            "label_provenance": "silver",   # -> "gold" once a human confirms it
            "confirmed_by": None,
            "confirmed_at": None,
        })
    rows.sort(key=lambda r: (r["disposition"], r["id"]))
    return rows


def pii_report(rows: list[dict]) -> dict:
    leaks: dict[str, list[str]] = {k: [] for k in _LEAKS}
    placeheld = 0
    for r in rows:
        if _PLACEHOLDER.search(r["text"]):
            placeheld += 1
        for kind, rx in _LEAKS.items():
            for hit in rx.findall(r["text"]):
                leaks[kind].append(f'{r["id"]}:{hit}')
    return {"rows_with_placeholders": placeheld,
            "leaks": {k: v for k, v in leaks.items() if v}}


def split(rows: list[dict], frac: float = 0.2) -> tuple[list[dict], list[dict]]:
    """A deterministic, STRATIFIED split — same rows every run, no random seed to forget.

    Stratified because the distribution is severe: payment_not_received is 26% and
    consumables_damaged is one row. An unstratified 20% sample can miss a class entirely and
    then 'accuracy' is measured on a taxonomy that is not the taxonomy.
    """
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["disposition"], []).append(r)
    train, held = [], []
    for disp, group in sorted(by.items()):
        group = sorted(group, key=lambda r: r["id"])          # id is a content hash: stable
        n_held = int(len(group) * frac)
        held.extend(group[:n_held])
        train.extend(group[n_held:])
    return train, held


def report(rows: list[dict]) -> None:
    disp = Counter(r["disposition"] for r in rows)
    novel = disp.get("NOVEL", 0)
    n = len(rows)
    lens = sorted(len(r["text"]) for r in rows)
    print(f"\n  {n} partner messages with a disposition label")
    print(f"  {len(disp)} distinct dispositions")
    print(f"  NOVEL {novel}/{n} = {novel / n:.1%}   <-- taxonomy coverage gap (silver labels)")
    print(f"  message length: median {lens[n // 2]} chars, p90 {lens[int(n * 0.9)]}\n")
    for k, v in disp.most_common():
        bar = "█" * max(1, round(v / max(disp.values()) * 34))
        print(f"    {k:<24} {v:>5}  {v / n:>5.1%}  {bar}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--holdout", action="store_true", help="also write a stratified 20% split")
    ap.add_argument("--check-pii", action="store_true", help="scan for residual PII and exit")
    a = ap.parse_args()

    if not SRC.exists():
        raise SystemExit(f"{SRC} not found")
    rows = build()

    if a.check_pii:
        rep = pii_report(rows)
        print(f"  rows carrying a <placeholder>: {rep['rows_with_placeholders']}/{len(rows)}")
        if rep["leaks"]:
            print("  RESIDUAL PII FOUND — do not commit until resolved:")
            for kind, hits in rep["leaks"].items():
                print(f"    {kind}: {len(hits)} e.g. {hits[:3]}")
            return 1
        print("  no residual mobiles or emails found")
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        "_what": "Partner-voice messages paired with a disposition, extracted from "
                 "data/kapture_audits.json. The only (partner text -> label) corpus in the repo.",
        "_labels": "SILVER — produced by the engine's own classifier (audit/runner.py calls a "
                   "model). The NOVEL rate measures how often that classifier assigned a "
                   "bucket, NOT whether the taxonomy covers reality. A human pass promotes a "
                   "row to gold.",
        "_source": str(SRC.relative_to(ROOT)),
        "_count": len(rows),
        "messages": rows,
    }
    OUT.write_text(json.dumps(meta, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    report(rows)

    if a.holdout:
        train, held = split(rows)
        HOLDOUT.write_text(json.dumps(
            {"_what": "Stratified 20% held-out split. Deterministic — the id is a content hash "
                      "and the split is by sorted position, so there is no seed to forget and "
                      "the same rows are held out on every rebuild.",
             "_train": len(train), "_holdout": len(held), "messages": held},
            indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nwrote {HOLDOUT.relative_to(ROOT)}  train={len(train)} holdout={len(held)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
