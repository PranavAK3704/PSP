"""Gemini provider — stands in for Claude for the demo.

Uses the REST API directly (no SDK dependency) so the demo runs anywhere with
`requests` installed. Mirrors the tiered strategy in BRD §10.
"""
from __future__ import annotations

import json
from typing import Optional

import requests

from .base import LLMProvider

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(LLMProvider):
    name = "gemini"

    def _generate(self, prompt: str, *, model: str, system: Optional[str], json_mode: bool):
        url = f"{_BASE}/{model}:generateContent?key={self.api_key}"
        gen_cfg = {"temperature": self.temperature}
        if json_mode:
            gen_cfg["responseMimeType"] = "application/json"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": gen_cfg,
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        resp = requests.post(url, json=body, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError):
            # finishReason without text (safety, recitation, etc.) — surface empty.
            text = ""

        um = data.get("usageMetadata", {})
        usage = {"input": um.get("promptTokenCount", 0), "output": um.get("candidatesTokenCount", 0)}
        return text, usage, data

    def chat(self, contents: list, *, model: str, system: Optional[str] = None,
             tools: Optional[list] = None):
        """One turn of a tool-using chat. Returns (model_content_dict, usage).

        `contents` is the running Gemini conversation array; the caller appends the
        returned model content + any functionResponse and calls again. This is the
        agent loop's primitive — provider-agnostic in shape (Claude maps 1:1)."""
        url = f"{_BASE}/{model}:generateContent?key={self.api_key}"
        body = {"contents": contents, "generationConfig": {"temperature": self.temperature}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"function_declarations": tools}]
        resp = requests.post(url, json=body, timeout=90)
        resp.raise_for_status()
        data = resp.json()
        try:
            content = data["candidates"][0]["content"]
        except (KeyError, IndexError):
            content = {"role": "model", "parts": [{"text": ""}]}
        um = data.get("usageMetadata", {})
        usage = {"input": um.get("promptTokenCount", 0), "output": um.get("candidatesTokenCount", 0)}
        return content, usage

    def generate_json(self, prompt: str, *, model: str, node: str, system: Optional[str] = None):
        """Convenience: generate in JSON mode and parse. Falls back to brace-extraction."""
        res = self.generate(prompt, model=model, node=node, system=system, json_mode=True)
        return _parse_json(res.text), res


def _parse_json(text: str):
    text = (text or "").strip()
    if not text:
        return {}
    # strip ```json fences if present
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().startswith("json"):
            text = text.lstrip()[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    return {}
