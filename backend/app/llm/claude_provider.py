"""Claude provider — the production target (BRD §10).

Drop-in for the OpenAI/Gemini providers: flipping `provider: claude` in
config/models.yaml (plus an ANTHROPIC_API_KEY, baked at data/anthropic_key.txt or
set in the env) is the only change required — no pipeline code changes.

The engine speaks a Gemini-shaped `contents`/`parts` protocol (text / functionCall /
functionResponse). This provider translates that to/from Anthropic's Messages API
(text / tool_use / tool_result blocks), including synthesising the tool-use ids
Anthropic requires (Gemini has none), exactly like the OpenAI provider does.
"""
from __future__ import annotations

import json
import os
from typing import Optional

from .base import LLMProvider


class ClaudeProvider(LLMProvider):
    name = "claude"

    def _client(self):
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "Claude provider selected but the `anthropic` SDK is not installed. "
                "Run `pip install anthropic` (it is in requirements.txt) and set "
                "ANTHROPIC_API_KEY, or set provider: openai in config/models.yaml."
            ) from e
        kwargs = {"api_key": self.api_key}
        base = os.environ.get("ANTHROPIC_BASE_URL")
        if base:
            kwargs["base_url"] = base
        return anthropic.Anthropic(**kwargs)

    # ── single-shot completion ──────────────────────────────────────────────
    def _generate(self, prompt: str, *, model: str, system: Optional[str], json_mode: bool):
        client = self._client()
        user = prompt
        if json_mode:
            user = prompt + "\n\nRespond with ONLY valid JSON, no prose, no markdown fences."
        kwargs = dict(
            model=model,
            max_tokens=1500,
            temperature=self.temperature,
            messages=[{"role": "user", "content": user}],
        )
        if system:
            kwargs["system"] = system
        msg = client.messages.create(**kwargs)
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
        usage = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens}
        return text, usage, msg

    def generate_json(self, prompt: str, *, model: str, node: str, system: Optional[str] = None):
        from .gemini_provider import _parse_json  # shared, forgiving JSON extractor
        res = self.generate(prompt, model=model, node=node, system=system, json_mode=True)
        return _parse_json(res.text), res

    # ── tool-using agent loop ───────────────────────────────────────────────
    def chat(self, contents: list, *, model: str, system: Optional[str] = None,
             tools: Optional[list] = None):
        """One turn of the tool-using loop. Takes the engine's Gemini-shaped `contents`
        and returns (model_content_dict, usage) in the SAME Gemini shape so the caller
        can append it and continue — identical contract to the Gemini/OpenAI providers."""
        client = self._client()
        messages = _to_anthropic_messages(contents)
        kwargs = dict(model=model, max_tokens=1500, temperature=self.temperature, messages=messages)
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = [{
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
            } for t in tools]
            kwargs["tool_choice"] = {"type": "auto"}

        msg = client.messages.create(**kwargs)

        parts = []
        for block in msg.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                parts.append({"text": block.text})
            elif btype == "tool_use":
                parts.append({"functionCall": {"name": block.name, "args": dict(block.input or {})}})
        if not parts:
            parts.append({"text": ""})

        usage = {"input": msg.usage.input_tokens, "output": msg.usage.output_tokens}
        return {"role": "model", "parts": parts}, usage


def _to_anthropic_messages(contents: list) -> list:
    """Translate the engine's Gemini-shaped `contents` into Anthropic `messages`.

    Gemini carries no tool_use id; Anthropic requires each tool_result to reference
    the id of the tool_use it answers. The engine always appends a model turn with N
    functionCalls immediately followed by a user turn with N functionResponses in the
    same order, so we mint deterministic ids on the model turn and pair them in order
    on the following turn (same scheme as the OpenAI provider)."""
    messages: list = []
    pending_ids: list = []
    counter = 0

    for content in contents:
        role = content.get("role")
        parts = content.get("parts", [])
        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
        function_responses = [p["functionResponse"] for p in parts if "functionResponse" in p]
        texts = [p["text"] for p in parts if "text" in p]

        if role == "model":
            blocks = []
            text = "".join(texts)
            if text:
                blocks.append({"type": "text", "text": text})
            pending_ids = []
            for fc in function_calls:
                cid = f"toolu_{counter}"
                counter += 1
                pending_ids.append(cid)
                blocks.append({
                    "type": "tool_use", "id": cid,
                    "name": fc.get("name", ""), "input": fc.get("args", {}) or {},
                })
            messages.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
        else:
            if function_responses:
                blocks = []
                for i, fr in enumerate(function_responses):
                    cid = pending_ids[i] if i < len(pending_ids) else f"toolu_{counter}"
                    counter += 1
                    blocks.append({
                        "type": "tool_result", "tool_use_id": cid,
                        "content": json.dumps(fr.get("response", {})),
                    })
                messages.append({"role": "user", "content": blocks})
                pending_ids = []
            else:
                messages.append({"role": "user", "content": "".join(texts)})

    return messages
