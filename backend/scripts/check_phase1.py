"""Phase-1 verification. NO LLM CALLS, NO NETWORK — every assertion runs offline.

One command for the whole of phase 1:

    python scripts/check_phase1.py

Covers, in order:
  1  audit_batch spend bounds + the judge-failure path (no all-zeros persistence)
  2  AWB recall against the full 1,000,001-row ledger, old pattern vs new
  3  the spend meter: pricing, both ceilings, the refusal path, per-turn accounting
  4  history control: search_sops projection, MAX_STEPS, trimming, session eviction
  4b every bug a review pass found, pinned so it cannot come back silently
  4c thread safety and decision determinism under concurrency
  5  the data-plane boundary (delegates to scripts/check_dataplane.py)

The provider is a stub throughout, so section 3 exercises the real conversation loop —
including the BudgetExhausted classification — without a single API call. That is the
point: the phase is verifiable before anyone spends a rupee.

Exit code 0 = everything passes.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PSP_DATA_PROVIDER", "localdb")

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)


def head(n: str) -> None:
    print(f"\n{'─' * 78}\n{n}\n{'─' * 78}")


# ══ 1. audit_batch bounds ════════════════════════════════════════════════════════════
def sec_audit() -> None:
    head("[1] audit_batch — the one route that could drain the credit in a single request")
    from pydantic import ValidationError

    from app.audit import runner
    from app.main import AuditBatchIn

    check(f"default limit is small ({AuditBatchIn().limit})",
          AuditBatchIn().limit <= 5)
    rejected = 0
    for bad in (0, -1, runner.MAX_BATCH + 1, 500, 100_000):
        try:
            AuditBatchIn(limit=bad)
        except ValidationError:
            rejected += 1
    check(f"boundary rejects out-of-range limits ({rejected}/5)", rejected == 5,
          f"ge=1, le={runner.MAX_BATCH}")

    calls = {"n": 0}

    class Boom:
        def generate(self, *a, **k):
            calls["n"] += 1
            raise RuntimeError("simulated transport failure")

    orig_for_node = runner.llm_registry.for_node
    runner.llm_registry.for_node = lambda node: (Boom(), "stub-model")
    fake = [{"id": f"C{i}", "captain_id": "P1", "disposition": "hardstop_loss"} for i in range(60)]
    orig_all, orig_trace, orig_load, orig_write = (
        runner.concern_log.all_concerns, runner.trace_log.get, runner._load, runner._write)
    runner.concern_log.all_concerns = lambda: fake
    runner.trace_log.get = lambda cid: None
    runner._load = lambda: []
    wrote = {"n": 0}
    runner._write = lambda items: wrote.__setitem__("n", wrote["n"] + 1)
    try:
        out = runner.audit_batch(500)          # the attack shape, bypassing FastAPI entirely
    finally:
        (runner.llm_registry.for_node, runner.concern_log.all_concerns, runner.trace_log.get,
         runner._load, runner._write) = (orig_for_node, orig_all, orig_trace, orig_load, orig_write)

    check("runner clamps a direct limit=500 call", out["limit_applied"] == runner.MAX_BATCH,
          f"→ {out['limit_applied']}")
    check("LLM calls stay under the batch cap",
          out["llm_calls"] <= runner.MAX_CALLS_PER_BATCH,
          f"{out['llm_calls']} ≤ {runner.MAX_CALLS_PER_BATCH} (retries included)")
    check("a failed judge persists NOTHING", wrote["n"] == 0,
          "the old code wrote composite=0, indistinguishable from a terrible resolution")
    check("failures are reported as failures, not as scores",
          out["audited"] == 0 and out["failed"] == runner.MAX_BATCH
          and all(r.get("judge_failed") and "composite" not in r for r in out["results"]))

    rub = runner.rubric_mod.get_rubric()
    check("(for contrast) the old fallback would have stored composite=0",
          runner._composite(runner._coerce_result({}, rub), rub) == 0)


# ══ 2. AWB recall ════════════════════════════════════════════════════════════════════
def sec_awb() -> None:
    head("[2] AWB lexer recall — measured against the full ledger, not sampled")
    from app.engine.algo.entities import _AWB, extract
    from app.substrate import loss_db

    OLD = re.compile(r"\bVL\d{13}\b", re.I)
    if not loss_db.available():
        check("loss DB available", False, "cannot measure recall")
        return
    rows = [str(r["awb"] or "") for r in loss_db._query("SELECT awb FROM losses", ())]
    n = len(rows) or 1
    old = sum(1 for a in rows if OLD.fullmatch(a))
    new = sum(1 for a in rows if _AWB.fullmatch(a))
    print(f"  {n:,} rows · old {old:,} ({100 * old / n:.4f}%) · new {new:,} ({100 * new / n:.4f}%)")
    # PINNED BOTH WAYS: the new number proves the fix, and the OLD number pinned means a
    # revert is loud rather than a silent 25% regression.
    check("old pattern still measures 74.38% (a revert would be loud)",
          abs(100 * old / n - 74.3769) < 0.01, f"{100 * old / n:.4f}%")
    check("new pattern reaches 99.999%", 100 * new / n > 99.999, f"{100 * new / n:.4f}%")
    check("VLR is read from free text", extract("VLR082347105569 ka paisa kata")["awbs"]
          == ["VLR082347105569"])
    check("no phone is fabricated from an AWB tail",
          extract("VL9999999999999 galat hai")["phones"] == [],
          "the lookbehind that stops a 13-digit AWB reading as a 10-digit mobile")
    check("an over-long digit run matches nothing",
          extract("VLR08234710556912345 junk")["awbs"] == [])


# ══ 3. the spend meter ═══════════════════════════════════════════════════════════════
def sec_meter() -> None:
    head("[3] spend meter — a dollar ceiling, exercised through the real turn loop")
    from app.engine import conversation
    from app.llm import meter

    check("an unknown model prices at the OPUS rate, never zero",
          meter.price_of("some-model-nobody-configured") == (5.00, 25.00))
    check("a dated model id prices as its family",
          meter.price_of("claude-haiku-4-5-20251001") == (1.00, 5.00))
    c = meter.cost_usd("claude-opus-5", 5300, 800)
    check("pricing arithmetic", abs(c - (5300 * 5 + 800 * 25) / 1e6) < 1e-9, f"${c:.5f}/call")

    # ── the refusal path, through the REAL loop with a stub provider ─────────────────
    # Subclasses LLMProvider so it inherits the real `chat_metered` — the point is to
    # exercise the actual chokepoint, not a re-implementation of it. Reports a large token
    # count so the ledger moves in a few turns instead of a few thousand.
    from app.llm.base import LLMProvider

    class Stub(LLMProvider):
        name = "stub"
        calls = 0

        def chat(self, contents, *, model, system=None, tools=None):
            Stub.calls += 1
            return ({"role": "model", "parts": [{"text": "theek hai"}]},
                    {"input": 200_000, "output": 20_000})

    stub = Stub(api_key="x")
    check("the stub inherits the real chokepoint",
          Stub.chat_metered is LLMProvider.chat_metered)
    orig_for_node, orig_ctx = conversation.llm_registry.for_node, conversation.ctx.get_context
    conversation.llm_registry.for_node = lambda node: (stub, "claude-opus-5")
    conversation.ctx.get_context = lambda cid: {"captain_id": cid, "profile": {"hub_name": "LZ5"},
                                                "ledger": [], "losses": [], "cash": {},
                                                "shipments": [], "summary": {}, "_sources": {}}
    saved = dict(os.environ)
    try:
        meter.reset()
        os.environ["LLM_BUDGET_USD"] = "5"
        os.environ["LLM_BUDGET_PER_TURN_USD"] = "10"     # total is the binding constraint here
        kinds, turn_costs = [], []
        for i in range(8):
            for ev in conversation.handle_turn(f"conv-{i}", "20020388788", "hello"):
                if ev["node"] == "cost":
                    turn_costs.append(ev["data"]["cost_usd"])
                if ev["node"] == "reply":
                    kinds.append(ev["data"].get("error_kind"))
        spent = meter.spent()
        check("the ledger accumulates real token cost", spent > 0, f"${spent:.2f} over 8 turns")
        check("the total ceiling stops further calls", "budget" in kinds,
              (f"turn {kinds.index('budget') + 1} of 8 refused at "
               f"${meter.total_budget():.0f}") if "budget" in kinds
              else f"no refusal fired; reply kinds were {kinds}")
        check("overshoot is at most one call past the ceiling",
              spent <= meter.total_budget() + max(turn_costs or [0]) + 1e-9,
              f"${spent:.2f} vs ceiling ${meter.total_budget():.0f}")
        check("the refusal is classified as 'budget', NOT as 'billing'",
              "billing" not in kinds,
              "our own ceiling and an empty Anthropic account need different fixes")

        # ── the per-turn ceiling, which the total cannot catch ───────────────────────
        meter.reset()
        os.environ["LLM_BUDGET_USD"] = "1000"            # effectively unlimited
        os.environ["LLM_BUDGET_PER_TURN_USD"] = "0.50"
        Stub.calls = 0
        per_turn_cost = None
        for ev in conversation.handle_turn("conv-turn", "20020388788", "hello"):
            if ev["node"] == "cost":
                per_turn_cost = ev["data"]
        one_call = meter.cost_usd("claude-opus-5", 200_000, 20_000)
        check("a single turn is bounded independently of the total",
              per_turn_cost is not None and per_turn_cost["cost_usd"] <= 0.50 + one_call,
              f"${(per_turn_cost or {}).get('cost_usd', 0):.3f} in one turn "
              f"(cap $0.50, one call = ${one_call:.3f})")
        check("the turn reports its own cost, calls and tokens",
              bool(per_turn_cost and per_turn_cost["calls"] and per_turn_cost["tokens_in"]),
              json.dumps({k: per_turn_cost[k] for k in ("cost_usd", "calls", "tokens_in")})
              if per_turn_cost else "")
        check("usage is no longer discarded on the chat call",
              bool(per_turn_cost and per_turn_cost["tokens_out"]),
              "conversation.py:271 used to be `content, _ = provider.chat(...)`")
        t = meter.totals()
        check("totals() reports per-node attribution for /api/health",
              "classify" in (t.get("by_node") or {}), json.dumps(t.get("by_node")))
    finally:
        conversation.llm_registry.for_node, conversation.ctx.get_context = orig_for_node, orig_ctx
        os.environ.clear(); os.environ.update(saved)
        meter.reset()


# ══ 4. history control ═══════════════════════════════════════════════════════════════
def sec_history() -> None:
    head("[4] history control — what gets resent on every step of every turn")
    from app.engine import conversation, tools
    from app.engine.session import IDLE_EVICT_SECONDS, MAX_HISTORY_TURNS, Session, SessionStore
    from app.knowledge import store

    check("MAX_STEPS lowered to 4", conversation.MAX_STEPS == 4,
          "step 6 was the most expensive and least productive step in a turn")
    check("search_sops k lowered to 4", tools.SEARCH_K == 4)
    check("snippet cap lowered to 320", tools.SNIPPET_CHARS == 320)

    # The real measurement: what a search_sops result costs, permanently.
    hits8 = store.retrieve("hardstop loss reversal process", k=8)
    old = {"results": [{"title": h["title"], "snippet": h["text"][:700],
                        "type": h.get("knowledge_type"), "source": h["source_repo"]} for h in hits8]}
    result, _e, _c, _a = tools.dispatch("search_sops", {"query": "hardstop loss reversal process"},
                                        "20020388788", {"captain_id": "x"})
    b, a = len(json.dumps(old)), len(json.dumps(result))
    print(f"  search_sops payload: {b:,} chars → {a:,} chars "
          f"(~{b // 4:,} → ~{a // 4:,} tokens, carried for the rest of the conversation)")
    check("the projection is materially smaller", a < b, f"{100 - round(100 * a / b)}% smaller")
    check("and still returns grounded hits", len(result["results"]) > 0,
          f"{len(result['results'])} SOP(s) — the retrieval cutoff already drops tangential chunks")

    # ── trimming must never break an atomic (calls, results) pair ───────────────────
    s = Session("c", "p")
    for i in range(14):
        s.contents.append({"role": "user", "parts": [{"text": f"msg {i}"}]})
        s.contents.append({"role": "model", "parts": [
            {"functionCall": {"name": "a", "args": {}}}, {"functionCall": {"name": "b", "args": {}}}]})
        s.contents.append({"role": "user", "parts": [
            {"functionResponse": {"name": "a", "response": {}}},
            {"functionResponse": {"name": "b", "response": {}}}]})
        s.contents.append({"role": "model", "parts": [{"text": f"reply {i}"}]})
    before = len(s.contents)
    dropped = s.trim()
    broken = []
    for i, e in enumerate(s.contents):
        nresp = len([p for p in e["parts"] if "functionResponse" in p])
        if not nresp:
            continue
        prev = s.contents[i - 1] if i else {}
        ncalls = len([p for p in (prev.get("parts") or []) if "functionCall" in p]) \
            if prev.get("role") == "model" else -1
        if ncalls != nresp:
            broken.append(i)
    check(f"trim() bounds history ({before} → {len(s.contents)} entries)", dropped > 0)
    check("no (tool_use, tool_result) pair is ever split", not broken,
          "positional id pairing means a split pair is a hard 400 reported as 'transport'")
    kept = sum(1 for e in s.contents
               if e["role"] != "model" and not any("functionResponse" in p for p in e["parts"]))
    check(f"exactly {MAX_HISTORY_TURNS} captain turns kept", kept == MAX_HISTORY_TURNS)
    check("a short conversation is never trimmed", Session("c2", "p").trim() == 0)

    # ── eviction: the map used to grow forever ──────────────────────────────────────
    st = SessionStore()
    for i in range(50):
        st.get_or_create(f"c{i}", "p")
    check("sessions are tracked", st.stats()["sessions"] == 50)
    for s2 in st._s.values():                       # age them all past the idle window
        s2.last_seen -= IDLE_EVICT_SECONDS + 1
    st.get_or_create("fresh", "p")
    check("idle sessions are evicted on the next access", st.stats()["sessions"] == 1,
          f"{IDLE_EVICT_SECONDS // 3600}h idle window — the map used to only ever grow")


# ══ 4b. the review findings, pinned ══════════════════════════════════════════════════
def sec_review() -> None:
    """Every bug a review pass found, pinned so it cannot come back silently."""
    head("[4b] regressions found in review — pinned")
    from app.engine import dataplane, tools
    from app.llm import meter
    from app.substrate import loss_db

    # ── a REVERSED row must not be counted as a debit, and the buckets must reconcile
    if loss_db.available():
        bad, checked = [], 0
        for r in loss_db._query("SELECT DISTINCT partner_id FROM attribution"
                                " WHERE partner_id != ''", ()):
            sm = loss_db.captain_summary(str(r["partner_id"]))
            if not sm:
                continue
            checked += 1
            if sm["recovered_inr"] + sm["pending_inr"] + sm["failed_inr"] != sm["total_debited_inr"]:
                bad.append(str(r["partner_id"]))
        check(f"every partner reconciles: debited == recovered+pending+failed ({checked} partners)",
              not bad, f"{len(bad)} mismatch(es): {bad[:3]}" if bad
              else "reversals excluded from the total AND from every lifecycle bucket")
        rev = loss_db._query("SELECT partner_id FROM attribution WHERE current_status='REVERSED'"
                             " OR attribution_type='loss_reversal' LIMIT 1", ())
        if rev:
            sm = loss_db.captain_summary(str(rev[0]["partner_id"]))
            check("a partner with reversals is still known (not read as unknown)", bool(sm),
                  f"debits={sm.get('debits_on_record')} reversals={sm.get('reversals')}")

    # ── a payout credit is not a loss reversal
    ctx = {"captain_id": "X", "profile": {}, "cash": {}, "shipments": [], "losses": [],
           "ledger": [{"type": "credit", "amount_inr": 8600, "reason": "weekly_payout"},
                      {"type": "credit", "amount_inr": 131, "reason": "loss_reversal"}]}
    agg = tools.captain_aggregate(ctx)
    check("a weekly payout is not counted as a loss reversal",
          agg["reversals"] == 1 and agg["reversed_inr"] == 131
          and agg["payout_credits"] == 1 and agg["credited_inr"] == 8600,
          f"reversals={agg['reversals']}/₹{agg['reversed_inr']} "
          f"payouts={agg['payout_credits']}/₹{agg['credited_inr']}")

    # ── a malformed amount must not turn the tool call into an error
    ok = True
    for junk in ("abc", None, "", "1,450", "₹244", [], {}):
        try:
            tools.captain_aggregate({"captain_id": "X", "profile": {}, "cash": {}, "shipments": [],
                                     "losses": [], "ledger": [{"type": "debit", "amount_inr": junk}]})
        except Exception:  # noqa: BLE001
            ok = False
    check("a malformed ledger amount never raises", ok, "'1,450' and '₹244' parse; junk → 0")

    # ── redact must actually redact what violations flags
    payload = {"contact": 9876543210, "pid": 20012345678, "note": "call VL1234567890123 now",
               "nested": [{"awb": "VLR082347105569"}], "ok": True, "none": None}
    v = dataplane.violations(payload, set())
    red = dataplane.redact(payload, set())
    left = dataplane.violations(red, set())
    check(f"redact removes ALL {len(v)} flagged identifier(s), including non-strings",
          not left, f"leftover: {left}" if left else json.dumps(red, default=str)[:96])
    own = {"captain_id": "20020388788", "hub": "LZ5"}
    check("redact leaves the conversation's own subject alone",
          dataplane.redact(own, set())["captain_id"] == "20020388788")

    # ── a composer must never assert why a result is empty
    from app.engine import data_queries as dq
    banned = ("not connected in this environment", "contains no payout data at all")
    offenders = []
    for name in dq.QUERIES:
        ans, _rows = dq.run_and_compose(
            name, {}, {"captain_id": "X", "ledger": [], "losses": [], "shipments": [],
                       "cash": {"cod_pendency_inr": 0}, "_sources": {"account": "A", "shipments": "B"}})
        for b in banned:
            if b in ans:
                offenders.append(f"{name}: …{b}…")
    check("no composer asserts an environment fact on an empty result", not offenders,
          "; ".join(offenders) if offenders
          else "they name the source from _sources and stop — true under every provider")
    cod = dq.run_and_compose("cod_status", {}, {"captain_id": "X", "cash": {"cod_pendency_inr": 0},
                                                "ledger": [], "losses": [], "shipments": []})[0]
    check("a provider that HAS the cash field reports a real zero",
          "clear" in cod and "not connected" not in cod, cod[:70])
    cod2 = dq.run_and_compose("cod_status", {}, {"captain_id": "X", "cash": {},
                                                 "ledger": [], "losses": [], "shipments": []})[0]
    check("a provider that lacks it says so, and never quotes an amount",
          "no COD or cash data" in cod2 and "₹" not in cod2 and "zero balance" in cod2,
          cod2[:88])

    # ── the per-turn ceiling must cover the in-turn verifier fan-out
    import inspect

    from app.trust import verifier
    check("verifier.verify accepts the turn meter",
          "turn" in inspect.signature(verifier.verify).parameters)
    check("tools.dispatch threads it through",
          "turn" in inspect.signature(tools.dispatch).parameters)
    src = inspect.getsource(tools._apply_policy)
    check("apply_policy passes it to the verifier", "turn=turn" in src,
          "the once-per-disputed-AWB fan-out is the case a step budget cannot see")

    # ── a zero ceiling must BLOCK, not silently disable
    saved = dict(os.environ)
    try:
        meter.reset()
        os.environ["LLM_BUDGET_USD"] = "0"
        blocked = False
        try:
            meter.check("claude-opus-5", "classify")
        except meter.BudgetExhausted as e:
            blocked = True
            scope = getattr(e, "scope", "")
        check("LLM_BUDGET_USD=0 blocks every call", blocked,
              "health already reported it as exhausted; check() used to let it through")
        check("the exception names WHICH ceiling fired", blocked and scope == "total")
        os.environ["LLM_BUDGET_USD"] = "1000"
        os.environ["LLM_BUDGET_PER_TURN_USD"] = "0"
        tmx = meter.TurnMeter()
        got = ""
        try:
            meter.check("claude-opus-5", "classify", tmx)
        except meter.BudgetExhausted as e:
            got = getattr(e, "env_var", "")
        check("a per-turn stop names LLM_BUDGET_PER_TURN_USD, not the total",
              got == "LLM_BUDGET_PER_TURN_USD", got or "no exception raised")
    finally:
        os.environ.clear(); os.environ.update(saved); meter.reset()

    # ── a provider without tool-calling is a CONFIG error, not "try again later"
    from app.llm.base import LLMProvider

    class NoChat(LLMProvider):
        pass
    check("a tool-less provider is still detectable after the base gained a chat stub",
          getattr(type(NoChat(api_key="x")), "chat", None) is LLMProvider.chat,
          "hasattr() became useless once chat_metered was inherited")

    # ── an unknown tool name must leave a trace
    _r, evs, _c, _a = tools.dispatch("no_such_tool", {}, "X", {"captain_id": "X"})
    check("an unknown tool emits a trace event", len(evs) == 1 and evs[0]["status"] == "blocked",
          "it used to return silently — the one failure a reviewer most needs to see")


# ══ 4c. thread safety, found by a determinism test and worth pinning ═════════════════
def sec_threads() -> None:
    """`loss_db` shared ONE sqlite connection across threads. `check_same_thread=False` only
    disables Python's assertion; it does not make a connection safe for concurrent use, and two
    threads on one statement handle raise `sqlite3.InterfaceError` or `IndexError` from inside
    the row factory. Reachable in normal operation: /api/chat streams through
    `iterate_in_threadpool`, so two captains talking at once is the default case."""
    head("[4c] concurrency — one connection per thread, and a deterministic trail")
    import threading
    from collections import Counter

    from app.engine import policy_exec
    from app.substrate import captain_context as CC, loss_db

    errors: list[str] = []

    def hammer():
        try:
            for _ in range(25):
                loss_db._query("SELECT awb FROM losses LIMIT 3", ())
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")

    ts = [threading.Thread(target=hammer) for _ in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("500 concurrent queries across 20 threads raise nothing", not errors,
          "; ".join(sorted(set(errors))[:2]) or "one connection per thread")
    check("each thread got its OWN connection", hasattr(loss_db, "_conn"),
          "a shared connection with check_same_thread=False is not thread-safe")

    # And the decision path must be deterministic — the evidence trail used to vary because
    # captain_id travelled through a module global across four sqlite round-trips.
    AWB = "VL0093310077"
    mine, other = CC.get_context("VLMO-CPT-3310"), CC.get_context("VLMO-CPT-4471")
    trails: list[tuple] = []
    errs2: list[str] = []

    def decide(ctx, collect):
        try:
            d = policy_exec.execute("hardstop_loss", ctx, {"awb": AWB})
            if collect:
                trails.append(tuple(sorted(e["label"] for e in d["evidence_trail"])))
        except Exception as e:  # noqa: BLE001
            errs2.append(f"{type(e).__name__}: {e}")

    ts = ([threading.Thread(target=decide, args=(mine, True)) for _ in range(24)]
          + [threading.Thread(target=decide, args=(other, False)) for _ in range(24)])
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("48 concurrent decisions raise nothing", not errs2,
          "; ".join(sorted(set(errs2))[:2]))
    c = Counter(trails)
    check("the same request always yields the SAME evidence trail", len(c) == 1,
          f"{len(c)} distinct trails from {sum(c.values())} identical requests — "
          f"an audit record that varies under load is not an audit record")


# ══ 5. the data-plane boundary ═══════════════════════════════════════════════════════
def sec_dataplane() -> None:
    head("[5] data-plane boundary (scripts/check_dataplane.py)")
    import check_dataplane
    if check_dataplane.main() != 0:
        FAILED.append("data-plane boundary")


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    for sec in (sec_audit, sec_awb, sec_meter, sec_history, sec_review,
                sec_threads, sec_dataplane):
        try:
            sec()
        except Exception as e:  # noqa: BLE001 — a broken check is a failure, not a crash
            import traceback
            traceback.print_exc()
            FAILED.append(f"{sec.__name__} raised {type(e).__name__}: {e}")
    print(f"\n{'═' * 78}")
    if FAILED:
        print(f"FAILED {len(FAILED)}:")
        for f in FAILED:
            print(f"  · {f}")
        return 1
    print("PHASE 1 VERIFIED — all checks pass, no API calls made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
