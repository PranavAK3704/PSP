"""LLM provider interface.

Every model call in the platform goes through this interface. The pipeline never
imports a vendor SDK directly — it asks the registry for a provider by node name.
That is what makes the Claude<->Gemini swap a config change (see registry.py).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from . import meter


@dataclass
class LLMResult:
    text: str
    model: str
    node: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0        # priced at list rate by llm/meter.py, not estimated
    raw: Any = field(default=None, repr=False)


class LLMProvider:
    """Base class. Concrete providers implement _generate()."""

    name: str = "base"

    def __init__(self, api_key: str, temperature: float = 0.1):
        self.api_key = api_key
        self.temperature = temperature

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        node: str,
        system: Optional[str] = None,
        json_mode: bool = False,
        turn: Optional["meter.TurnMeter"] = None,
    ) -> LLMResult:
        # ── the spend chokepoint ────────────────────────────────────────────────────
        # Every single-shot call in the platform passes through here — the compilers, the
        # verifier, the audit judge, the monitor. Metering at this one point covers all of
        # them without a single call site knowing the meter exists, and means a new call
        # site is metered by default rather than by remembering to. `check` raises
        # BudgetExhausted BEFORE spending; `add` bills the real token counts after.
        meter.check(model, node, turn)
        t0 = time.time()
        text, usage, raw = self._generate(prompt, model=model, system=system, json_mode=json_mode)
        cost = (turn.add(model, node, usage.get("input", 0), usage.get("output", 0)) if turn
                else meter.record(model, node, usage.get("input", 0), usage.get("output", 0)))
        return LLMResult(
            text=text,
            model=model,
            node=node,
            input_tokens=usage.get("input", 0),
            output_tokens=usage.get("output", 0),
            latency_ms=int((time.time() - t0) * 1000),
            cost_usd=round(cost, 6),
            raw=raw,
        )

    def chat_metered(self, contents: list, *, model: str, node: str = "classify",
                     system: Optional[str] = None, tools: Optional[list] = None,
                     turn: Optional["meter.TurnMeter"] = None):
        """`chat()` with the spend ceiling applied. THE conversation loop should call this.

        The tool-using loop is where cost actually accumulates — its history is resent in
        full on every step of every turn — yet `chat()` is implemented separately by each of
        the three providers. Rather than duplicate the meter into all three (where it would
        drift), it lives here once and delegates. Providers stay untouched; the guarantee is
        inherited.

        Returns (content, usage) exactly as `chat()` does, so it is a drop-in.
        """
        meter.check(model, node, turn)
        content, usage = self.chat(contents, model=model, system=system, tools=tools)
        usage = usage or {}
        if turn is not None:
            turn.add(model, node, usage.get("input", 0), usage.get("output", 0))
        else:
            meter.record(model, node, usage.get("input", 0), usage.get("output", 0))
        return content, usage

    def chat(self, contents: list, *, model, system=None, tools=None):  # pragma: no cover
        """Provider-specific tool-using turn. Prefer `chat_metered` — a direct call here
        bypasses the spend ceiling."""
        raise NotImplementedError

    def _generate(self, prompt, *, model, system, json_mode):  # pragma: no cover
        raise NotImplementedError
