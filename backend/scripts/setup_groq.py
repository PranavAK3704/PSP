"""One-step Groq provisioning — TEMPORARY bridge while the approved LLM path is procured.

    1. paste your key into backend/data/groq_key.txt   (gitignored)
    2. cd backend && python scripts/setup_groq.py
    3. . scripts/load_env.sh   ->  every LLM call now goes to Groq

Groq is OpenAI-API-compatible, so this needs NO provider code: it reuses the openai provider with
OPENAI_BASE_URL pointed at Groq. This script does the parts that are easy to get wrong:

  • Model ids CHANGE. Rather than hardcoding a guess, it asks Groq which models the key can
    actually use and picks the best available from a preference order, then records the choice in
    data/groq_model.txt so load_env.sh and every batch run agree.
  • It verifies JSON MODE actually works on that model — the audit judge depends on strict JSON,
    and not every hosted model supports response_format. A model that fails here is unusable for
    auditing even if it chats fine.
  • It reports the free-tier rate limit so batch concurrency can be set sanely instead of
    discovering it as a wall of 429s halfway through a 700-ticket run.

Nothing here prints the key.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

DATA = Path(__file__).resolve().parents[1] / "data"
KEY_FILE = DATA / "groq_key.txt"
MODEL_FILE = DATA / "groq_model.txt"
BASE = "https://api.groq.com/openai/v1"
CHAT_URL = f"{BASE}/chat/completions"

# Preference order: strongest general-reasoning open models first. The script only picks one that
# the key can actually see, so a retired id here is skipped rather than breaking the run.
PREFERRED = [
    "openai/gpt-oss-120b",
    "moonshotai/kimi-k2-instruct",
    "llama-3.3-70b-versatile",
    "deepseek-r1-distill-llama-70b",
    "qwen/qwen3-32b",
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instant",
]


def _key() -> str:
    if not KEY_FILE.exists():
        raise SystemExit(f"missing {KEY_FILE}\n  create it with your Groq key "
                         f"(https://console.groq.com/keys) — it is gitignored.")
    k = KEY_FILE.read_text().strip()
    if not k:
        raise SystemExit(f"{KEY_FILE} is empty")
    return k


def main() -> int:
    key = _key()
    h = {"Authorization": f"Bearer {key}"}
    print(f"key loaded from {KEY_FILE.name} (len {len(key)})")

    # ── which models can this key use? ──
    try:
        r = requests.get(f"{BASE}/models", headers=h, timeout=30)
    except requests.RequestException as e:
        raise SystemExit(f"cannot reach Groq: {type(e).__name__}") from e
    if r.status_code == 401:
        raise SystemExit("Groq rejected the key (401). Check it at https://console.groq.com/keys")
    if r.status_code != 200:
        raise SystemExit(f"Groq /models returned {r.status_code}: {r.text[:200]}")
    available = sorted(m["id"] for m in r.json().get("data", []))
    print(f"models visible to this key: {len(available)}")

    chosen = next((m for m in PREFERRED if m in available), None)
    if not chosen:
        # fall back to any chat-looking model rather than giving up
        chosen = next((m for m in available if not any(x in m for x in ("whisper", "tts", "guard"))), None)
    if not chosen:
        raise SystemExit("no usable chat model found for this key")
    print(f"selected model: {chosen}")
    others = [m for m in PREFERRED if m in available and m != chosen]
    if others:
        print(f"  (also available: {', '.join(others[:4])})")

    # ── does JSON mode work? the audit judge requires it ──
    body = {"model": chosen, "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": "Reply with strict JSON only."},
                         {"role": "user",
                          "content": 'Return exactly this json: {"ok": true, "n": 2}'}]}
    try:
        r2 = requests.post(CHAT_URL, headers={**h, "Content-Type": "application/json"},
                           json=body, timeout=60)
    except requests.RequestException as e:
        raise SystemExit(f"chat call failed: {type(e).__name__}") from e
    if r2.status_code != 200:
        print(f"  JSON-mode test FAILED ({r2.status_code}): {r2.text[:200]}")
        print("  -> this model cannot be used for auditing (the judge needs strict JSON).")
        return 1
    txt = r2.json()["choices"][0]["message"]["content"]
    try:
        parsed = json.loads(txt)
        print(f"JSON mode: OK -> {parsed}")
    except ValueError:
        print(f"  JSON mode returned non-JSON: {txt[:120]!r} — unusable for the judge.")
        return 1

    # ── rate limits, so batch concurrency can be set deliberately ──
    lim = {k2: v for k2, v in r2.headers.items() if k2.lower().startswith("x-ratelimit")}
    if lim:
        print("rate limits:")
        for k2 in sorted(lim):
            print(f"  {k2}: {lim[k2]}")
        rpm = lim.get("x-ratelimit-limit-requests")
        if rpm:
            try:
                n = int(rpm)
                print(f"  -> suggested batch concurrency: {max(1, min(8, n // 6))} "
                      f"(leaves headroom; 429s are retried with Retry-After)")
            except ValueError:
                pass

    MODEL_FILE.write_text(chosen + "\n")
    print(f"\nwrote {MODEL_FILE.name}")
    print("\nNow run:   . scripts/load_env.sh    (or export these directly)")
    print(f"  LLM_PROVIDER=openai")
    print(f"  OPENAI_BASE_URL={CHAT_URL}")
    print(f"  OPENAI_API_KEY=<from {KEY_FILE.name}>")
    print(f"  LLM_MODEL_FAST={chosen}")
    print(f"  LLM_MODEL_DEEP={chosen}")
    print("\nTEMPORARY: Groq is a third-party US service. Fine for redacted calibration data;")
    print("the approved enterprise LLM path is still the production answer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
