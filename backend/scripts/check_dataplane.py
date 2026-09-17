"""Offline assertion harness for the data-plane boundary. NO LLM CALLS, NO NETWORK.

The rule this enforces (see app/engine/dataplane.py):
    every identifier-shaped token in an outbound tool result must already appear in
    something the captain themselves said.

Why it exists as a script rather than a comment: the projections in tools.py are correct
today by inspection, and inspection does not survive the next tool, the next widened query,
or the next provider. A rule that nothing checks is a rule that decays.

Runs against REAL rows from valmo.db via LocalDbProvider — the provider with the most to
leak, because it is the only one carrying real AWBs, real 11-digit partner ids, and real
Meesho employee names in `metadata_attribution_marked_by`.

Usage:
    PSP_DATA_PROVIDER=localdb python scripts/check_dataplane.py
Exit code 0 = clean.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts._contain import contain; contain()   # MUST precede every `app.` import — see _contain.py
from scripts._harness import FAILED, check, head   # noqa: E402

os.environ.setdefault("PSP_DATA_PROVIDER", "localdb")   # the provider with the most to leak

from app.engine import dataplane, tools                      # noqa: E402
from app.engine.algo.entities import extract as extract_ents  # noqa: E402
from app.substrate import captain_context as ctx, loss_db     # noqa: E402



def main() -> int:
    if not loss_db.available():
        print("no loss DB — nothing to check against. Provide backend/data/valmo.db.")
        return 1

    partners = loss_db.known_partners()
    if not partners:
        print("no partners in `attribution`")
        return 1
    pid = partners[0]
    context = ctx.get_context(pid)
    if not context:
        print(f"no context for {pid} — is PSP_DATA_PROVIDER=localdb set?")
        return 1

    # A real AWB belonging to this captain, and one belonging to a DIFFERENT captain. The
    # second is the interesting case: it is the shape of an accidental cross-captain leak.
    own = [l["awb"] for l in context.get("losses", []) if l.get("awb")]
    other = [str(r["awb"]) for r in loss_db._query(
        "SELECT awb FROM attribution WHERE partner_id != ? AND awb != '' LIMIT 3", (pid,))]

    print(f"\nprovider : {context['_sources']['account']}")
    print(f"captain  : {pid}  ({len(context.get('losses', []))} loss row(s), hub "
          f"{context.get('profile', {}).get('hub_name', '?')})")

    # ── 1. the guard itself: does it see what it must see? ───────────────────────────
    print("\n[1] the guard's own detection")
    if own:
        check("detects a real AWB", own[0].upper() in dataplane.identifiers(own[0]),
              own[0])
    check("detects an 11-digit partner id", pid in dataplane.identifiers({"x": pid}), pid)
    check("detects a phone number", "9876543210" in dataplane.identifiers("call 9876543210"))
    check("a hub code is NOT an identifier", not dataplane.identifiers({"hub": "KDK"}),
          "facility codes are permitted by design")
    check("a self-id under its own key is not a disclosure",
          not dataplane.identifiers({"captain_id": pid}))
    check("the guard's AWB shape matches the lexer's",
          bool(dataplane.identifiers("VLR082347105569")) and bool(extract_ents("VLR082347105569")["awbs"]),
          "both must see VLR — they drifted once and it cost 25.6% of the ledger")

    # ── 2. every tool result, against a captain who supplied NOTHING ─────────────────
    # The strictest case: an empty allow-list, so ANY identifier in a result is a leak.
    print("\n[2] every tool result with an empty allow-list (the strict case)")
    calls = [
        ("get_captain_context", {}),
        ("run_data_query", {"query_name": "loss_summary"}),
        ("run_data_query", {"query_name": "payout_status"}),
        ("run_data_query", {"query_name": "cod_status"}),
        ("run_data_query", {"query_name": "shipment_status"}),
        ("run_data_query", {"query_name": "scan_history"}),
        ("search_sops", {"query": "hardstop loss reversal"}),
    ]
    for name, args in calls:
        result, events, _c, _a = tools.dispatch(name, args, pid, context)
        leaks = dataplane.violations(result, set())
        check(f"{name}({args.get('query_name', '')})".ljust(40), not leaks,
              f"{len(json.dumps(result, default=str))} chars out"
              + ("" if not leaks else f" · LEAKED {leaks[:4]}"))

    # ── 3. the corpus check a regex cannot do: employee names ────────────────────────
    # `naveen` is indistinguishable from any other word, so no pattern can find it. These
    # names are kept out by PROJECTION — the tool results are built from a hand-written key
    # set that has no route to the column — and this check is what proves the projection
    # actually holds against the real values in the table.
    print("\n[3] employee names (structural patterns cannot catch these)")
    raw = [str(r["v"]).strip() for r in loss_db._query(
        "SELECT DISTINCT metadata_attribution_marked_by AS v FROM attribution"
        " WHERE v IS NOT NULL AND v != ''", ())]
    marked = {v.lower() for v in raw if v}
    print(f"  sampled {len(raw)} distinct raw value(s) → {len(marked)} name(s): "
          + ", ".join(sorted(marked)[:6]))
    for name, args in calls:
        result, _e, _c, _a = tools.dispatch(name, args, pid, context)
        blob = json.dumps(result, default=str).lower()
        hits = sorted(n for n in marked if n in blob)
        check(f"no marked_by name in {name}({args.get('query_name', '')})".ljust(52),
              not hits, ", ".join(hits[:5]) if hits else "")
    # And prove the check has teeth: it must fire on a payload that DOES carry a name.
    if marked:
        n0 = sorted(marked)[0]
        check("the check itself detects a planted name",
              n0 in json.dumps({"marked_by": n0}).lower(), f"planted {n0!r}")

    # ── 4. the subset rule, both directions ─────────────────────────────────────────
    print("\n[4] the subset rule")
    if own and other:
        allowed = dataplane.supplied(f"{own[0]} par galat loss laga hai")
        check("an AWB the captain typed is allowed through",
              not dataplane.violations({"reason": f"₹244 debit on {own[0]}"}, allowed), own[0])
        check("another captain's AWB is a violation",
              bool(dataplane.violations({"reason": f"see also {other[0]}"}, allowed)), other[0])
        red = dataplane.redact({"a": own[0], "b": other[0]}, allowed)
        check("redaction keeps the supplied token and masks the other",
              red["a"] == own[0].upper() and "redacted" in red["b"], str(red))

    # ── 5. the materialised table agrees with the live computation ───────────────────
    print("\n[5] captain_summary: materialised vs live")
    if loss_db._has("captain_summary"):
        bad = []
        for p in partners:
            live, mat = loss_db.captain_summary(p), loss_db.read_captain_summary(p)
            for k in ("debits_on_record", "open_debits", "total_debited_inr",
                      "recovered_inr", "pending_inr", "reversals"):
                if round(float(live.get(k) or 0)) != round(float(mat.get(k) or 0)):
                    bad.append(f"{p}.{k}: live {live.get(k)} vs table {mat.get(k)}")
        check(f"all {len(partners)} materialised row(s) match", not bad, "; ".join(bad[:3]))
        check("the read path used the table",
              bool(loss_db.read_captain_summary(partners[0]).get("materialised_at")))
    else:
        check("captain_summary table present", False,
              "run scripts/build_captain_summary.py (the engine still works — it falls back "
              "to computing live, so this is a warning about latency, not correctness)")
        FAILED.remove("captain_summary table present")   # a missing optimisation is not a failure

    # ── 6. the cost side of the same change ─────────────────────────────────────────
    print("\n[6] payload size (the cost argument for the same change)")
    agg, _e, _c, _a = tools.dispatch("get_captain_context", {}, pid, context)
    before = {"profile": {"name": context.get("profile", {}).get("name"),
                          "hub": context.get("profile", {}).get("hub_name")},
              "debits": [{"id": d["id"], "amount_inr": d["amount_inr"], "date": d["date"],
                          "reason": d.get("reason"), "awb": d.get("awb")}
                         for d in context.get("ledger", []) if d.get("type") == "debit"],
              "losses": context.get("losses", []),
              "shipments": context.get("shipments", [])}
    b, a2 = len(json.dumps(before, default=str)), len(json.dumps(agg, default=str))
    print(f"  get_captain_context: {b:,} chars → {a2:,} chars "
          f"({100 - round(100 * a2 / b)}% smaller, resent on EVERY later step and turn)")

    # ── 7. apply_policy: the case that proves the rule is a SUBSET test ─────────────
    # apply_policy's reason string legitimately names the disputed AWB ("a credit note is
    # already on record for AWB VL…"). A blanket ban on identifiers would make that sentence
    # unwritable; the subset rule permits it precisely because the captain typed it — and
    # still catches it if the AWB came from somewhere else.
    print("\n[7] apply_policy — an echoed identifier vs an invented one")
    # Walk this captain's AWBs and report BOTH shapes, because the two branches differ and
    # each one matters:
    #   · a branch whose reason NAMES the awb ("a credit note is already on record for AWB
    #     VL…") — the case the subset rule exists for, and the case a blanket ban would break
    #   · the escalation branch, whose relay line deliberately carries no identifier at all
    # Sample AWBs that reach DIFFERENT branches, not just this captain's rows — all 39 of
    # theirs escalate, and the escalation branch is the one that carries no identifier, so a
    # captain-only sample would never exercise the interesting case. apply_policy looks a loss
    # up by awb independently of who is asking, so this is the same code path.
    branchy = [str(r["awb"]) for r in loss_db._query(
        "SELECT awb FROM losses WHERE facility_inscan != '' LIMIT 6", ())] \
        + [str(r["awb"]) for r in loss_db._query(
            "SELECT awb FROM losses WHERE reason_l1 = 'debit_revoked' LIMIT 6", ())] \
        + [str(r["awb"]) for r in loss_db._query(
            "SELECT awb FROM losses WHERE loss_percentage = '0%' LIMIT 6", ())]
    echoing = silent = None
    for awb in list(dict.fromkeys(branchy)) + own[:20]:
        res, _e, _c, act = tools.dispatch(
            "apply_policy", {"disposition": "hardstop_loss", "awb": awb}, pid, context)
        if dataplane.violations(res, set()) and echoing is None:
            echoing = (awb, act, res)
        elif not dataplane.violations(res, set()) and silent is None:
            silent = (awb, act, res)
        if echoing and silent:
            break
    if echoing:
        awb, act, res = echoing
        allowed = dataplane.supplied(f"{awb} pe galat loss laga hai")
        check(f"action={act} · the echoed awb passes when the captain supplied it",
              not dataplane.violations(res, allowed), (res.get("reason") or "")[:64] + "…")
        check("       · and is caught when they did not (empty allow-list)",
              bool(dataplane.violations(res, set())),
              f"{len(dataplane.violations(res, set()))} token(s) — the check is live, not vacuous")
    else:
        print("  --   no branch in this captain's rows echoes an awb; subset rule covered by [4]")
    if silent:
        awb, act, res = silent
        check(f"action={act} · carries no identifier at all",
              not dataplane.violations(res, set()),
              "the escalation relay line names no awb by design")

    # ── a reply must never contradict the row it was composed from ──────────────────────────
    #
    # THE BUG THIS EXISTS FOR: `_eval_real_loss` branch 2 gated on `action_kind == "inform"` and
    # replied "already marked REVOKED/reversed on record. Nothing is pending from your side."
    # `inform` does not mean reversed — it means the outcome is decided. For the two largest
    # shortage sub-states the decided outcome is that the captain PAYS: 84,586 + 75,463 =
    # 160,049 rows, every one of them debited, each told their money had come back.
    #
    # A claim about money that contradicts its own source row should fail a harness, not a demo.
    head("[8] a reply never contradicts the row it came from")
    from app.engine import policy_exec as _pe
    from app.substrate import loss_db as _ldb
    _CTX = {"captain_id": "20020388788"}
    # One real AWB per debited sub-state — the exact rows that used to lie.
    for awb, note in (("VL0082826330217", "shortage, evidence never received, Rs 469"),
                      ("VLR081540945517", "shortage, evidence invalid, Rs 179"),
                      ("VL0083129710031", "shortage marked late, Rs 137")):
        row = _ldb.get_loss_by_awb(awb) or {}
        val = str(row.get("loss_value") or "0").replace(",", "")
        debited = (float(val or 0) > 0) and str(row.get("loss_percentage") or "") not in ("0%", "")
        d = _pe.execute(row.get("reason_l1") or "hardstop_loss", _CTX, {"awb": awb})
        said = (d.get("reason") or "").lower()
        # PAST tense only. "should be reversed, and I have raised it for reversal" is the
        # raise_for_reversal branch making a RECOMMENDATION, which is true and must not trip this.
        # The defect was a claim of COMPLETION — that the money is already back.
        claims_reversed = any(w in said for w in (
            "already marked revoked", "already reversed", "already credited",
            "credited back", "nothing is pending from your side"))
        check(f"{awb} — {note}", not (debited and claims_reversed),
              "row says the debit STANDS; the reply claimed it was reversed" if debited and claims_reversed
              else f"debited={debited}, action={d.get('action')}")
        # And the corollary: a live debit must arm the evidence follow-ups, or the captain who
        # needs the CCTV/72h route is the one who cannot be offered it.
        # Only when we ANSWERED. An escalated case deliberately arms nothing: `router._refusals`
        # declines the following turn with "previous turn escalated" rather than talk over a case
        # already with a human, so follow-up facts there would be state nothing can read.
        if debited and d.get("action") == "respond":
            check("   and arms the evidence follow-ups",
                  (d.get("followup_facts") or {}).get("debited") == "yes",
                  "a standing debit we answered must offer the evidence route")

    # ── declared actions that no row can trigger ────────────────────────────────────────────
    #
    # Reported, NOT failed. A policy may legitimately declare an action for a world state this
    # snapshot does not contain — `shortage_evidence_upheld` declares raise_for_reversal and all
    # 23,839 of its rows carry 0% loss, because those captains were never debited. That is the
    # policy being right about the world and wrong about the data, which is worth SEEING every
    # run rather than discovering when someone asks why a number is zero.
    head("[9] declared money actions vs what the data can actually trigger")
    import sqlite3 as _sq
    try:
        _c = _sq.connect("file:data/valmo.db?mode=ro", uri=True)
        _debited = ("SUM(CASE WHEN CAST(REPLACE(COALESCE(loss_value,'0'),',','') AS REAL) > 0 "
                    "AND TRIM(COALESCE(loss_percentage,'')) NOT IN ('0%','') THEN 1 ELSE 0 END)")
        # A shortage SUB-STATE is not a column value — `loss_db._refine_shortage` derives it at
        # read time from `losses.reason`. Querying reason_l1 for it matches nothing, which would
        # make this report silently say "0 unreachable" forever. Reverse the same map it uses.
        from app.substrate.loss_db import _SHORTAGE_SUBSTATE as _SUB
        _rev = {v: k for k, v in _SUB.items()}
        from app.knowledge import policies as _pol
        unreachable = []
        for _p in _pol.all_policies():
            if (_p.get("resolution") or {}).get("action") != "raise_for_reversal":
                continue
            _d = _p["disposition"]
            if _d in _rev:
                n, deb = _c.execute(f"SELECT COUNT(*), {_debited} FROM losses "
                                    "WHERE LOWER(TRIM(COALESCE(reason,''))) = ?", (_rev[_d],)).fetchone()
            else:
                n, deb = _c.execute(f"SELECT COUNT(*), {_debited} FROM losses "
                                    "WHERE LOWER(TRIM(COALESCE(reason_l1,''))) = ?", (_d,)).fetchone()
            if n and not (deb or 0):
                unreachable.append(f"{_d} ({n:,} rows, 0 debited)")
        for u in unreachable:
            print(f"       declared raise_for_reversal but unreachable: {u}")
        print(f"       {len(unreachable)} policy(ies) declare a money action no row can trigger "
              f"— reported, not failed")
    except Exception as e:  # noqa: BLE001 — a report must never fail the harness
        print(f"       (reachability report unavailable: {type(e).__name__})")

    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: " + "; ".join(FAILED))
        return 1
    print("all data-plane checks pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
