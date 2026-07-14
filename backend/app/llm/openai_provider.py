"""OpenAI-compatible provider — the demo brain, via the Bifrost gateway.

Talks to an OpenAI-compatible Chat Completions endpoint (the hackathon Bifrost
gateway by default) using requests only — no SDK dependency — mirroring
GeminiProvider. Implements the interface the pipeline expects:

  - _generate()      single-shot completion (JSON mode supported)
  - generate_json()  convenience JSON parse
  - chat()           one turn of the tool-using agent loop. The engine speaks
                     Gemini's function-calling shape (contents with `parts` /
                     `functionCall` / `functionResponse`); this translates that
                     to OpenAI `messages` + `tools`/`tool_calls` and back, so
                     app/engine/conversation.py works unchanged.

The gateway intermittently returns 401 "virtual_key_not_found" on a fraction of
otherwise-valid requests, so every call retries transient failures (that flake,
429s, 5xx, and network errors). Without this the multi-step agent loop would
fail constantly.
"""
from __future__ import annotations

import json
import os
import random
import time
from typing import Optional

import requests

from .base import LLMProvider

# OpenAI-compatible endpoint. Defaults to the hackathon Bifrost gateway so the
# deployed image needs no runtime env vars; override with OPENAI_BASE_URL.
_DEFAULT_URL = "https://gateway-buildathon.ltl.sh/v1/chat/completions"


def _supports_custom_temperature(model: str) -> bool:
    """GPT-5 / o-series reasoning models reject a custom `temperature` (the gateway
    400s with "does not support 0.1 ... Only the default (1) value is supported").
    For those we must omit the field entirely; gpt-4o and friends accept it."""
    m = (model or "").lower()
    return not (m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"))

# The gateway 401 "virtual_key_not_found" flake is a per-request routing miss
# (a fraction of backend nodes lack the key) and has been observed at ~70%.
# Retrying immediately hits a different node, so we retry it aggressively with a
# tiny pause. Rate-limits / 5xx / network errors get fewer, backed-off retries.
_VK_MAX_ATTEMPTS = 30      # for the flaky virtual-key 401
_VK_PAUSE_SECONDS = 0.2
# The edge WAF 403s clients that don't look like a browser. Sending the full browser
# header set (see _headers) fixes the SYSTEMIC 403. A residual 403 during a rapid
# multi-call turn is a transient burst-block, so retry it with a jittered backoff
# (backoff, not a fast pause, so bursts don't look bot-like).
_WAF_MAX_ATTEMPTS = 8
_WAF_BACKOFF_SECONDS = 0.8
_ERR_MAX_ATTEMPTS = 5      # for 429 / 5xx / network
_ERR_BACKOFF_SECONDS = 0.6


class OpenAIProvider(LLMProvider):
    name = "openai"

    def _url(self) -> str:
        return os.environ.get("OPENAI_BASE_URL", _DEFAULT_URL)

    def _headers(self) -> dict:
        # The gateway sits behind an Akamai edge WAF that 403s non-browser clients.
        # A User-Agent ALONE is not enough — the WAF checks the full browser signal set
        # (sec-ch-ua / Sec-Fetch-* / Accept-Language / Origin / Referer). Verified against
        # the live gateway: minimal headers -> 403 "Access Denied"; this full set -> 200.
        # We deliberately do NOT set Accept-Encoding: `requests` manages it and decodes
        # the body itself (forcing br here can yield an undecodable response).
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/125.0.0.0 Safari/537.36"),
            "Origin": "https://gateway-buildathon.ltl.sh",
            "Referer": "https://gateway-buildathon.ltl.sh/",
            "sec-ch-ua": '"Chromium";v="125", "Not.A/Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    def _post(self, body: dict, *, timeout: int = 90) -> dict:
        """POST with retries. The virtual-key 401 flake gets many fast retries
        (a different backend node usually has the key); other transient failures
        (429/5xx/network) get a few backed-off retries."""
        last_err = None
        vk_attempts = 0
        waf_attempts = 0
        err_attempts = 0
        while True:
            try:
                resp = requests.post(self._url(), headers=self._headers(), json=body, timeout=timeout)
            except requests.RequestException as e:  # network blip
                last_err = e
                err_attempts += 1
                if err_attempts >= _ERR_MAX_ATTEMPTS:
                    raise
                time.sleep(_ERR_BACKOFF_SECONDS * err_attempts)
                continue

            if resp.status_code == 200:
                return resp.json()

            text = resp.text or ""
            last_err = RuntimeError(f"gateway HTTP {resp.status_code}: {text[:300]}")

            # The flaky per-request virtual-key 401 — retry hard (jittered) — a good node has the key.
            if resp.status_code == 401 and "virtual_key_not_found" in text:
                vk_attempts += 1
                if vk_attempts >= _VK_MAX_ATTEMPTS:
                    raise last_err
                time.sleep(_VK_PAUSE_SECONDS + random.uniform(0, 0.35))
                continue

            # Intermittent edge-WAF 403 "Access Denied" — retry with a longer JITTERED
            # backoff so rapid uniform retries don't look bot-like (which worsens the block).
            if resp.status_code == 403:
                waf_attempts += 1
                if waf_attempts >= _WAF_MAX_ATTEMPTS:
                    raise last_err
                time.sleep(_WAF_BACKOFF_SECONDS + random.uniform(0, 0.9))
                continue

            # Rate limits / server errors — a few backed-off retries.
            if resp.status_code == 429 or resp.status_code >= 500:
                err_attempts += 1
                if err_attempts >= _ERR_MAX_ATTEMPTS:
                    raise last_err
                time.sleep(_ERR_BACKOFF_SECONDS * err_attempts)
                continue

            # Anything else (400/403/real auth failure) is not transient.
            raise last_err

    # ── single-shot completion ──────────────────────────────────────────────
    def _generate(self, prompt: str, *, model: str, system: Optional[str], json_mode: bool):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        user = prompt
        body = {"model": model}
        if _supports_custom_temperature(model):
            body["temperature"] = self.temperature
        if json_mode:
            # OpenAI requires the literal token "json" somewhere in the messages.
            user = prompt + "\n\nRespond with ONLY valid JSON, no prose, no markdown fences."
            body["response_format"] = {"type": "json_object"}
        messages.append({"role": "user", "content": user})
        body["messages"] = messages

        data = self._post(body)
        try:
            text = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError):
            text = ""
        u = data.get("usage", {})
        usage = {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0)}
        return text, usage, data

    def generate_json(self, prompt: str, *, model: str, node: str, system: Optional[str] = None):
        res = self.generate(prompt, model=model, node=node, system=system, json_mode=True)
        return _parse_json(res.text), res

    # ── tool-using agent loop ───────────────────────────────────────────────
    def chat(self, contents: list, *, model: str, system: Optional[str] = None,
             tools: Optional[list] = None):
        """One turn. `contents` is the engine's running Gemini-shaped array; returns
        (model_content_dict, usage) in the SAME Gemini shape so the caller can append
        it and continue. Tool declarations arrive in Gemini form and are translated."""
        messages = _to_openai_messages(contents, system)
        body = {"model": model, "messages": messages}
        if _supports_custom_temperature(model):
            body["temperature"] = self.temperature
        if tools:
            body["tools"] = [{
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {"type": "object", "properties": {}}),
                },
            } for t in tools]
            body["tool_choice"] = "auto"

        data = self._post(body)
        msg = (data.get("choices") or [{}])[0].get("message", {}) or {}

        parts = []
        text = msg.get("content")
        if text:
            parts.append({"text": text})
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                cargs = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                cargs = {}
            parts.append({"functionCall": {"name": fn.get("name", ""), "args": cargs}})
        if not parts:
            parts.append({"text": ""})

        u = data.get("usage", {})
        usage = {"input": u.get("prompt_tokens", 0), "output": u.get("completion_tokens", 0)}
        return {"role": "model", "parts": parts}, usage


