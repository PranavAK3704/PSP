"""LLM provider interface.

Every model call in the platform goes through this interface. The pipeline never
imports a vendor SDK directly — it asks the registry for a provider by node name.
That is what makes the Claude<->Gemini swap a config change (see registry.py).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class LLMResult:
    text: str
    model: str
    node: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
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
    ) -> LLMResult:
        t0 = time.time()
        text, usage, raw = self._generate(prompt, model=model, system=system, json_mode=json_mode)
        return LLMResult(
            text=text,
            model=model,
            node=node,
            input_tokens=usage.get("input", 0),
            output_tokens=usage.get("output", 0),
            latency_ms=int((time.time() - t0) * 1000),
            raw=raw,
        )

    def _generate(self, prompt, *, model, system, json_mode):  # pragma: no cover
        raise NotImplementedError
