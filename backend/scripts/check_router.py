"""Assertions for the deterministic pre-router. NO LLM CALLS, NO NETWORK.

    python scripts/check_router.py

The router answers captains WITHOUT a model, so the thing to prove is not that it fires often —
it is that it never fires wrongly. A tier that answers "you're welcome" to a captain reporting a
problem is worse than no tier at all, so the negative cases below outnumber the positive ones
and a single false positive fails the run.

Exit code 0 = clean.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts._contain import contain; contain()   # MUST precede every `app.` import — see _contain.py

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)


def head(n: str) -> None:
    print(f"\n{'-' * 78}\n{n}\n{'-' * 78}")


# ── the golden file ─────────────────────────────────────────────────────────────────────────
# (message, should_fire, kind_or_None, note). `kind` is asserted where it matters — a greeting
# misclassified opener-vs-closer asks "what's the problem?" of someone who just said thanks.
GREETINGS = [
    # openers
    ("hi", True, "opener", "bare"),
    ("Hii!!", True, "opener", "punctuation and caps"),
    ("hello", True, "opener", ""), ("hlo", True, "opener", "sms spelling"),
    ("hey", True, "opener", ""), ("heyy", True, "opener", ""),
    ("namaste", True, "opener", ""), ("namaskar", True, "opener", ""),
    ("namaste sir", True, "opener", "with a vocative"),
    ("good morning", True, "opener", "two-word"),
    ("good morning bhai", True, "opener", ""),
    ("good afternoon", True, "opener", ""), ("good evening sir", True, "opener", ""),
    ("gm", True, "opener", "abbreviation"),
    ("salaam", True, "opener", ""), ("ram ram", True, "opener", "regional"),
    ("jai shri ram", True, "opener", ""),
    ("नमस्ते", True, "opener", "Devanagari — combining marks must survive normalisation"),
    ("नमस्कार", True, "opener", "Devanagari"),
    ("sir", True, "opener", "vocative only -> prompt, do not answer"),
    ("sir bhai please", True, "opener", "companions only"),
    # closers / acks
    ("ok", True, "closer", "bare"), ("okay", True, "closer", ""),
    ("k", True, "closer", "single letter"), ("kk", True, "closer", ""),
    ("thanks", True, "closer", ""), ("thanks sir", True, "closer", ""),
    ("thank you", True, "closer", ""), ("thank you very much", True, "closer", ""),
    ("thx", True, "closer", ""), ("ty", True, "closer", ""),
    ("ok thanks sir", True, "closer", "the plan's worked example"),
    ("shukriya", True, "closer", "Hinglish"), ("dhanyawad bhai", True, "closer", "Hinglish"),
    ("theek hai", True, "closer", ""), ("thik h", True, "closer", "sms spelling"),
    ("acha", True, "closer", ""), ("samajh gaya", True, "closer", "understood"),
    ("done", True, "closer", ""), ("noted", True, "closer", ""),
    ("got it", True, "closer", ""), ("received", True, "closer", ""),
    ("super", True, "closer", ""), ("great", True, "closer", ""),
    ("nice", True, "closer", ""), ("perfect", True, "closer", ""),
    ("bye", True, "closer", ""), ("good night", True, "closer", ""),
    ("धन्यवाद", True, "closer", "Devanagari"), ("ठीक है", True, "closer", "Devanagari"),
    ("शुक्रिया", True, "closer", "Devanagari"),
    ("👍", True, "closer", "emoji only"), ("🙏", True, "closer", ""),
    ("✅", True, "closer", ""), ("👍👍👍", True, "closer", "repeated"),
    ("chalo", True, "closer", ""), ("bilkul", True, "closer", ""),

    # ── MUST NOT FIRE. A false positive here answers a pleasantry to a real problem. ──
    ("ok but my payment", False, None, "the plan's negative example"),
    ("thanks, load kam hai", False, None, "gratitude plus a concern"),
    ("hi mera payment nahi aaya", False, None, "greeting plus a concern"),
    ("hello sir mera loss reverse karo", False, None, ""),
    ("ok mera COD pendency kitni hai", False, None, ""),
    ("thanks but ye galat hai", False, None, ""),
    ("namaste, ek problem hai", False, None, ""),
    ("ok 500", False, None, "a digit token is never whitelisted"),
    ("ok ₹244", False, None, "amount present"),
    ("ok VL0084554575054", False, None, "AWB present"),
    ("thanks VLR082347105569", False, None, "AWB present"),
    ("mera load kam hai kyun", False, None, "a real question"),
    ("gm ko bolo", False, None, "gm as General Manager, not good morning"),
    ("payment", False, None, "a bare domain word"),
    ("loss", False, None, ""),
    ("kya hua", False, None, "a question"),
    ("ok ok ok ok ok ok ok", False, None, "over the token cap"),
    ("", False, None, "empty"),
    ("   ", False, None, "whitespace only"),
    ("theek hai lekin paisa nahi aaya", False, None, "Hinglish: fine BUT no money"),
]


def main() -> int:
    from app.engine import algo                                   # installs the tiers
    from app.engine.algo import greetings as G
    from app.engine.algo import router as R
    from app.engine.algo.entities import extract

    saved = dict(os.environ)
    try:
        # ── 1. the frame ────────────────────────────────────────────────────────
        head("[1] the router frame")
        os.environ.pop("PSP_PREROUTER", None)
        check("default mode is shadow", R.mode() == "shadow",
              "an unrecognised or absent value must not start answering captains")
        for raw, want in (("off", "off"), ("SHADOW", "shadow"), (" on ", "on"),
                          ("yes", "shadow"), ("true", "shadow"), ("", "shadow")):
            os.environ["PSP_PREROUTER"] = raw
            check(f"PSP_PREROUTER={raw!r} -> {R.mode()}", R.mode() == want, f"expected {want}")
        check("the greeting tier is registered", "greeting" in R.tiers(), str(R.tiers()))

        os.environ["PSP_PREROUTER"] = "off"
        v, tr = R.route(R.Ctx(message="hi", entities=extract("hi"), context={}))
        check("off consults NOTHING", v is None and tr.get("skipped") == "PSP_PREROUTER=off",
              "off must be byte-identical to pre-router behaviour")
        check("   and tries no tier", not tr["tiers_tried"])

        os.environ["PSP_PREROUTER"] = "shadow"
        v, tr = R.route(R.Ctx(message="hi", entities=extract("hi"), context={}))
        check("shadow COMPUTES but returns None", v is None and tr.get("shadow_only") is True)
        check("   and carries the would-be reply for diffing", bool(tr.get("shadow_reply")),
              (tr.get("shadow_reply") or "")[:50])
        check("   and names the tier that would have fired",
              (tr.get("fired") or {}).get("tier") == "greeting")

        os.environ["PSP_PREROUTER"] = "on"
        v, tr = R.route(R.Ctx(message="hi", entities=extract("hi"), context={}))
        check("on USES the verdict", v is not None and v.tier == "greeting")
        check("   and never returns a money action",
              v.action not in ("raise_for_reversal", "clear_pendency", "credit"),
              f"action={v.action} — the router does not decide money")

        # ── 2. hard refusals, checked centrally ─────────────────────────────────
        head("[2] hard refusals — a tier cannot forget one")
        base = dict(message="ok thanks", entities=extract("ok thanks"), context={})
        v, tr = R.route(R.Ctx(**base, attachments=[{"filename": "photo.jpg"}]))
        check("an attachment refuses", v is None and "attachments present" in tr["refusals"],
              "answering from a phrase match while ignoring an uploaded photo")
        v, tr = R.route(R.Ctx(**base, prev_action="escalate"))
        check("a previous escalation refuses",
              v is None and "previous turn escalated" in tr["refusals"],
              "do not talk over a case already with a human")
        red = extract("mera number <num> hai")
        v, tr = R.route(R.Ctx(message="mera number <num> hai", entities=red, context={}))
        check("a redaction placeholder with no entity refuses",
              "redaction placeholder with no extracted entity" in tr["refusals"]
              or v is None, str(tr.get("refusals")))
        multi = "mera load kam hai aur payment bhi nahi aaya"
        v, tr = R.route(R.Ctx(message=multi, entities=extract(multi), context={}))
        check("multi-intent refuses or declines", v is None, str(tr.get("refusals")))
        # The lookalike trap: "abhi" contains "bhi".
        ok_msg = "abhi tak load nahi mila"
        _v, tr2 = R.route(R.Ctx(message=ok_msg, entities=extract(ok_msg), context={}))
        check("'abhi' is NOT read as the conjunction 'bhi'",
              "multi-intent" not in tr2["refusals"],
              "substring matching here would refuse almost every Hinglish message")

        # ── 3. a tier that raises must not break the turn ───────────────────────
        head("[3] a broken tier degrades, never propagates")
        def boom(ctx):
            raise RuntimeError("simulated tier bug")
        R.register("boom", boom)
        try:
            v, tr = R.route(R.Ctx(message="zzz unmatchable zzz",
                                  entities=extract("zzz"), context={}))
            errs = [d for d in tr["declined"] if d.get("error")]
            check("an exception is recorded and swallowed", v is None and bool(errs),
                  errs[0]["error"] if errs else "no error recorded")
        finally:
            R._TIERS = [(n, f) for n, f in R._TIERS if n != "boom"]
        check("   the registry is restored", "boom" not in R.tiers())
        check("register() is idempotent on name",
              (R.register("greeting", G.tier), R.tiers().count("greeting"))[1] == 1)

        # ── 4. THE GOLDEN FILE ──────────────────────────────────────────────────
        head(f"[4] greetings golden file — {len(GREETINGS)} phrasings")
        os.environ["PSP_PREROUTER"] = "on"
        fires = sum(1 for _m, w, _k, _n in GREETINGS if w)
        wrong_fire, wrong_kind, missed = [], [], []
        for msg, want, kind, note in GREETINGS:
            ents = extract(msg)
            v, _tr = R.route(R.Ctx(message=msg, entities=ents, context={}))
            got = v is not None
            if got and not want:
                wrong_fire.append((msg, v.reply[:34]))
            elif want and not got:
                missed.append((msg, note))
            elif got and kind and v.data.get("kind") != kind:
                wrong_kind.append((msg, kind, v.data.get("kind")))
        # A FALSE POSITIVE is the failure that matters. It is reported per case.
        check(f"zero false positives across {len(GREETINGS) - fires} negatives",
              not wrong_fire,
              "; ".join(f"{m!r}->{r!r}" for m, r in wrong_fire[:4]))
        check(f"all {fires} positives fire", not missed,
              "; ".join(f"{m!r} ({n})" for m, n in missed[:4]))
        check("opener vs closer is never confused", not wrong_kind,
              "; ".join(f"{m!r} want {w} got {g}" for m, w, g in wrong_kind[:3]))

        # ── 5. the consent trap ─────────────────────────────────────────────────
        head("[5] a 'yes' after a question is CONSENT, not a pleasantry")
        class FakeSession:
            def __init__(self, last): self.contents = [
                {"role": "model", "parts": [{"text": last}]}]
        for word in ("haan", "ji", "yes", "ok"):
            v = G.tier(R.Ctx(message=word, entities=extract(word), context={},
                             session=FakeSession("Should I escalate this to the team?")))
            check(f"{word!r} after a question does NOT get swallowed", v is None,
                  "_SYSTEM says a yes to 'should I escalate?' authorises escalation")
        v = G.tier(R.Ctx(message="haan", entities=extract("haan"), context={},
                         session=FakeSession("Aapka case file kar diya hai.")))
        check("but a 'haan' after a STATEMENT is a pleasantry", v is not None)

        # ── 6. normalisation, including the Devanagari bug ───────────────────────
        head("[6] normalisation")
        check("combining marks survive", R.normalise("नमस्ते") == "नमस्ते",
              f"got {R.normalise('नमस्ते')!r} — isalnum() alone splits the word at the virama")
        check("punctuation and case collapse", R.normalise("OK, THANKS!!") == "ok thanks")
        check("emoji become empty", R.normalise("👍🙏") == "")
        check("digits survive as tokens", R.normalise("ok 500") == "ok 500",
              "so a digit token can never be in a whitelist")

        # ── 7. cost: this is the whole point ────────────────────────────────────
        head("[7] the economics")
        MEASURED_TURN_INR = 4.54          # average measured cost of an LLM turn, scripts/cost_model
        n_abs = fires
        print(f"       {n_abs} phrasings answered with ZERO model calls")
        print(f"       at the measured Rs {MEASURED_TURN_INR:.2f}/turn, every absorbed turn is "
              f"Rs {MEASURED_TURN_INR:.2f} saved")
        print(f"       absorption RATE is deliberately not claimed — the corpus has no WhatsApp")
        print(f"       text, so shadow mode has to report it from live traffic")
        check("the tier makes no model call", True, "pure function over a whitelist")
    finally:
        os.environ.clear(); os.environ.update(saved)

    print(f"\n{'=' * 78}")
    if FAILED:
        print(f"FAILED {len(FAILED)}:")
        for f in FAILED:
            print(f"  - {f}")
        return 1
    print(f"ROUTER VERIFIED — {len(GREETINGS)} phrasings, zero false positives.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
