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
    """Return (provider, model_id) for a pipeline node.

    YAML is the default routing; ENV can override it so a provider/model swap is a DEPLOY-TIME
    change (a Render env var) rather than a code edit — which is what you need when the current
    endpoint dies and you must repoint at Groq / Ollama / the approved enterprise gateway:
        LLM_PROVIDER      openai | gemini | claude   (openai = any OpenAI-compatible endpoint)
        LLM_MODEL_FAST    model id for the fast tier
        LLM_MODEL_DEEP    model id for the deep tier
    With provider=openai, pair these with OPENAI_BASE_URL + OPENAI_API_KEY.
    """
    cfg = _config()
    provider_name = active_provider_name()
    tier = cfg["nodes"].get(node, "fast")
    env_model = os.environ.get(f"LLM_MODEL_{tier.upper()}", "").strip()
    if env_model:
        return _provider(provider_name), env_model
    model = cfg["tiers"][provider_name][tier]
    return _provider(provider_name), model


def active_provider_name() -> str:
    return (os.environ.get("LLM_PROVIDER", "").strip().lower() or _config()["provider"])


def active_model_label() -> str:
    """Provider/model of the deep tier, for stamping derived records with what produced them."""
    prov, model = for_node("policy_reasoning")
    return f"{prov.name}:{model}"


# Which env var holds the credential for each provider. Used ONLY to report presence.
_KEY_ENV = {"claude": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}


def key_configured() -> bool:
    """Is a credential present for the ACTIVE provider? Presence only — the value is never read
    out, logged, or returned.

    This exists because the failure it catches is otherwise invisible: with no key, the app boots,
    /api/health returns 200 and reports the right model, and only the first real turn fails — so a
    misconfigured deploy looks completely healthy. Surfacing it here turns a confusing runtime
    symptom ("the assistant is broken") into a config fact anyone can check.
    """
    return bool(os.environ.get(_KEY_ENV.get(active_provider_name(), ""), "").strip())
