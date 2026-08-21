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

# Providers this registry can construct. Used to validate an override rather than trusting it:
# an unknown name in one node's env var falls back to the global provider instead of raising
# `Unknown provider` on the first call, which would take down whichever node was overridden.
_KNOWN_PROVIDERS = ("claude", "gemini", "openai")


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


def _env_node(node: str) -> str:
    """LLM_PROVIDER_ADVERSARIAL_VERIFY for node `adversarial_verify`."""
    return "LLM_PROVIDER_" + (node or "").strip().upper().replace("-", "_")


def provider_for_node(node: str) -> str:
    """Which PROVIDER serves this node. Per-node, which `active_provider_name()` is not.

    WHY THIS EXISTS
    `models.yaml` routes every node to one provider, and both `policy_reasoning` and
    `adversarial_verify` resolve to the same `deep` tier — so the skeptic that checks a money
    decision was the same model, from the same vendor, that the rest of the pipeline runs on. A
    verifier sharing the proposer's weights shares its blind spots, which makes it a second
    opinion in form only.

    Before this, the finest granularity available from the environment was per-TIER model id
    within a single provider (`LLM_MODEL_DEEP`), which retargets three nodes at once and cannot
    cross vendors at all.

    Resolution order, most specific first:
        LLM_PROVIDER_<NODE>   env, per node        e.g. LLM_PROVIDER_ADVERSARIAL_VERIFY=gemini
        node_providers:       models.yaml, per node
        LLM_PROVIDER          env, global
        provider:             models.yaml, global
    An unknown name falls through to the global provider rather than raising — a typo in one
    node's override must not take the whole engine down.
    """
    env = os.environ.get(_env_node(node), "").strip().lower()
    if env in _KNOWN_PROVIDERS:
        # An EXPLICIT env override is honoured even without a key. Someone who names a provider
        # at deploy time means it, and silently ignoring them would hide a broken deploy.
        return env
    cfg = _config()
    yaml_node = str((cfg.get("node_providers") or {}).get(node, "")).strip().lower()
    if yaml_node in _KNOWN_PROVIDERS:
        # A YAML default, however, must not break a working deployment. If the provider it names
        # has NO credential, fall back to the global one and let `routing()` say so.
        #
        # This matters most for `adversarial_verify`: it fails CLOSED, so routing it at a
        # keyless provider turns every money decision into an escalation — and the escalation
        # reads as the skeptic disagreeing. Degrading to the global provider keeps the engine
        # working; `independence_status()` and the verify event's `independent` flag report that
        # the second opinion is NOT actually independent, so nothing claims what it lacks.
        if key_configured(yaml_node):
            return yaml_node
    return active_provider_name()


def for_node(node: str) -> tuple[LLMProvider, str]:
    """Return (provider, model_id) for a pipeline node.

    YAML is the default routing; ENV can override it so a provider/model swap is a DEPLOY-TIME
    change (a Render env var) rather than a code edit — which is what you need when the current
    endpoint dies and you must repoint at Groq / Ollama / the approved enterprise gateway:
        LLM_PROVIDER            openai | gemini | claude   (openai = any OpenAI-compatible endpoint)
        LLM_PROVIDER_<NODE>     per-node provider override (see provider_for_node)
        LLM_MODEL_FAST          model id for the fast tier
        LLM_MODEL_DEEP          model id for the deep tier
        LLM_MODEL_<NODE>        per-node model override, wins over the tier vars
    With provider=openai, pair these with OPENAI_BASE_URL + OPENAI_API_KEY.
    """
    cfg = _config()
    provider_name = provider_for_node(node)
    tier = cfg["nodes"].get(node, "fast")
    # Per-node model beats per-tier model: a node routed to a different PROVIDER almost always
    # needs a different model id too, and LLM_MODEL_DEEP would hand it one from the wrong vendor.
    env_model = (os.environ.get("LLM_MODEL_" + (node or "").strip().upper(), "").strip()
                 or os.environ.get(f"LLM_MODEL_{tier.upper()}", "").strip())
    if env_model:
        return _provider(provider_name), env_model
    # A per-node YAML model, PROVIDER-SCOPED. It has to be: if a node's provider degrades (its
    # credential is absent) an unscoped model id would follow it across the vendor boundary and
    # produce `claude:gemini-2.5-pro` — a Claude client asking for a Gemini model, which fails
    # at request time with a confusing 404 rather than at config time.
    node_model = str(((cfg.get("node_models") or {}).get(node) or {}).get(provider_name, "")).strip()
    if node_model:
        return _provider(provider_name), node_model
    model = cfg["tiers"][provider_name][tier]
    return _provider(provider_name), model


