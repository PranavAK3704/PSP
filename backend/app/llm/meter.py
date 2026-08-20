"""LLM spend meter — a hard dollar ceiling, enforced at the one place every call passes.

WHY A DOLLAR BOUND AND NOT A CALL COUNT
A step budget bounds the wrong thing. Two shapes drain a credit without exceeding any step
limit: a conversation whose tool results have grown until each of six steps carries 20k
tokens of history, and the adversarial-verifier fan-out that fires once per disputed AWB in
a single turn. Both are legal under `MAX_STEPS`; neither is legal under a dollar ceiling.
Money is what actually runs out, so money is what is counted.

WHERE IT IS ENFORCED
`LLMProvider.generate()` in base.py, and each provider's `chat()`. Those two are the whole
surface — every one of the nine call sites in the codebase goes through one of them — so no
call site needs to know this module exists.

TWO CEILINGS, BOTH REQUIRED
  LLM_BUDGET_USD           total for the deployment  (default 45, leaving a $5 reserve of $50)
  LLM_BUDGET_PER_TURN_USD  one conversation turn      (default 0.50)
The total stops a slow drain across many sessions; the per-turn stops a single runaway loop
or fan-out inside one turn, which the total would only notice after it had already happened.

FAILING SAFE MEANS PRICING HIGH
An unknown model id is priced at the OPUS rate, never at zero. The failure mode of guessing
low is silent overspend, which is the exact thing this file exists to prevent; the failure
mode of guessing high is an early stop with a clear message. Env-var model overrides
(LLM_MODEL_DEEP=…) mean unknown ids are a routine occurrence, not a hypothetical.

NO contextvars, NO threading.local. `sse_starlette` drives the generator through
`iterate_in_threadpool`, so successive `__next__` calls can land on DIFFERENT threads and a
thread-local turn counter would silently reset mid-turn. Per-turn accounting is therefore an
explicit object the caller holds (see `TurnMeter`), passed down rather than discovered.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone

from ..durable_state import durable_path

_LEDGER = durable_path("llm_spend.json")
_lock = threading.Lock()

# ── prices, USD per 1M tokens (list price, input/output) ─────────────────────────────
# Sonnet 5's $2/$10 is an INTRODUCTORY rate that expires 2026-08-31. Pricing on the intro
# rate would silently under-count from September onwards, and a budget that under-counts is
# not a budget — so the list price is used and the intro rate is a note, not a number.
_PRICES = {
    "claude-opus-5":      (5.00, 25.00),
    "claude-opus-4-8":    (5.00, 25.00),
    "claude-opus-4-7":    (5.00, 25.00),
    "claude-opus-4-6":    (5.00, 25.00),
    "claude-fable-5":     (10.00, 50.00),
    "claude-mythos-5":    (10.00, 50.00),
    "claude-sonnet-5":    (3.00, 15.00),
    "claude-sonnet-4-6":  (3.00, 15.00),
    "claude-haiku-4-5":   (1.00, 5.00),
    # non-Anthropic providers the registry can route to
    "gemini-2.5-pro":     (1.25, 10.00),
    "gemini-2.5-flash":   (0.30, 2.50),
    "gpt-4o":             (2.50, 10.00),
    "gpt-4o-mini":        (0.15, 0.60),
}
_UNKNOWN_PRICE = (5.00, 25.00)   # the Opus rate — see the module note on failing safe


class BudgetExhausted(RuntimeError):
    """Raised INSTEAD of making a call that would exceed a ceiling.

    A RuntimeError subclass on purpose: conversation.py already catches broad exceptions
    around provider calls and classifies them for the captain, so this surfaces through the
    existing path rather than needing a new one.
    """


def price_of(model: str) -> tuple[float, float]:
    """(input, output) USD per 1M tokens. Longest-prefix match, so a dated id
    (`claude-haiku-4-5-20251001`) prices as its family rather than as unknown."""
    m = (model or "").strip().lower()
    if m in _PRICES:
        return _PRICES[m]
    best = ""
    for known in _PRICES:
        if m.startswith(known) and len(known) > len(best):
            best = known
    return _PRICES[best] if best else _UNKNOWN_PRICE


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = price_of(model)
    return (max(0, int(input_tokens or 0)) * pin
            + max(0, int(output_tokens or 0)) * pout) / 1_000_000


def _budget(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        v = float(raw) if raw else default
    except ValueError:
        v = default
    return max(0.0, v)


def total_budget() -> float:
    return _budget("LLM_BUDGET_USD", 45.0)


def per_turn_budget() -> float:
    return _budget("LLM_BUDGET_PER_TURN_USD", 0.50)


# ── the ledger ───────────────────────────────────────────────────────────────────────
# Durable (Turso-mirrored) so the running total survives a Render redeploy. Without that
# the ceiling resets on every deploy, which on a free tier that restarts when idle means it
# is not a ceiling at all.

def _load() -> dict:
    try:
        if _LEDGER.exists():
            d = json.loads(_LEDGER.read_text())
            if isinstance(d, dict):
                d.setdefault("spent_usd", 0.0)
                d.setdefault("calls", 0)
                d.setdefault("by_node", {})
                d.setdefault("by_model", {})
                return d
    except Exception:  # noqa: BLE001 — a corrupt ledger must not wedge every LLM call
        pass
    return {"spent_usd": 0.0, "calls": 0, "by_node": {}, "by_model": {},
            "tokens_in": 0, "tokens_out": 0, "since": _now()}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def spent() -> float:
    return float(_load().get("spent_usd") or 0.0)


def remaining() -> float:
    return max(0.0, total_budget() - spent())


def totals() -> dict:
    """The full ledger + derived headroom, for /api/health and the cost panel."""
    d = _load()
    tot = total_budget()
    sp = float(d.get("spent_usd") or 0.0)
    return {
        "spent_usd": round(sp, 4),
        "budget_usd": tot,
        "remaining_usd": round(max(0.0, tot - sp), 4),
        "pct_used": round(100.0 * sp / tot, 1) if tot else 100.0,   # a 0 ceiling is 100% used
        "calls": int(d.get("calls") or 0),
        "tokens_in": int(d.get("tokens_in") or 0),
        "tokens_out": int(d.get("tokens_out") or 0),
        "by_node": d.get("by_node") or {},
        "by_model": d.get("by_model") or {},
        "per_turn_budget_usd": per_turn_budget(),
        "since": d.get("since", ""),
    }


def record(model: str, node: str, input_tokens: int, output_tokens: int) -> float:
    """Bill one completed call. Returns its cost. Never raises."""
    c = cost_usd(model, input_tokens, output_tokens)
    try:
        # The read-modify-update is under the lock (it must be, or concurrent calls lose
        # increments); the WRITE is not. durable_state mirrors to Turso over HTTPS, so
        # holding the lock across it would serialise every LLM call in the process behind a
        # network round-trip. A lost write costs an under-counted call, which the next write
        # supersedes; a serialised lock costs every concurrent turn.
        with _lock:
            d = _load()
            d["spent_usd"] = round(float(d.get("spent_usd") or 0.0) + c, 6)
            d["calls"] = int(d.get("calls") or 0) + 1
            d["tokens_in"] = int(d.get("tokens_in") or 0) + max(0, int(input_tokens or 0))
            d["tokens_out"] = int(d.get("tokens_out") or 0) + max(0, int(output_tokens or 0))
            d["by_node"][node or "?"] = round(float(d["by_node"].get(node or "?", 0.0)) + c, 6)
            d["by_model"][model or "?"] = round(float(d["by_model"].get(model or "?", 0.0)) + c, 6)
            d["updated_at"] = _now()
            payload = json.dumps(d, indent=1)
        _LEDGER.write_text(payload)
    except Exception:  # noqa: BLE001 — a ledger-write failure must not fail the call
        pass
    return c


def check(model: str, node: str = "", turn: "TurnMeter | None" = None) -> None:
    """Raise BudgetExhausted if this call must not be made. Called BEFORE the call.

    The check is against what has ALREADY been spent, not against a prediction of this
    call's cost: predicting requires knowing the output length, which is exactly what is
    unknown beforehand. One call's worth of overshoot past the ceiling is the price of not
    guessing, and it is why the defaults leave a $5 reserve.
    """
    if turn is not None:
        cap = per_turn_budget()
        # `>=` with no `if cap` guard: a ceiling of 0 means ZERO SPEND ALLOWED, which is a
        # legitimate setting (a locked-down deploy) and is exactly what /api/health already
        # reports for it. Treating 0 as falsy made it silently disable the ceiling instead —
        # health said "exhausted, chat will refuse" while chat happily spent.
        if turn.spent >= cap:
            e = BudgetExhausted(
                f"per-turn LLM budget reached: ${turn.spent:.3f} of ${cap:.2f} spent in this "
                f"turn across {turn.calls} call(s). Raise LLM_BUDGET_PER_TURN_USD to continue.")
            e.scope = "per_turn"        # so the caller names the RIGHT env var to the operator
            e.env_var = "LLM_BUDGET_PER_TURN_USD"
            raise e
    tot = total_budget()
    if spent() >= tot:
        e = BudgetExhausted(
            f"LLM budget exhausted: ${spent():.2f} of ${tot:.2f} spent. Nothing is broken — "
            f"raise LLM_BUDGET_USD (or reset the ledger) to continue.")
        e.scope = "total"
        e.env_var = "LLM_BUDGET_USD"
        raise e


class TurnMeter:
    """Per-turn accounting, held explicitly by the caller.

    Explicit rather than ambient because ambient would not work here: the SSE layer iterates
    the turn generator across threads, so a thread-local would reset mid-turn and a
    contextvar would not propagate. `_run_turn` owns one of these and threads it into the
    provider calls, which is also what fixes conversation.py discarding `usage` on the chat
    call — the meter needs that number, so it can no longer be dropped.
    """

    __slots__ = ("spent", "calls", "tokens_in", "tokens_out", "by_node")

    def __init__(self):
        self.spent = 0.0
        self.calls = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.by_node: dict[str, float] = {}

    def add(self, model: str, node: str, input_tokens: int, output_tokens: int) -> float:
        c = record(model, node, input_tokens, output_tokens)
        self.spent += c
        self.calls += 1
        self.tokens_in += max(0, int(input_tokens or 0))
        self.tokens_out += max(0, int(output_tokens or 0))
        self.by_node[node or "?"] = round(self.by_node.get(node or "?", 0.0) + c, 6)
        return c

    def summary(self) -> dict:
        return {"cost_usd": round(self.spent, 5), "calls": self.calls,
                "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
                "by_node": self.by_node,
                "budget_remaining_usd": round(remaining(), 3)}


def reset() -> dict:
    """Zero the ledger. For the offline harness and for a deliberate budget reset."""
    with _lock:
        d = {"spent_usd": 0.0, "calls": 0, "by_node": {}, "by_model": {},
             "tokens_in": 0, "tokens_out": 0, "since": _now()}
        _LEDGER.write_text(json.dumps(d, indent=1))
    return d
