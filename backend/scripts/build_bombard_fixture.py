"""Turn the adversarial message corpus into a loadable NDJSON fixture, and score the extractor.

    python scripts/build_bombard_fixture.py            # build + score
    python scripts/build_bombard_fixture.py --paste    # print a paste-ready list for Slack

── WHY THIS EXISTS ───────────────────────────────────────────────────────────────────────────
Typing 155 messages into Slack by hand to test an extractor is not a test, it is an afternoon.
This turns the corpus into the same day-partitioned NDJSON the exporter and the live reader
both produce, so the whole pipeline runs over it in seconds and every message carries its
EXPECTED outcome alongside what the pipeline actually did.

The corpus was generated per-dimension and then adversarially reviewed. That review changed
two expectations, and both changes are the interesting kind:

  · A blank template containing "DC Code: XXX" was expected to yield nothing. Wrong — XXX is
    label-adjacent and three uppercase chars, so Tier A extracts it by design. The message was
    rewritten with underscore placeholders rather than the expectation being fudged.
  · "Invoice Series: INV/26-27/00451" was expected to yield nothing. Also wrong — INV is a
    genuine hub code in the registry and is not on the denylist, so Tier B fires on it. That
    is a REAL false positive worth knowing about, so the expectation records what the rules
    actually do today rather than what we would like them to do.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORPUS = ROOT / "data" / "intake" / "corpus" / "messages.json"
OUT = ROOT / "data" / "intake" / "corpus" / "raw"
IST = timezone(timedelta(hours=5, minutes=30))
CH = ("C0BOMBARD01", "intake-bombard")
WS = "T0S2UJU8H"
AUTHORS = [("U0BOMB00001", "Example Hub Captain", "hub.captain@meesho.com"),
           ("U0BOMB00002", "Example Ops Lead", "ops.lead@meesho.com"),
           ("U0BOMB00003", "Example FE Coordinator", "fe.coord@meesho.com")]


def rec(text: str, ts: float, author: tuple) -> dict:
    mid = f"{ts:.6f}"
    dt = datetime.fromtimestamp(ts + 19800, timezone.utc)
    mentions = []
    for tok in text.split("<@"):
        if tok and tok[0] in "UW":
            u = tok.split(">")[0].split("|")[0]
            if u not in mentions:
                mentions.append(u)
    return {
        "schema_version": "1.1", "source": "synthetic:adversarial-corpus", "workspace_id": WS,
        "channel_id": CH[0], "channel_name": CH[1], "message_id": mid, "ts_epoch": float(mid),
        "ts_iso": dt.strftime("%Y-%m-%dT%H:%M:%S") + "+05:30",
        "author_id": author[0], "author_name": author[1], "author_email": author[2],
        "author_is_bot": False, "text": text, "subtype": None, "thread_ref": None,
        "is_thread_parent": False, "reply_count": 0, "mentions": mentions,
        "channel_mention": any(m in text for m in ("<!channel>", "<!here>", "<!everyone>")),
        "subteam_mentions": [], "attachments": [], "has_media": False, "reactions": [],
        "permalink": f"https://meesho.slack.com/archives/{CH[0]}/p{mid.replace('.', '')}",
        "edited_ts": None, "fetched_at": "2026-09-09T18:00:00+05:30",
    }


def build() -> tuple[list[dict], list[dict]]:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))["messages"]
    base = datetime(2026, 9, 8, 9, 0, 0, tzinfo=IST).timestamp()
    recs, expect = [], []
    for i, m in enumerate(corpus):
        ts = base + i * 47 + (i % 97) / 1000.0 + 0.000001
        r = rec(m["text"], ts, AUTHORS[i % len(AUTHORS)])
        recs.append(r)
        expect.append({"message_id": r["message_id"], **m})
    return recs, expect


def score(expect: list[dict]) -> int:
    """Run the pipeline over the fixture and compare, per message, against the expectation."""
    import os
    import tempfile
    os.environ.setdefault("INTAKE_DB", str(Path(tempfile.mkdtemp()) / "bombard.db"))
    from app.intake import extract as istage      # noqa: E402
    from app.intake import group, loadstage, noise, qualify, register, store, emit  # noqa: E402

    # The synthetic channel is not in channels.yaml, so qualify would exclude it. Score the
    # extractor and the gate directly instead of forcing a config edit for a test corpus.
    loadstage.load(OUT, run_id="bomb", skip_validate=True)
    noise.run("bomb")
    istage.run("bomb")

    con = store.connect()
    by = {e["message_id"]: e for e in expect}
    dc_tp = dc_fp = dc_fn = 0
    gate_ok = gate_bad = 0
    info_ok = info_bad = 0
    fps, fns, gate_misses, info_misses = [], [], [], []

    for mid, e in by.items():
        got = {r[0] for r in con.execute(
            "SELECT value FROM entities WHERE run_id='bomb' AND message_id=? AND kind='dc_code'",
            (mid,))}
        want = set(e["expect_dc_codes"])
        dc_tp += len(got & want)
        for v in sorted(got - want):
            dc_fp += 1
            fps.append((v, e["dimension"], e["text"][:64]))
        for v in sorted(want - got):
            dc_fn += 1
            fns.append((v, e["dimension"], e["text"][:64]))

        f = con.execute("SELECT gated, informational FROM message_flags "
                        "WHERE run_id='bomb' AND message_id=?", (mid,)).fetchone()
        gated, info = (bool(f["gated"]), bool(f["informational"])) if f else (False, False)
        if e["expect"] == "gate":
            gate_ok += gated
            gate_bad += not gated
            if not gated:
                gate_misses.append((e["dimension"], e["text"][:64]))
        elif gated:
            gate_bad += 1
            gate_misses.append((e["dimension"], "OVER-GATED: " + e["text"][:56]))
        if e["expect"] == "informational":
            info_ok += info
            info_bad += not info
            if not info:
                info_misses.append((e["dimension"], e["text"][:64]))

    def pct(a, b):
        return f"{a / b:.1%}" if b else "n/a"

    print(f"\n{'=' * 84}\nEXTRACTOR vs {len(by)} ADVERSARIAL MESSAGES\n{'=' * 84}")
    print(f"\n  DC codes    precision {pct(dc_tp, dc_tp + dc_fp)}   "
          f"recall {pct(dc_tp, dc_tp + dc_fn)}   (tp={dc_tp} fp={dc_fp} fn={dc_fn})")
    print(f"  noise gate  {gate_ok} correct, {gate_bad} wrong")
    print(f"  weather     {info_ok} correct, {info_bad} missed")

    if fps:
        print(f"\n  FALSE POSITIVES ({len(fps)}) — extracted but should not have been:")
        for v, d, t in fps[:14]:
            print(f"    {v:<5} [{d}] {t!r}")
    if fns:
        print(f"\n  FALSE NEGATIVES ({len(fns)}) — expected but missed:")
        for v, d, t in fns[:14]:
            print(f"    {v:<5} [{d}] {t!r}")
    if gate_misses:
        print(f"\n  GATE DISAGREEMENTS ({len(gate_misses)}):")
        for d, t in gate_misses[:10]:
            print(f"    [{d}] {t!r}")
    if info_misses:
        print(f"\n  WEATHER MISSED ({len(info_misses)}):")
        for d, t in info_misses[:8]:
            print(f"    [{d}] {t!r}")
    con.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paste", action="store_true", help="print a numbered list for Slack")
    ap.add_argument("--only", help="one dimension only")
    a = ap.parse_args()

    recs, expect = build()
    if a.paste:
        corpus = json.loads(CORPUS.read_text(encoding="utf-8"))["messages"]
        n = 0
        for m in corpus:
            if a.only and m["dimension"] != a.only:
                continue
            n += 1
            print(f"\n--- {n}. [{m['dimension']}] expect={m['expect']} "
                  f"dc={m['expect_dc_codes'] or '-'}\n{m['text']}")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.ndjson"):
        f.unlink()
    buckets: dict[str, list] = {}
    for r in recs:
        buckets.setdefault(r["ts_iso"][:10], []).append(r)
    for day, rows in buckets.items():
        (OUT / f"{day}.ndjson").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    (OUT.parent / "expected.json").write_text(json.dumps(expect, indent=1, ensure_ascii=False),
                                              encoding="utf-8")
    print(f"wrote {len(recs)} records to {OUT.relative_to(ROOT)} "
          f"({', '.join(f'{d}:{len(v)}' for d, v in sorted(buckets.items()))})")
    return score(expect)


if __name__ == "__main__":
    raise SystemExit(main())
