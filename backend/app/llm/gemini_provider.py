"""Gemini provider — stands in for Claude for the demo.

Uses the REST API directly (no SDK dependency) so the demo runs anywhere with
`requests` installed. Mirrors the tiered strategy in BRD §10.
"""
from __future__ import annotations

import json
import time
from typing import Optional

import requests

from .base import LLMProvider

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# ── ONE retry, on transient status codes only ────────────────────────────────────────────────
# This provider had a bare `raise_for_status()` and no retry at all, unlike the OpenAI and
# Claude providers. That was survivable while Gemini was a demo stand-in. It is not survivable
# now that `adversarial_verify` routes here: `verifier.verify` fails CLOSED, which is correct
# for production — a skeptic that cannot be reached must never auto-approve money — but it means
# a single transient 429 converts a clean reversal into an escalation, and on a stage that reads
# as the verifier having DISAGREED.
#
# So: retry once, only on codes that are actually transient, and keep failing closed after it.
# Deliberately not a longer ladder — the verifier runs inside a turn the captain is waiting on,
# and a 30-second retry chain is its own kind of failure.
_RETRY_STATUS = (429, 500, 502, 503, 504)
_RETRY_SLEEP_S = 1.5


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

        resp = _post_with_retry(url, body)
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
        resp = _post_with_retry(url, body, timeout=90)
        data = resp.json()
        try:
            content = data["candidates"][0]["content"]
        except (KeyError, IndexError):
            content = {"role": "model", "parts": [{"text": ""}]}
        um = data.get("usageMetadata", {})
        usage = {"input": um.get("promptTokenCount", 0), "output": um.get("candidatesTokenCount", 0)}
        return content, usage


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


def _post_with_retry(url: str, body: dict, timeout: int = 60):
    """POST once, retry once on a transient status, then raise. See the note at the top.

    A non-transient status (400 bad request, 403 bad key) raises immediately — retrying a
    rejected request wastes the captain's time and tells the operator nothing new.
    """
    resp = requests.post(url, json=body, timeout=timeout)
    if resp.status_code in _RETRY_STATUS:
        time.sleep(_RETRY_SLEEP_S)
        resp = requests.post(url, json=body, timeout=timeout)
    resp.raise_for_status()
    return resp
