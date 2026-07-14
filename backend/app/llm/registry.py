"""Task -> provider/model resolution (BRD §10, §15.2 adaptability).

The registry reads config/models.yaml once and answers: "for pipeline node X,
which provider and which concrete model?" Routing is data, not code — swapping
Claude for Gemini, or changing a node's tier, is a YAML edit.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

from .base import LLMProvider
from .claude_provider import ClaudeProvider
from .gemini_provider import GeminiProvider
from .openai_provider import OpenAIProvider

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


@lru_cache(maxsize=1)
def _config() -> dict:
    with open(_CONFIG) as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=4)
def _provider(name: str) -> LLMProvider:
    cfg = _config()
    temp = cfg.get("temperature", 0.1)
    if name == "gemini":
        key = os.environ.get("GEMINI_API_KEY", "")
        return GeminiProvider(api_key=key, temperature=temp)
    if name == "claude":
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        return ClaudeProvider(api_key=key, temperature=temp)
    if name == "openai":
        key = os.environ.get("OPENAI_API_KEY", "")
        return OpenAIProvider(api_key=key, temperature=temp)
    raise ValueError(f"Unknown provider: {name}")


def for_node(node: str) -> tuple[LLMProvider, str]:
    """Return (provider, model_id) for a pipeline node."""
    cfg = _config()
    provider_name = cfg["provider"]
    tier = cfg["nodes"].get(node, "fast")
    model = cfg["tiers"][provider_name][tier]
    return _provider(provider_name), model


def active_provider_name() -> str:
    return _config()["provider"]