def label_for_node(node: str) -> str:
    """"gemini:gemini-2.5-pro" — for stamping a record with what produced it."""
    prov, model = for_node(node)
    return f"{prov.name}:{model}"


def active_provider_name() -> str:
    return (os.environ.get("LLM_PROVIDER", "").strip().lower() or _config()["provider"])


def active_model_label() -> str:
    """Provider/model of the deep tier, for stamping derived records with what produced them."""
    prov, model = for_node("policy_reasoning")
    return f"{prov.name}:{model}"


# Which env var holds the credential for each provider. Used ONLY to report presence.
_KEY_ENV = {"claude": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}


def key_configured(provider_name: str | None = None) -> bool:
    """Is a credential present for a provider (default: the active one)? Presence only — the
    value is never read out, logged, or returned.

    This exists because the failure it catches is otherwise invisible: with no key, the app boots,
    /api/health returns 200 and reports the right model, and only the first real turn fails — so a
    misconfigured deploy looks completely healthy. Surfacing it here turns a confusing runtime
    symptom ("the assistant is broken") into a config fact anyone can check.

    Takes a provider argument now because a per-node override introduces a SECOND credential
    that can be missing independently. Without this, a deploy with a valid Anthropic key and no
    Gemini key would look healthy right up until the first money decision, at which point the
    verifier fails closed and every reversal turns into an escalation — a failure that reads as
    the skeptic disagreeing rather than as a missing config value.
    """
    return bool(os.environ.get(
        _KEY_ENV.get(provider_name or active_provider_name(), ""), "").strip())


def independence_status() -> dict:
    """Is the adversarial verifier genuinely a different vendor from the conversation model?

    Reported rather than assumed, because a verifier that shares the proposer's weights shares
    its blind spots — and a config that ASKS for independence while silently not getting it is
    worse than one that never asked.
    """
    node = "adversarial_verify"
    want = str((_config().get("node_providers") or {}).get(node, "")).strip().lower()
    got = provider_for_node(node)
    proposer = provider_for_node("classify")
    return {
        "requested": want or None,
        "active": got,
        "proposer": proposer,
        "independent": got != proposer,
        "degraded": bool(want) and want != got,
        "reason": (f"models.yaml routes {node} to '{want}' but no {_KEY_ENV.get(want, 'API key')} "
                   f"is set, so it fell back to '{got}' — the verifier is NOT independent. "
                   f"Set {_KEY_ENV.get(want, 'the key')} to activate it."
                   if want and want != got else
                   f"{node} runs on '{got}'; the conversation runs on '{proposer}'."),
    }


def routing() -> dict:
    """Every node's resolved provider:model + whether its credential is present.

    For /api/health and the connector panel. Reports the per-node picture rather than one
    global label, which is the only way a cross-vendor split is visible before it matters.
    """
    cfg = _config()
    out = {}
    for node in (cfg.get("nodes") or {}):
        pname = provider_for_node(node)
        try:
            _p, model = for_node(node)
        except Exception:  # noqa: BLE001 — an unresolvable node must not break health
            model = "?"
        out[node] = {"provider": pname, "model": model, "tier": cfg["nodes"].get(node),
                     "key_configured": key_configured(pname),
                     "overridden": pname != active_provider_name()}
    return out