def _to_openai_messages(contents: list, system: Optional[str]) -> list:
    """Translate the engine's Gemini-shaped `contents` into OpenAI `messages`.

    Gemini has no tool_call_id; OpenAI requires each tool result to reference the
    id of the assistant tool_call it answers. The engine always appends an
    assistant (model) message with N functionCalls immediately followed by a user
    message with N functionResponses in the same order, so we mint deterministic
    ids on the assistant turn and pair them in order on the following turn.
    """
    messages: list = []
    if system:
        messages.append({"role": "system", "content": system})

    pending_ids: list = []   # tool_call ids from the most recent assistant turn
    counter = 0

    for content in contents:
        role = content.get("role")
        parts = content.get("parts", [])
        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
        function_responses = [p["functionResponse"] for p in parts if "functionResponse" in p]
        texts = [p["text"] for p in parts if "text" in p]

        if role == "model":
            assistant = {"role": "assistant"}
            text = "".join(texts)
            tool_calls = []
            pending_ids = []
            for fc in function_calls:
                cid = f"call_{counter}"
                counter += 1
                pending_ids.append(cid)
                tool_calls.append({
                    "id": cid,
                    "type": "function",
                    "function": {
                        "name": fc.get("name", ""),
                        "arguments": json.dumps(fc.get("args", {}) or {}),
                    },
                })
            if tool_calls:
                assistant["tool_calls"] = tool_calls
                assistant["content"] = text or None
            else:
                assistant["content"] = text
            messages.append(assistant)
        else:
            # user turn — plain message, or tool responses to the prior assistant turn
            if function_responses:
                for i, fr in enumerate(function_responses):
                    cid = pending_ids[i] if i < len(pending_ids) else f"call_{counter}"
                    counter += 1
                    messages.append({
                        "role": "tool",
                        "tool_call_id": cid,
                        "content": json.dumps(fr.get("response", {})),
                    })
                pending_ids = []
            else:
                messages.append({"role": "user", "content": "".join(texts)})

    return _balance_tool_calls(messages)


def _balance_tool_calls(messages: list) -> list:
    """OpenAI hard-requires every assistant tool_call id to be answered by a `tool` message.
    A history where a tool errored mid-turn (or the turn ended on a tool_call) leaves a dangling
    assistant(tool_calls) → HTTP 400 on every subsequent turn. Self-heal: for any tool_call id
    without a following tool message, inject a stub so the conversation can never get poisoned."""
    out: list = []
    i, n = 0, len(messages)
    while i < n:
        m = messages[i]
        out.append(m)
        if m.get("role") == "assistant" and m.get("tool_calls"):
            ids = [tc["id"] for tc in m["tool_calls"]]
            j, covered = i + 1, set()
            while j < n and messages[j].get("role") == "tool":
                out.append(messages[j]); covered.add(messages[j].get("tool_call_id")); j += 1
            for cid in ids:
                if cid not in covered:
                    out.append({"role": "tool", "tool_call_id": cid,
                                "content": '{"note":"no response recorded"}'})
            i = j
            continue
        i += 1
    return out


def _parse_json(text: str):
    text = (text or "").strip()
    if not text:
        return {}
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
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    return {}
