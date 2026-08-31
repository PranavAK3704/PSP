"""Score SOP retrieval against a golden set. NO LLM CALLS, NO NETWORK.

    python scripts/check_retrieval.py            # score the current scorer
    python scripts/check_retrieval.py --verbose  # show every miss with what came back instead

WHY THIS EXISTS. `knowledge/store.retrieve()` is the layer that decides which SOP the engine
cites, and until now nothing measured it. It was hand-tuned by looking at a few queries, which is
exactly how a scorer ends up good at the queries someone happened to try and bad at the rest.

WHAT IT MEASURES
  recall@1   the right chunk is the FIRST result — this is what matters, because the composer
             reads the top hit
  recall@3   the right chunk is in the top three — the reply may cite any of them
  no-hit     the query returned nothing at all (the relevance cutoff ate everything)
  wrong@1    something came back and the top hit was wrong — the worst outcome, because a
             confidently wrong citation is harder to spot than an empty one

THE GOLDEN SET is real captain phrasing, not invented queries: Hinglish taken from the
`describe issue in detail` field of the Kapture export and from the follow-up corpus, mapped to
the SOP that should answer it. `expect` is a set of keywords — a hit counts if ANY appears in the
chunk's title or disposition, which keeps the test robust to the corpus being re-ingested with
different chunk ids while still being specific about the answer.

CALIBRATION NOTE: a golden set written by the same person who wrote the scorer will flatter it.
These expectations were fixed BEFORE the scorer was changed and the baseline was recorded first,
so any improvement is measured against a number nobody could tune after the fact.

    BASELINE, measured 2026-08-31 on the set-overlap scorer:
        recall@1  31/40  77.5%
        recall@3  35/40  87.5%
        wrong@1    8/40  20.0%
        no hit     1/40   2.5%

An earlier draft of this docstring guessed "21/40" before the harness had ever run. It was wrong
by ten, which is the whole argument for having the harness.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts._contain import contain; contain()   # noqa: E402 — MUST precede every `app.` import

from app.knowledge import store                    # noqa: E402

#: (query, {keywords — any one in the title or disposition counts as correct})
GOLDEN: list[tuple[str, set[str]]] = [
    # ── losses & debits: hardstop ──────────────────────────────────────────────────────────
    ("hardstop kyun laga",                              {"hardstop"}),
    ("VL0084554575054 pe Rs 583 galat kata hai",        {"hardstop", "loss", "debit", "reversal"}),
    ("hardstop alert aaya lekin shipment deliver ho gaya", {"hardstop"}),
    ("shipment already delivered but hardstop mein ja raha hai", {"hardstop"}),
    ("mujhe hardstop ka debit wapas chahiye",           {"hardstop", "reversal"}),
    ("galat debit laga hai mere DC pe",                 {"loss", "debit", "hardstop", "reversal"}),
    # ── losses & debits: shortage ──────────────────────────────────────────────────────────
    ("shortage ka evidence kaise bhejun",               {"shortage", "evidence"}),
    ("shortage loss mark hua hai evidence dene ke baad", {"shortage", "evidence"}),
    ("cctv footage bhej diya phir bhi loss laga",       {"shortage", "cctv", "evidence"}),
    ("72 ghante ke andar cctv chahiye kya",             {"cctv", "shortage", "evidence"}),
    ("bag shortage mera fault nahi hai",                {"shortage"}),
    ("shortage kis hub pe hua tha",                     {"shortage"}),
    # ── losses & debits: other ─────────────────────────────────────────────────────────────
    ("dual scan mismatch",                              {"dual scan", "dual"}),
    ("secondary qc fail ka debit galat hai",            {"qc", "secondary"}),
    ("qc image reject kyun hui",                        {"qc"}),
    ("misroute shipment wapas kaise bhejun",            {"misroute"}),
    ("in transit mein shipment kho gaya",               {"transit", "pendency", "loss"}),
    ("wrong rvp pickup ka loss laga",                   {"rvp", "wrong"}),
    ("damage shipment ka debit laga hai",               {"damage"}),
    ("reversal kab process hoga",                       {"reversal"}),
    # ── payments ───────────────────────────────────────────────────────────────────────────
    ("mera payment nahi aaya",                          {"payment"}),
    ("payment kab aayega",                              {"payment"}),
    ("bank mein payment fail ho gaya",                  {"payment"}),
    ("invoice generate nahi hua",                       {"invoice", "payment"}),
    ("amount kam mila is cycle mein",                   {"payment", "mismatch", "count"}),
    ("shipment count mismatch hai invoice mein",         {"mismatch", "count"}),
    ("rate card galat hai",                             {"rate card", "rate"}),
    ("tds kaat liya gaya",                              {"tds", "payment"}),
    # ── COD / cash ─────────────────────────────────────────────────────────────────────────
    ("cod pendency clear karo",                         {"cod", "pendency"}),
    ("paisa deposit kiya lekin reflect nahi hua",        {"cod", "deposit", "pendency"}),
    ("razorpay qr se paid kiya phir bhi pendency",       {"razorpay", "cod", "pendency"}),
    ("cod shortfall ka debit laga",                      {"cod", "shortfall"}),
    # ── orders & planning ──────────────────────────────────────────────────────────────────
    ("mera load kam kyun hua",                          {"load", "order", "allocation", "planning"}),
    ("load zero ho gaya hai",                           {"load", "order", "allocation", "capacity"}),
    ("capacity cut kyun laga",                          {"capacity"}),
    ("promised load se kam mil raha hai",               {"load", "order", "allocation", "planning"}),
    ("orders kaise badhaun",                            {"load", "order", "allocation", "planning"}),
    ("pincode add karna hai mere DC mein",              {"pincode", "serviceab", "service area"}),
    ("rvp performance galat dikha raha hai",            {"rvp"}),
    # ── profile / FE ───────────────────────────────────────────────────────────────────────
    ("mera FE ID deactivate ho gaya",                   {"deactivat", "fe id", "reactivat"}),
]


def hit(chunk: dict, expect: set[str]) -> bool:
    hay = f"{chunk.get('title','')} {chunk.get('disposition','')} {chunk.get('id','')}".lower()
    return any(k.lower() in hay for k in expect)


def main() -> int:
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    n = len(GOLDEN)
    r1 = r3 = nohit = wrong1 = 0
    misses: list[str] = []
    for q, expect in GOLDEN:
        res = store.retrieve(q, k=3)
        if not res:
            nohit += 1
            misses.append(f"  NO HIT   {q!r}\n           expected any of {sorted(expect)}")
            continue
        top_ok = hit(res[0], expect)
        any_ok = any(hit(x, expect) for x in res)
        r1 += 1 if top_ok else 0
        r3 += 1 if any_ok else 0
        if not top_ok:
            wrong1 += 1
            got = " | ".join(f"{x['score']} {x['title'][:44]}" for x in res)
            misses.append(f"  WRONG@1  {q!r}\n           expected any of {sorted(expect)}"
                          f"\n           got: {got}")

    print(f"golden set: {n} real captain phrasings\n")
    print(f"  recall@1   {r1:>3}/{n}   {100*r1/n:>5.1f}%   (the composer reads the top hit)")
    print(f"  recall@3   {r3:>3}/{n}   {100*r3/n:>5.1f}%")
    print(f"  wrong@1    {wrong1:>3}/{n}   {100*wrong1/n:>5.1f}%   (confidently wrong citation)")
    print(f"  no hit     {nohit:>3}/{n}   {100*nohit/n:>5.1f}%   (cutoff ate everything)")
    if verbose and misses:
        print(f"\n{'─'*78}\nMISSES\n{'─'*78}")
        for m in misses:
            print(m)
    elif misses:
        print(f"\n  {len(misses)} miss(es) — re-run with --verbose to see them")

    # Not a pass/fail gate. This is a MEASUREMENT harness: the number is the point, and a
    # threshold here would only invite tuning the threshold. Always exits 0 so it can sit in
    # check_all without turning a regression into a red build somebody learns to ignore.
    print(f"\nRETRIEVAL MEASURED — recall@1 {100*r1/n:.1f}%, recall@3 {100*r3/n:.1f}%.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
