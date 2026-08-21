"""Assertions for verifier independence + the per-node provider seam. NO LLM CALLS.

    python scripts/check_verifier.py

What is being guarded: `adversarial_verify` is the skeptic that must agree before a money
decision stands, and it was the same vendor AND the same model as the rest of the pipeline. A
verifier sharing the proposer's weights shares its blind spots, so it was a second opinion in
form only. These checks cover the seam that fixes it and — just as important — the reporting
that stops it CLAIMING an independence it does not have.

Exit code 0 = clean.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)


def head(n: str) -> None:
    print(f"\n{'-' * 78}\n{n}\n{'-' * 78}")


def main() -> int:
    from app.llm import registry as R
    saved = dict(os.environ)
    try:
        head("[1] the per-node provider seam")
        os.environ["ANTHROPIC_API_KEY"] = "x"
        os.environ["GEMINI_API_KEY"] = "y"
        os.environ.pop("LLM_PROVIDER_ADVERSARIAL_VERIFY", None)
        os.environ.pop("LLM_PROVIDER", None)
        R._provider.cache_clear()

        check("a per-node override exists at all",
              hasattr(R, "provider_for_node"),
              "active_provider_name() takes no node argument, so this had to be added")
        os.environ["LLM_PROVIDER_ADVERSARIAL_VERIFY"] = "gemini"
        check("LLM_PROVIDER_<NODE> routes one node to another VENDOR",
              R.provider_for_node("adversarial_verify") == "gemini"
              and R.provider_for_node("classify") != "gemini",
              f"verify={R.provider_for_node('adversarial_verify')} "
              f"classify={R.provider_for_node('classify')}")
        check("env beats models.yaml", R.provider_for_node("adversarial_verify") == "gemini")
        check("the model id follows the provider, not the tier",
              R.label_for_node("adversarial_verify") == "gemini:gemini-2.5-pro",
              R.label_for_node("adversarial_verify"))
        os.environ["LLM_PROVIDER_ADVERSARIAL_VERIFY"] = "not-a-provider"
        check("an unknown override falls back instead of raising",
              R.provider_for_node("adversarial_verify") == R.active_provider_name(),
              "a typo in one node must not take the engine down")
        os.environ.pop("LLM_PROVIDER_ADVERSARIAL_VERIFY")

        head("[2] no cross-vendor model bleed")
        # The bug this catches: an unscoped node_models entry follows the node across a vendor
        # boundary when its provider degrades, producing claude:gemini-2.5-pro.
        os.environ.pop("GEMINI_API_KEY", None)
        R._provider.cache_clear()
        lbl = R.label_for_node("adversarial_verify")
        vendor, model = lbl.split(":", 1)
        check("a degraded provider does NOT keep the other vendor's model id",
              "gemini" not in model if vendor != "gemini" else True, lbl)
        check("the label is internally consistent",
              (vendor == "gemini") == ("gemini" in model), lbl)

        head("[3] degradation is REPORTED, never silent")
        # Force the scenario rather than depending on what models.yaml happens to say today —
        # the safe default there is allowed to change without invalidating this behaviour.
        cfg = dict(R._config())
        cfg["node_providers"] = {"adversarial_verify": "gemini"}
        orig_cfg = R._config
        R._config = lambda: cfg
        try:
            st = R.independence_status()
            check("a YAML-requested provider with NO key falls back to the global one",
                  st["active"] == R.active_provider_name() and st["requested"] == "gemini",
                  f"requested={st['requested']} active={st['active']}")
            check("and independence is reported as False", st["independent"] is False,
                  "a config that ASKS for independence while not getting it is the worst case")
            check("and it is flagged as degraded", st["degraded"] is True)
            check("the reason names the env var to set", "GEMINI_API_KEY" in st["reason"],
                  st["reason"][:88])
            # With the key present it must NOT degrade.
            os.environ["GEMINI_API_KEY"] = "y"
            R._provider.cache_clear()
            st2 = R.independence_status()
            check("with the key present, independence is real",
                  st2["independent"] is True and st2["degraded"] is False,
                  f"active={st2['active']} proposer={st2['proposer']}")
        finally:
            R._config = orig_cfg
        os.environ["GEMINI_API_KEY"] = "y"
        R._provider.cache_clear()
        check("the shipped default never CLAIMS independence it lacks",
              R.independence_status()["degraded"] is False,
              "models.yaml is set to a provider whose key actually works")

        head("[4] key presence is checked PER PROVIDER")
        check("key_configured takes a provider argument",
              R.key_configured("gemini") is True and R.key_configured("claude") is True)
        os.environ.pop("GEMINI_API_KEY", None)
        check("a missing second credential is visible",
              R.key_configured("gemini") is False and R.key_configured("claude") is True,
              "without this, a deploy looks healthy until the first money decision")
        route = R.routing()
        check("routing() reports every node with its own key status",
              all({"provider", "model", "key_configured", "overridden"} <= set(v)
                  for v in route.values()), f"{len(route)} nodes")
        os.environ["GEMINI_API_KEY"] = "y"
        R._provider.cache_clear()

        head("[5] the verifier fails CLOSED, and says why")
        from app.trust import verifier
        os.environ["LLM_PROVIDER_ADVERSARIAL_VERIFY"] = "gemini"
        os.environ.pop("GEMINI_API_KEY", None)
        R._provider.cache_clear()
        v = verifier.verify(
            {"action": "reverse_debit", "amount_inr": 244, "checks_run": [],
             "disposition": "hardstop_loss"},
            [{"label": "Loss record", "value": "hardstop"}], "reversing")
        check("no key → agrees is False (fails CLOSED)", v["agrees"] is False,
              "a skeptic that cannot be reached must never auto-approve money")
        check("it is marked unavailable, not disagreeing", v.get("unavailable") is True)
        check("the reason says it is NOT a disagreement",
              "NOT the verifier disagreeing" in v["reason"], v["reason"][:80])
        check("the reason names the provider attempted", "gemini" in v["reason"])
        check("it carries proposed_by AND verified_by",
              bool(v.get("proposed_by")) and bool(v.get("verified_by")),
              f"{v.get('proposed_by')} vs {v.get('verified_by')}")

        head("[6] the Gemini retry — one attempt, transient codes only")
        from app.llm import gemini_provider as G
        check("a retry exists at all", hasattr(G, "_post_with_retry"),
              "it had a bare raise_for_status(), unlike the OpenAI and Claude providers")
        check("only transient statuses retry", set(G._RETRY_STATUS) == {429, 500, 502, 503, 504},
              str(sorted(G._RETRY_STATUS)))
        check("400/401/403 are NOT retried",
              not ({400, 401, 403} & set(G._RETRY_STATUS)),
              "retrying a rejected request wastes the captain's wait and tells nobody anything")
        check("the backoff is short", G._RETRY_SLEEP_S <= 2.0,
              f"{G._RETRY_SLEEP_S}s — the verifier runs inside a turn someone is waiting on")

        calls = {"n": 0}
        class _R:
            def __init__(self, code): self.status_code = code
            def raise_for_status(self):
                if self.status_code >= 400:
                    import requests
                    raise requests.HTTPError(f"{self.status_code}")
            def json(self): return {"candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
        seq = [429, 200]
        def fake_post(url, **kw):
            calls["n"] += 1
            return _R(seq[min(calls["n"] - 1, len(seq) - 1)])
        orig_post, orig_sleep = G.requests.post, G.time.sleep
        G.requests.post, G.time.sleep = fake_post, lambda s: None
        try:
            G._post_with_retry("http://x", {})
            check("a 429 then 200 succeeds on the retry", calls["n"] == 2, f"{calls['n']} attempts")
            calls["n"] = 0; seq = [400, 200]
            raised = False
            try:
                G._post_with_retry("http://x", {})
            except Exception:
                raised = True
            check("a 400 raises immediately without retrying",
                  raised and calls["n"] == 1, f"{calls['n']} attempt(s)")
        finally:
            G.requests.post, G.time.sleep = orig_post, orig_sleep
    finally:
        os.environ.clear(); os.environ.update(saved)
        from app.llm import registry as R2
        R2._provider.cache_clear()

    print(f"\n{'=' * 78}")
    if FAILED:
        print(f"FAILED {len(FAILED)}:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print("VERIFIER SEAM VERIFIED — independence is one env var away, and never overclaimed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
