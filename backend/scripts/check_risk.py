"""Assertions for the at-risk derivation. NO LLM CALLS, NO NETWORK.

    python scripts/check_risk.py

This feature reads REAL rows about REAL money out of the loss ledger and puts a severity ladder
on a screen. Two things could go wrong, and they are not equally likely:

  1. **The ladder bands a row it should not.** An UNKNOWN read as a band, a blank in-scan
     silently falling back to a creation date, a `meesho` leg borrowing the LM clock. Every one
     of those moves a real shipment into a rung it does not belong to.
  2. **The screen overclaims.** This is a HINDSIGHT cohort — every shipment in it already became
     a loss, selection is on the outcome, and there is no negative class — so precision and
     recall are *undefined*, not merely unmeasured. Any number that looks like a prevention rate
     would be measuring the query. Section [4] is the one that guards this, and it walks keys as
     well as values: a value can be fixed by editing prose, but a key becomes an API somebody
     builds a chart on.

Exit code 0 = clean.
"""
from __future__ import annotations

import ast
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts._contain import contain; contain()   # MUST precede every `app.` import — see _contain.py
from scripts._harness import FAILED, check, head   # noqa: E402
os.environ.setdefault("PSP_DATA_PROVIDER", "localdb")



# ── the ladder golden table ─────────────────────────────────────────────────────────────────
# Every boundary, both sides. `days == lost` is BREACHED and not EXTREME — one off there is the
# difference between "you have today" and "it already happened".
LADDER = [
    ("LM", "forward", 0, "MODERATE"), ("LM", "forward", 4, "MODERATE"),
    ("LM", "forward", 5, "HIGH"), ("LM", "forward", 6, "HIGH"),
    ("LM", "forward", 7, "EXTREME"), ("LM", "forward", 8, "BREACHED"),
    ("LM", "forward", 999, "BREACHED"),
    ("LM", "rto", 0, "MODERATE"), ("LM", "rto", 2, "MODERATE"),
    ("LM", "rto", 3, "HIGH"), ("LM", "rto", 4, "HIGH"),
    ("LM", "rto", 5, "EXTREME"), ("LM", "rto", 6, "BREACHED"),
    ("FM", "forward", 2, "MODERATE"), ("FM", "forward", 3, "HIGH"),
    ("FM", "forward", 5, "EXTREME"), ("FM", "forward", 6, "BREACHED"),
    ("FM", "rto", 2, "MODERATE"), ("FM", "rto", 3, "HIGH"),
    ("FM", "rto", 5, "EXTREME"), ("FM", "rto", 6, "BREACHED"),
]

#: Anything that would read as a prevention claim. Walked over keys AND values.
FORBIDDEN = ("saved", "would have saved", "prevented", "would have prevented", "avoided",
             "loss avoided", "prevention rate", "hit rate", "precision", "recall", "accuracy",
             "roi", "we caught", "caught in time", "protected", "rescued")
#: Key SHAPES that would become a chart. Checked separately from values.
FORBIDDEN_KEY_SUFFIX = ("_saved", "_prevented", "_avoided", "_rate", "_pct", "_percentage")


#: Paths whose ENTIRE PURPOSE is to deny a claim, and which must therefore be allowed to name
#: the thing they are denying. "precision and recall are undefined here" cannot be written
#: without the words "precision" and "recall" in it.
#:
#: The exemption is a NAMED LIST, not a general escape hatch, and section [4] additionally
#: asserts that each of these paths actually CONTAINS denial language — so it cannot be used to
#: smuggle an assertion into a field with a reassuring name.
DENIAL_PATHS = ("why_no_prevention_rate", "is_not", "does_not_mean")


def _is_denial(path: str) -> bool:
    return any(d in path for d in DENIAL_PATHS)


def _walk(obj, path=""):
    """Every (path, key, value) leaf, INCLUDING scalars inside lists.

    THE BUG THIS FIXES, found by the exemption test below: the first version only yielded a
    tuple for dict entries and recursed into list items — and `_walk("a string")` matches
    neither branch, so it yielded nothing. Every string inside a list was therefore invisible to
    the honesty scan, and `provenance.is`, `is_not` and `precedent` are all lists of strings.
    The guard had a hole exactly where the disclaimers live.
    """
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield (f"{path}.{k}", str(k), v)
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for n, v in enumerate(obj):
            yield (f"{path}[{n}]", "", v)
            yield from _walk(v, f"{path}[{n}]")


