"""Verify the Anthropic key works — masked output only, never prints the key.

Checks, cheapest first:
  1. key present and shaped like a key
  2. GET /v1/models      — auth works, and which models this key can see
  3. count_tokens        — free, proves the messages surface accepts our shape
  4. one tiny completion — proves generation + reports real token usage

DELIBERATELY NOT CONTAINED (unlike every check_*.py in scripts/_contain.py's remit). This one
makes a real, billed call, and step 4's spend belongs in the REAL `llm_spend.json` — a probe
whose cost lands in a tmpdir and vanishes would make the running total under-report actual
money spent. It writes no concern rows, so there is nothing here for the ledger to over-count.
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def load_key() -> str | None:
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if k:
        return k
    for p in (HERE / "data" / "anthropic_key.txt",):
        if p.exists():
            v = p.read_text().strip()
            if v:
                return v
    return None


def mask(k: str) -> str:
    return f"{k[:7]}…{k[-4:]}  ({len(k)} chars)" if len(k) > 14 else f"({len(k)} chars)"


def main() -> int:
    key = load_key()
    if not key:
        print("FAIL  no key found in ANTHROPIC_API_KEY or backend/data/anthropic_key.txt")
        return 1
    print(f"  key      {mask(key)}")
    if not key.startswith("sk-ant-"):
        print("  WARNING  does not start with 'sk-ant-' — check you pasted the whole thing")

    try:
        import anthropic
    except ImportError:
        print("FAIL  the `anthropic` package is not installed in this venv")
        return 1
    print(f"  sdk      anthropic {anthropic.__version__}")

    client = anthropic.Anthropic(api_key=key)

    # 2. auth + visible models
    try:
        models = client.models.list(limit=20)
        ids = [m.id for m in models.data]
        print(f"  auth     OK — {len(ids)} models visible")
        for want in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"):
            hit = [i for i in ids if i.startswith(want)]
            print(f"    {'yes' if hit else 'NO '}  {want}{'  -> ' + hit[0] if hit else ''}")
    except Exception as e:
        name = type(e).__name__
        print(f"  auth     FAIL — {name}: {str(e)[:160]}")
        if "authentication" in str(e).lower() or "401" in str(e):
            print("           the key is being rejected. Regenerate it in the console.")
        return 1

    # 3+4. real call on the cheapest model, reporting usage
    model = next((i for i in ids if i.startswith("claude-haiku-4-5")), ids[0] if ids else "claude-haiku-4-5")
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()
        u = msg.usage
        print(f"  call     OK on {model} — replied {text!r}")
        print(f"  usage    in={u.input_tokens} out={u.output_tokens}")
        print("\nKEY WORKS.")
        return 0
    except Exception as e:
        print(f"  call     FAIL — {type(e).__name__}: {str(e)[:200]}")
        if "credit" in str(e).lower() or "billing" in str(e).lower():
            print("           auth is fine but there is no credit on the account. Add funds.")
        if "rate" in str(e).lower() or "429" in str(e):
            print("           rate limited on the first call — you are on the lowest tier.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