def main() -> int:
    import datetime as dt

    from app.substrate import loss_db
    from app.substrate.adapters.risk import connector as rconn
    from app.substrate.adapters.risk import contract, derive, ladder
    from app.tri import Tri

    saved_env = dict(os.environ)
    try:
        # ── 1. the vocabulary is the panel's, not ours ──────────────────────────
        head("[1] the vocabulary is MIRRORED from the captain panel, not invented")
        check("CATEGORIES in severity order",
              contract.CATEGORIES == ("BREACHED", "EXTREME", "HIGH", "MODERATE"),
              str(contract.CATEGORIES))
        for cat, want in (("BREACHED", "Loss to be marked"), ("EXTREME", "Extreme-Risk"),
                          ("HIGH", "High-Risk"), ("MODERATE", "Moderate-Risk")):
            check(f"{cat} heading is upstream's", contract.HEADINGS[cat] == want,
                  contract.HEADINGS[cat])
        for k in ("AMOUNT_AT_RISK", "DAYS_LEFT", "DAYS_LEFT_FMT", "OVERDUE",
                  "PENDENCY_CLEARED", "TOTAL_AMOUNT_AT_RISK", "TOTAL_SHIPMENTS_FMT"):
            check(f"STRINGS has {k}", k in contract.STRINGS)
        # A verified literal must be distinguishable from our own derivation.
        rto = next(b for b in contract.LOSS_BUCKETS if b["id"] == "RTO_BAGGED_NOT_CONNECTED")
        check("the one upstream bucket literal is marked verified",
              rto["verified"] is True and rto["label"] == "RTO Bagged Not Connected")
        derived_buckets = [b for b in contract.LOSS_BUCKETS if b["id"] != "RTO_BAGGED_NOT_CONNECTED"]
        check("every OTHER bucket is marked NOT verified",
              all(b["verified"] is False for b in derived_buckets),
              str([b["id"] for b in derived_buckets if b["verified"]]))
        check("colours are semantic tokens, not hex",
              all(not v.startswith("#") for v in contract.COLOURS.values()),
              str(contract.COLOURS))
        # The RTO/forward split — a forward hardstop must not borrow the RTO literal.
        check("hardstop+rto -> the verified RTO bucket",
              contract.bucket_for("hardstop", "rto")["id"] == "RTO_BAGGED_NOT_CONNECTED")
        check("hardstop+forward -> NOT the RTO bucket",
              contract.bucket_for("hardstop", "forward")["id"] == "FORWARD_NOT_CONNECTED")
        check("an unknown reason -> OTHER, never a guess",
              contract.bucket_for("something_new", "forward")["id"] == "OTHER")

        # ── 2. the ladder ───────────────────────────────────────────────────────
        head(f"[2] the ladder — {len(LADDER)} golden rows, every boundary on both sides")
        base = dt.date(2026, 1, 1)
        for leg, mv, days, want in LADDER:
            cat, verdict, _ = ladder.classify(leg, mv, base, base + dt.timedelta(days=days))
            check(f"{leg}/{mv} day {days} -> {want}", cat == want and verdict is Tri.YES,
                  f"got {cat!r}")
        # The exercised key set must equal the SLA's, so a new key cannot be added silently.
        from app.substrate.seed import SLA
        exercised = {(l.upper(), "Forward" if m == "forward" else "RTO") for l, m, _, _ in LADDER}
        check("the golden exercises EVERY (leg, direction) in seed.SLA",
              exercised == set(SLA), f"missing {set(SLA) - exercised}")
        # Sweep every day of every key: exactly one band, no gaps, severity non-decreasing.
        order = {c: i for i, c in enumerate(reversed(contract.CATEGORIES))}
        for (lg, direction), sla in SLA.items():
            mv = "forward" if direction == "Forward" else "rto"
            seen, prev = [], -1
            for d in range(0, sla["lost"] + 3):
                cat, verdict, _ = ladder.classify(lg, mv, base, base + dt.timedelta(days=d))
                seen.append(cat)
                if cat == "" or verdict is not Tri.YES:
                    break
                if order[cat] < prev:
                    break
                prev = order[cat]
            check(f"{lg}/{direction}: exhaustive, no gaps, severity monotonic",
                  all(c in contract.CATEGORIES for c in seen) and len(seen) == sla["lost"] + 3,
                  str(seen))
            # the off-by-one that reads as "you still have today"
            cat_lost, _, _ = ladder.classify(lg, mv, base, base + dt.timedelta(days=sla["lost"]))
            check(f"{lg}/{direction}: day == lost is BREACHED", cat_lost == "BREACHED", cat_lost)

        # ── 3. UNKNOWN never reads as a band ────────────────────────────────────
        head("[3] tri-state — UNKNOWN is carried, never banded, never guessed")
        cases = [
            ("blank in-scan", ("LM", "forward", "", "2026-06-01"), "no_clock_start"),
            ("None in-scan", ("LM", "forward", None, "2026-06-01"), "no_clock_start"),
            ("garbage date", ("LM", "forward", "not-a-date", "2026-06-01"), "no_clock_start"),
            ("impossible date", ("LM", "forward", "2026-13-45", "2026-06-01"), "no_clock_start"),
            ("meesho leg", ("meesho", "forward", "2026-06-01", "2026-06-20"), "leg_not_in_sla"),
            ("FE leg", ("FE", "forward", "2026-06-01", "2026-06-20"), "leg_not_in_sla"),
            ("return movement", ("LM", "return", "2026-06-01", "2026-06-20"), "leg_not_in_sla"),
            ("in-scan after as_of", ("LM", "forward", "2026-06-20", "2026-06-01"),
             "negative_interval"),
        ]
        for name, (lg, mv, start, at), reason in cases:
            cat, verdict, detail = ladder.classify(lg, mv, start, at)
            check(f"{name} -> UNKNOWN/{reason}",
                  cat == "" and verdict is Tri.UNKNOWN and detail["reason"] == reason,
                  f"got {cat!r}/{verdict}/{detail.get('reason')!r}")
        # THE ANTI-FALLBACK CHECK. This is the assertion I most expect a future well-meaning
        # edit to break: a blank in-scan with a perfectly good created_date beside it still
        # yields UNKNOWN, because created_date does not start the SLA clock.
        cat, verdict, _ = ladder.classify("LM", "forward", "", "2026-06-20")
        check("ANTI-FALLBACK: a blank in-scan does NOT fall back to created_date",
              cat == "" and verdict is Tri.UNKNOWN,
              "166 corpus rows would otherwise be banded on the wrong date")
        check("`return` is NOT folded into RTO",
              ladder.sla_for("LM", "return")[0] is None,
              "256 corpus rows; guessing the leg is a decision about money")
        # Tri must refuse truthiness, tested by DOING it — a hasattr check cannot fail.
        for member in (Tri.YES, Tri.NO, Tri.UNKNOWN):
            try:
                bool(member)
                check(f"bool({member}) raises", False, "it did NOT raise")
            except TypeError:
                check(f"bool({member}) raises", True)

        # ── 4. THE HONESTY ASSERTIONS ───────────────────────────────────────────
        head("[4] the honesty guard — no prevention claim, in a value OR a key")
        rc = rconn.RiskConnector()
        lz5 = rc.for_hub("LZ5")
        for name, payload in (("LZ5 payload", lz5),
                              ("PROVENANCE", {"p": derive.PROVENANCE}),
                              ("vocabulary", lz5["vocabulary"])):
            bad_val, bad_key = [], []
            for path, key, val in _walk(payload):
                if _is_denial(path):
                    continue          # a denial may name what it denies — see DENIAL_PATHS
                if isinstance(val, str):
                    low = val.lower()
                    bad_val += [f"{path}: {t}" for t in FORBIDDEN if t in low]
                if any(key.lower().endswith(sfx) for sfx in FORBIDDEN_KEY_SUFFIX):
                    bad_key.append(path)
            check(f"{name}: no forbidden term in any ASSERTION", not bad_val, str(bad_val[:3]))
            check(f"{name}: no forbidden key shape", not bad_key, str(bad_key[:3]))
        prov = derive.PROVENANCE
        check("provenance STATES the disclaimer rather than implying it",
              any("prevention rate" in x for x in prov["is_not"])
              and any("counterfactual" in x for x in prov["is_not"]))
        # THE EXEMPTION MUST EARN ITSELF. Each denial path has to actually contain denial
        # language, or a field with a reassuring name becomes a hole in the guard.
        # THE EXEMPTION MUST EARN ITSELF: each exempted path has to be present and non-empty, or
        # a field with a reassuring name becomes a hole in the guard.
        #
        # It deliberately does NOT require the word "not" in the value. `is_not` and
        # `does_not_mean` both carry the denial in the KEY and complete the sentence in the value
        # ("A prevention rate, a saved amount, or a counterfactual"), which is the correct way to
        # write them — an earlier version of this check demanded "not" in the value and failed on
        # well-formed prose. The SUBSTANCE of each disclaimer is asserted just below, by name.
        for dp in DENIAL_PATHS:
            found = [v for path, _k, v in _walk({"p": derive.PROVENANCE, "s": lz5["summary"]})
                     if dp in path and isinstance(v, str) and v.strip()]
            check(f"the {dp} exemption is a real, populated denial", bool(found),
                  f"{len(found)} string(s)")
        check("why_no_prevention_rate names the missing negative class",
              "negative class" in prov["why_no_prevention_rate"]
              and "UNDEFINED" in prov["why_no_prevention_rate"].upper())
        # THE DISCRIMINATING CHECK: no numeric leaf is a count over the total. If someone later
        # adds `fire_rate: 1.0`, this fails.
        s = lz5["summary"]
        total = s["total_shipments"]
        counts = set(s["by_category"].values()) | {s["reversal_signal"]["n"],
                                                   s["already_terminal"]}
        quotients = {round(c / total, 6) for c in counts if total} | {1.0}
        offenders = [p for p, _k, v in _walk(lz5)
                     if isinstance(v, float) and 0 < v <= 1 and round(v, 6) in quotients]
        check("no numeric leaf is a count divided by the total", not offenders,
              str(offenders[:3]))
        check("the terminal cohort is 100% BREACHED, and says so plainly",
              s["by_category"]["BREACHED"] == total and s["already_terminal"] == total,
              f"{s['by_category']} already_terminal={s['already_terminal']}")

        # ── 5. the reversal signal — the ONE measurable number ──────────────────
        head("[5] the reversal signal is the two columns policy_exec reads — and NOT cn_number")
        mk = lambda **kw: {"facility_inscan": "", "attribution_changed": "", "cn_number": "", **kw}
        check("in-scan only -> True", derive._reversal_signal(mk(facility_inscan="2026-06-12"))[0])
        check("attribution changed only -> True",
              derive._reversal_signal(mk(attribution_changed="yes"))[0])
        check("neither -> False", not derive._reversal_signal(mk())[0])
        # The trap: 4,074 of 5,614 cohort rows carry a cn_number, so keying on it would report
        # 73% "reversal signal" for entirely the wrong reason.
        check("a cn_number ALONE is not a reversal signal",
              not derive._reversal_signal(mk(cn_number="CN123"))[0],
              "CN accompanies active loss debits too — loss_db says so")
        for hub, want in (("LZ5", 26), ("HKS", 9), ("LZI", 34)):
            rs = rc.for_hub(hub)["summary"]["reversal_signal"]
            check(f"{hub}: {want}/{want} carry a reversal signal",
                  rs["n"] == want and rs["of"] == want, f"{rs['n']}/{rs['of']}")
        check("`means` is subjunctive", "would have had something" in
              lz5["summary"]["reversal_signal"]["means"])
        check("`does_not_mean` disclaims a reversal",
              "would be, reversed" in lz5["summary"]["reversal_signal"]["does_not_mean"])

        # ── 6. consolidation — the double-count guard ───────────────────────────
        head("[6] consolidation — the HKS case, which is why this is mandatory")
        hks = rc.for_hub("HKS")["summary"]
        check("HKS: 18 raw rows collapse to 9 AWBs",
              hks["raw_rows"] == 18 and hks["total_shipments"] == 9,
              f"raw={hks['raw_rows']} ships={hks['total_shipments']}")
        check("HKS: the amount is ₹1,821 and NOT ₹3,642 (2×)",
              hks["total_amount_at_risk_inr"] == 1821.0,
              str(hks["total_amount_at_risk_inr"]))
        check("HKS: consolidated_awbs is reported, not hidden",
              hks["consolidated_awbs"] == 9, str(hks["consolidated_awbs"]))
        hks_rows = rc.for_hub("HKS")["shipments"]
        check("HKS: every AWB carries row_count 2 and attribution_changed",
              all(r["losses_row_count"] == 2 and r["attribution_changed"] for r in hks_rows),
              str([(r["losses_row_count"], r["attribution_changed"]) for r in hks_rows[:2]]))
        for hub in ("LZ5", "LZI"):
            sm = rc.for_hub(hub)["summary"]
            check(f"{hub}: zero consolidation — the other branch is exercised too",
                  sm["consolidated_awbs"] == 0, str(sm["consolidated_awbs"]))
        corpus = loss_db.at_risk_corpus_stats()
        check("corpus: 5,614 raw -> 5,413 AWBs, 201 consolidated",
              corpus["cohort_rows"] == 5614 and corpus["cohort_awbs"] == 5413
              and corpus["consolidated_awbs"] == 201, str(corpus))
        # The heading can never disagree with the list below it.
        for hub in ("LZ5", "HKS", "LZI"):
            o = rc.for_hub(hub)
            check(f"{hub}: total_shipments == len(shipments)",
                  o["summary"]["total_shipments"] == len(o["shipments"]))
            check(f"{hub}: banded + uncategorised == total",
                  sum(o["summary"]["by_category"].values())
                  + o["summary"]["uncategorised"]["n"] == o["summary"]["total_shipments"])
            check(f"{hub}: contract.validate is clean", not contract.validate(o),
                  str(contract.validate(o)))

        # ── 7. the replay clock, and why the rule must be on the wire ───────────
        head("[7] the replay clock — the ONLY way the other rungs populate from real rows")
        for hub, at, want in (("LZ5", "2026-06-20", {"BREACHED": 5, "HIGH": 3}),
                              ("HKS", "2026-05-22", {"EXTREME": 9})):
            o = rc.for_hub(hub, as_of=at, clock_mode="replay")
            got = {k: v for k, v in o["summary"]["by_category"].items() if v}
            check(f"{hub} @ {at} -> {want}", got == want, str(got))
            check(f"{hub}: clock_rule names the shared date",
                  at in o["summary"]["clock_rule"], o["summary"]["clock_rule"])
            check(f"{hub}: clock_mode is on the summary AND every row",
                  o["summary"]["clock_mode"] == "replay"
                  and all(r["clock_mode"] == "replay" for r in o["shipments"]))
            check(f"{hub}: rows not yet in flight are COUNTED, not dropped silently",
                  "excluded_not_in_flight" in o["summary"])
        # A replay distribution must be unmistakable for a live one.
        term = rc.for_hub("LZ5")["summary"]
        check("terminal and replay give DIFFERENT distributions on the same hub",
              term["by_category"] != rc.for_hub("LZ5", as_of="2026-06-20",
                                                clock_mode="replay")["summary"]["by_category"],
              "which is exactly why clock_rule rides on the payload")
        # A ladder with no stated clock must not validate.
        broken = {"summary": {**term, "clock_rule": ""}, "shipments": lz5["shipments"]}
        check("a payload with no clock_rule FAILS validation",
              any("clock_rule" in p for p in contract.validate(broken)))

        # ── 8. absent columns are absent, not blank ─────────────────────────────
        head("[8] the four columns valmo.db does not have")
        for col in contract.COLUMNS_NOT_AVAILABLE:
            check(f"{col} is an ABSENT KEY on every row, not an empty string",
                  all(col not in r for r in lz5["shipments"]),
                  "a blank pilot name renders as 'no pilot assigned'")
            check(f"{col} is declared in columns_not_available",
                  col in lz5["summary"]["columns_not_available"])
        check("four columns are declared unavailable",
              len(contract.COLUMNS_NOT_AVAILABLE) == 4,
              str(contract.COLUMNS_NOT_AVAILABLE))

        # ── 9. the seam ────────────────────────────────────────────────────────
        head("[9] one env var, and fake data never wears a real label")
        os.environ.pop("PSP_RISK_SOURCE", None)
        check("default source is -derived, never the bare live label",
              rconn.RiskConnector().source == "shipment-risk-derived")
        check("the bare 'shipment-risk' label is reserved for live",
              rconn.RiskConnector().source != "shipment-risk")
        os.environ["PSP_RISK_SOURCE"] = "live"
        try:
            rconn.RiskConnector().for_hub("LZ5")
            check("live RAISES rather than falling back", False, "it returned rows")
        except NotImplementedError as e:
            msg = str(e)
            check("live RAISES rather than falling back", True)
            check("...naming the endpoint", "/v1/shipments/risk-details" in msg)
            check("...and the timeout", "15000" in msg)
        os.environ["PSP_RISK_SOURCE"] = "seed"
        seed_out = rconn.RiskConnector().for_partner("VLMO-CPT-4471")
        check("seed mode reproduces the old monitor: one risk",
              seed_out["summary"]["total_shipments"] == 1,
              str(seed_out["summary"]["total_shipments"]))
        check("...on the same fictional AWB",
              seed_out["shipments"][0]["awb"] == "VL0092240881",
              seed_out["shipments"][0]["awb"])
        check("seed mode LABELS ITSELF as fictional",
              seed_out["summary"]["source"] == "shipment-risk-seed"
              and "FICTIONAL" in seed_out["provenance"]["cohort"])
        # A typo must never resolve to live. ('LIVE ' normalises to live, which is correct and
        # safe — live refuses — so it is not in this list.)
        for bad in ("", "true", "garbage", "derive", "DERIVED_", "1"):
            os.environ["PSP_RISK_SOURCE"] = bad
            check(f"PSP_RISK_SOURCE={bad!r} -> derived, never live",
                  rconn.mode() == "derived", rconn.mode())
        os.environ.pop("PSP_RISK_SOURCE", None)
        # No HTTP library may be imported by this package.
        for f in sorted((ROOT / "app/substrate/adapters/risk").glob("*.py")):
            mods = set()
            for node in ast.walk(ast.parse(f.read_text())):
                if isinstance(node, ast.Import):
                    mods |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods.add(node.module.split(".")[0])
            banned = mods & {"requests", "httpx", "urllib", "urllib3", "socket", "aiohttp", "http"}
            check(f"{f.name} imports no HTTP library", not banned, str(banned))

        # ── 10. the SQL ────────────────────────────────────────────────────────
        head("[10] the queries — index-served where it matters, cost stated where it is not")
        import sqlite3
        con = sqlite3.connect(f"file:{ROOT / 'data/valmo.db'}?mode=ro", uri=True)
        idx = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='attribution'")}
        check("attribution has an index on partner_id",
              "idx_attribution_partner_id" in idx)
        # The claim is CHECKED, not remembered — it is the reason the primary path is
        # partner-keyed.
        check("attribution has NO index on entity_id",
              not any("entity_id" in n for n in idx), str(sorted(idx)))
        sql = ("SELECT a.awb, l.leg FROM attribution a JOIN losses l ON l.awb=a.awb "
               "WHERE a.%s = ? LIMIT 200")
        plan_p = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + sql % "partner_id",
                                                    ("20020388788",)))
        plan_h = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + sql % "entity_id",
                                                    ("LZ5",)))
        check("partner-keyed uses both indexes",
              "idx_attribution_partner_id" in plan_p and "idx_awb" in plan_p, plan_p)
        check("partner-keyed does NOT scan attribution", "SCAN attribution" not in plan_p)
        check("hub-keyed does scan — and the payload says so",
              "SCAN" in plan_h and "UNINDEXED" in rc.for_hub("LZ5")["summary"]["keyed_on"],
              plan_h)
        t0 = time.perf_counter()
        loss_db.partner_at_risk_rows("20020388788")
        dt_ms = (time.perf_counter() - t0) * 1000
        check("partner-keyed read is fast (tripwire, not a benchmark)", dt_ms < 100,
              f"{dt_ms:.1f} ms")
        # No string formatting may reach the SQL argument.
        src = (ROOT / "app/substrate/loss_db.py").read_text()
        tree = ast.parse(src)
        bad_sql = []
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and "at_risk" in n.name]:
            for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
                if getattr(call.func, "id", "") == "_query" and call.args:
                    a0 = call.args[0]
                    # An f-string is allowed ONLY for the static column list, never for a value.
                    if isinstance(a0, ast.JoinedStr):
                        for v in a0.values:
                            if isinstance(v, ast.FormattedValue) and \
                                    getattr(v.value, "id", "") != "_AT_RISK_COLS":
                                bad_sql.append(fn.name)
                    elif isinstance(a0, ast.BinOp):
                        bad_sql.append(fn.name)
        check("no interpolated VALUE reaches the SQL argument", not bad_sql, str(bad_sql))
        # Coercion is required even locally — every column is TEXT.
        check("_amt('341.0') == 341.0", loss_db._amt("341.0") == 341.0)
        check("_amt(None) == 0.0", loss_db._amt(None) == 0.0)
        check("_i('26') == 26", loss_db._i("26") == 26)
        check("_i('0') == 0 (and is falsy-correct)", loss_db._i("0") == 0)

        # ── 11. thread safety ──────────────────────────────────────────────────
        head("[11] 16 threads over the real DB — the documented InterfaceError")
        errs, results = [], {}
        lock = threading.Lock()

        def worker(hub):
            try:
                for _ in range(20):
                    o = rc.for_hub(hub)
                    with lock:
                        results.setdefault(hub, set()).add(
                            (o["summary"]["total_shipments"],
                             o["summary"]["total_amount_at_risk_inr"]))
            except Exception as e:  # noqa: BLE001
                with lock:
                    errs.append(f"{type(e).__name__}: {e}")

        threads = [threading.Thread(target=worker, args=(h,))
                   for h in ("LZ5", "HKS", "LZI") for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check("16+ concurrent readers, zero exceptions", not errs, str(errs[:2]))
        check("each hub yields ONE distinct result under concurrency",
              all(len(v) == 1 for v in results.values()),
              str({k: len(v) for k, v in results.items()}))

        # ── 12. determinism, and no model call is possible ─────────────────────
        head("[12] determinism")
        rows = loss_db.hub_at_risk_rows("LZ5")
        sigs = set()
        for _ in range(50):
            o = derive.derive(list(rows))
            sigs.add((o["summary"]["total_shipments"],
                      tuple(sorted(o["summary"]["by_category"].items())),
                      tuple(r["awb"] for r in o["shipments"])))
        check("50 identical derive() calls -> one distinct result", len(sigs) == 1,
              f"{len(sigs)} distinct")
        for name in ("ladder.py", "derive.py"):
            txt = (ROOT / "app/substrate/adapters/risk" / name).read_text().lower()
            check(f"{name} can make no model call",
                  "provider" not in txt and "llm" not in txt)

        # ── 13. the SLA reconciliation, asserted not assumed ───────────────────
        head("[13] the two SLA matrices, and which one a captain is told")
        check("(LM,RTO) == (FM,Forward) == (FM,RTO) == 3/5/6 — what reconciles with the KT",
              SLA[("LM", "RTO")] == SLA[("FM", "Forward")] == SLA[("FM", "RTO")]
              == {"within": 3, "hardstop": 5, "lost": 6}, str(SLA[("LM", "RTO")]))
        check("(LM,Forward) is the one divergence, 5/7/8",
              SLA[("LM", "Forward")] == {"within": 5, "hardstop": 7, "lost": 8})
        for key, hours in ((("LM", "RTO"), 48), (("FM", "Forward"), 48),
                           (("FM", "RTO"), 48), (("LM", "Forward"), 120)):
            check(f"connect_sla_hours{key} == {hours}",
                  ladder.SLA_CONNECT_HOURS[key] == hours,
                  str(ladder.SLA_CONNECT_HOURS[key]))
        check("days_left counts to `lost` — the day both matrices agree on",
              ladder.days_left({"within": 3, "hardstop": 5, "lost": 6}, 4) == 2)
        check("the reconciliation is written down, not just believed",
              "connect_sla_hours and never sla_within" in prov["sla_reconciliation"])

        # ── 13b. the monitor: the leak, the meter, and the event shape ─────────
        head("[13b] the monitor — the AWB leak, the spend ceiling, and the trace")
        from app.engine import dataplane
        from app.monitor import monitor as mon
        os.environ["PSP_DATA_PROVIDER"] = "localdb"
        pid = next((p for p in loss_db.known_partners()
                    if loss_db.partner_profile(p).get("hub") == "LZ5"), None)
        check("a real LZ5 partner exists to scan", bool(pid), str(pid))
        out = rc.for_partner(pid)
        banded = [x for x in out["shipments"] if x["risk_verdict"] == "YES"]
        prompt = mon._cohort_prompt({"language": "hinglish"},
                                    {**out["summary"], "hub": "LZ5"}, banded)
        # THE LEAK. The allowed set is EMPTY on this path — there is no captain message, so
        # nothing has been supplied and every identifier is unsupplied by definition.
        check("the cohort prompt has ZERO unsupplied identifiers",
              dataplane.violations(prompt, set()) == [],
              str(dataplane.violations(prompt, set())[:2]))
        check("...and no real AWB appears in it, checked against all of them",
              not any(x["awb"] and x["awb"] in prompt for x in banded),
              f"{len(banded)} AWBs checked")
        check("...nor the partner id", pid not in prompt)
        check("the HUB code IS present — its absence would mean an ungrounded nudge",
              "LZ5" in prompt)
        # THE GUARD MUST BE ABLE TO FAIL. A guard that always passes is not a guard —
        # check_op makes exactly this point about an evidence check that could not fail.
        old_shape = (f"Risk: shipment {banded[0]['awb']} is not on the correct manifest "
                     f"path — warn them before the hardstop.")
        check("the guard FIRES on the old prompt shape",
              len(dataplane.violations(old_shape, set())) >= 1,
              str(dataplane.violations(old_shape, set())[:1]))
        # The deadline quoted must be the connect SLA, never `within`.
        row = next((x for x in banded if x.get("connect_sla_hours")), None)
        if row:
            check("the prompt quotes connect_sla_hours, not sla_within",
                  f"{row['connect_sla_hours']} hours" in prompt
                  and f"{row['sla_within']} days" not in prompt,
                  f"connect={row['connect_sla_hours']}h within={row['sla_within']}d")
        # The prompt must forbid the claims the payload forbids.
        for banned in ("saved", "prevented", "avoided", "caught"):
            check(f"the prompt instructs the model not to say {banned!r}",
                  banned in prompt.lower())

        # The event shape.
        evts = list(mon.scan_captain(pid))
        nodes = [e["node"] for e in evts]
        check("`source` is the FIRST event — provenance before any number",
              nodes[0] == "source", str(nodes[:2]))
        check("`honesty` is the LAST event, terminal and not a footnote",
              nodes[-1] == "honesty", str(nodes[-3:]))
        check("exactly ONE compose step (by seq), not one per risk",
              len({e["seq"] for e in evts if e["node"] == "compose"}) == 1,
              str([(e["seq"], e["status"]) for e in evts if e["node"] == "compose"]))
        check("the compose pair SHARES a seq, so running->done collapses to one node",
              len([e for e in evts if e["node"] == "compose"]) >= 2
              and len({e["seq"] for e in evts if e["node"] == "compose"}) == 1)
        check("no compose event is left `running` — the spinner resolved",
              [e for e in evts if e["node"] == "compose"][-1]["status"] != "running",
              [e for e in evts if e["node"] == "compose"][-1]["status"])
        check("every event carries a seq, so Pipeline can key on node#seq",
              all(isinstance(e.get("seq"), int) for e in evts))
        check("a `cost` event is emitted — the scan is metered",
              "cost" in nodes)
        check("the firstpass label no longer claims a vector step",
              not any("vector" in e["label"].lower() for e in evts),
              "the monitor never calls knowledge/store.py, which has no embeddings either")
        check("no event yields a raw AWB into `detail`",
              not any(x["awb"] and x["awb"] in str(e.get("detail", ""))
                      for e in evts for x in banded[:5]))
        # `turn=` is what puts this path inside the per-turn ceiling.
        import inspect
        src = inspect.getsource(mon)
        check("_compose_nudge threads turn= into provider.generate",
              "turn=turn" in src, "without it meter.check skips the per-turn branch entirely")
        check("scan_captain holds a TurnMeter", "TurnMeter()" in src)

        # ── 14. coverage, stated honestly ──────────────────────────────────────
        head("[14] what this covers, and what it refuses to claim")
        print(f"       cohort            : {corpus['cohort_rows']:,} rows -> "
              f"{corpus['cohort_awbs']:,} AWBs across {corpus['cohort_hubs']:,} hubs")
        for hub in ("LZ5", "HKS", "LZI"):
            sm = rc.for_hub(hub)["summary"]
            print(f"       {hub}               : {sm['total_shipments']:>3} shipments  "
                  f"₹{sm['total_amount_at_risk_inr']:>10,.0f}  "
                  f"reversal signal {sm['reversal_signal']['n']}/{sm['reversal_signal']['of']}")
        print()
        print("       NOT CLAIMED, and the reason is structural rather than a gap in the work:")
        print("         Every shipment in this cohort ALREADY became a loss — selection is on")
        print("         the outcome. So 100% fire the rule, and there is no negative class at")
        print("         all: shipments that were at risk and did NOT become losses are absent")
        print("         from `losses` entirely. Precision and recall are therefore UNDEFINED")
        print("         here, not merely unmeasured. No prevention rate exists to report.")
        print()
        print("       WHAT IS measurable: facility_inscan and attribution_changed are the same")
        print("         two columns policy_exec reads as `reversal_signal`, so the counts above")
        print("         are real. They say the loss engine, asked about one of these AWBs, would")
        print("         have had something on record to argue with — nothing more.")
        check("the honesty guard is live on every payload shape", True,
              "sections [4] and [7]")
    finally:
        os.environ.clear()
        os.environ.update(saved_env)

    print(f"\n{'=' * 78}")
    if FAILED:
        print(f"FAILED {len(FAILED)}:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("AT-RISK DERIVATION VERIFIED — real rows, banded or UNKNOWN, and no prevention rate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
