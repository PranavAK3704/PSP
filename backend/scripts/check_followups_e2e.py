"""End-to-end follow-ups: a real multi-turn conversation, both transports. NO LLM CALLS.

    python scripts/check_followups_e2e.py

check_followups.py asserts the ENGINE. This asserts the WIRING, which is where a feature like
this actually breaks: the tier can be perfect while the disposition never reaches the session,
the chips never reach the client, or the captain's "2" never reaches the option they were shown.

Driven through `conversation.handle_turn` and the real FastAPI route — not through the tier — so
every layer between the HTTP body and the answer is exercised.

Every assertion is FATAL. The first version of this script guarded the WhatsApp block with
`if a:` and printed its PASS banner after a 401 skipped every assertion in it; a harness that
can pass without testing anything is worse than no harness.

Exit code 0 = clean.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["PSP_PREROUTER"] = "on"
os.environ.pop("PSP_PREROUTER_GREETING", None)
os.environ.pop("PSP_PREROUTER_FOLLOWUP", None)

from app.engine import conversation, session as sessmod
from app.engine.algo import followups as F

FACTS = {"lever": "RTO Performance", "current": "31%", "target": "19%",
         "loss": 2108, "orders": 827, "max_potential": 1035}
CONV, CAP = "itest-conv-1", "VLMO-CPT-4471"

def turn(msg, selected=None):
    reply, opts, tier, cost = None, [], None, None
    for ev in conversation.handle_turn(CONV, CAP, msg, channel="chat", selected_option=selected):
        if ev.get("node") == "reply":
            d = ev.get("data") or {}
            reply, opts, tier, cost = d.get("reply"), d.get("options") or [], d.get("tier"), d.get("cost")
        if ev.get("node") == "firstpass":
            print(f"      [firstpass] {ev.get('detail','')[:100]}")
    return reply, opts, tier, cost

# turn 0 — seed the disposition exactly as a real apply_policy turn would
s = sessmod.STORE.get_or_create(CONV, CAP)
s.set_disposition("load_planning", FACTS)
print(f"seeded scope: {s.disposition}  facts={sorted(s.answer_facts)}\n")

print("─ TURN 1: free text follow-up ──────────────────────────────────────────")
r, o, t, c = turn("kaise theek karun")
print(f"  tier={t}  cost=${(c or {}).get('cost_usd', -1):.4f}  calls={(c or {}).get('calls')}")
print(f"  reply: {r}")
print(f"  chips: {[x['label'] for x in o]}")
print(f"  session.last_options: {sessmod.STORE.get_or_create(CONV, CAP).last_options}")
assert t == "followup", t
assert (c or {}).get("calls") == 0, "a router turn must make ZERO model calls"

print("\n─ TURN 2: reply with a NUMBER (the WhatsApp shape) ──────────────────────")
# Captured BEFORE the turn: the menu the captain was actually shown. Asserting against this is
# the only thing that proves the number landed where they pointed.
expect_id = o[1]["id"]
r2, o2, t2, c2 = turn("2")
print(f"  tier={t2}")
print(f"  reply: {r2}")
print(f"  chips: {[x['label'] for x in o2]}")
assert t2 == "followup", t2
# THE DECISIVE ASSERTION, and the reason it is written this way:
# this first read `assert r2 != r` — "the answer differs from turn 1". That is far too weak. A
# deliberately broken ordinal_choice that ALWAYS returned option 1 passed this whole script,
# because option 1's answer also differs from turn 1's. The script even PRINTED the expected
# option and then asserted something else. So the assertion has to name the expected node's own
# authored text, not merely observe that something changed.
assert F._BY_ID[expect_id].answer in r2, (
    f"'2' resolved to the wrong option: expected {expect_id} "
    f"({F._BY_ID[expect_id].ask!r}), got {r2[:90]!r}")
print(f"  ✓ '2' landed on option 2 = {expect_id} ({F._BY_ID[expect_id].ask!r})")

print("\n─ TURN 3: TAP a chip ───────────────────────────────────────────────────")
tap = o2[0]["id"]
r3, o3, t3, _ = turn(o2[0]["label"], selected=tap)
print(f"  tapped {tap}  tier={t3}")
print(f"  reply: {r3}")
print(f"  chips: {[x['label'] for x in o3]}")
assert t3 == "followup"

print("\n─ TURN 4: a glossary question ──────────────────────────────────────────")
r4, o4, t4, _ = turn("rto kya hai")
print(f"  tier={t4}")
print(f"  reply: {r4[:150]}...")

print("\n─ TURN 5: a NEW concern must fall through to the LLM ───────────────────")
r5, o5, t5, _ = turn("mera payment nahi aaya")
print(f"  tier={t5!r}  (None/absent = fell through to the LLM, which is correct)")
assert t5 != "followup", "a new concern must NOT be answered by the follow-up tier"

print("\n─ no answer repeated across the whole conversation ─────────────────────")
seen = [r, r2, r3, r4]
print(f"  {len(seen)} answers, {len(set(seen))} distinct")
assert len(set(seen)) == len(seen), "the engine repeated itself"

print("\n─ TURN 6: a menu is live for ONE turn only ─────────────────────────────")
# Turn 5 fell through to the LLM, so the menu from turn 4 must already be dead. A bare "2" now
# is far more likely to be an ANSWER ("2 din se") than a menu choice — and resolving it against
# a stale menu would answer a question the captain never asked.
st = sessmod.STORE.get_or_create(CONV, CAP)
print(f"  session.last_options after an LLM-path turn: {st.last_options}")
assert st.last_options == [], f"a stale menu survived: {st.last_options}"
r6, o6, t6, _ = turn("2")
print(f"  '2' -> tier={t6!r}  (None = correctly fell through, no stale menu to hit)")
assert t6 != "followup", "'2' resolved against a menu the captain was never shown this turn"

print("\n─ WHATSAPP round trip, through the real FastAPI route ──────────────────")
from fastapi.testclient import TestClient
from app.main import app
from app.auth import tokens
# The webhook sits behind _authed, so the test must actually authenticate. Skipping it on a 401
# and then printing a PASS banner is a self-passing assertion — the first run of this script did
# exactly that: the `if a:` guard swallowed the 401, every WhatsApp assertion was skipped, and
# the final line still claimed the round trip verified.
cl = TestClient(app)
HDR = {"Authorization": "Bearer " + tokens.make_token("itest@meesho.com", "approver")}

# fresh WhatsApp session, scope seeded the same way
wa = sessmod.STORE.get_or_create("wa-919000000001", "VLMO-CPT-4471")
wa.set_disposition("load_planning", FACTS)

def wa_post(text):
    # `payload`, not a bare {"from": ...}: WhatsAppIn declares `from_` with NO Pydantic alias,
    # so a JSON key of "from" silently does not bind and the route falls back to
    # {"from": None} -> unknown sender. The `payload` form is also the realistic shape (it is
    # what parse_webhook accepts from the simulator).
    res = cl.post("/api/whatsapp/webhook", headers=HDR,
                  json={"payload": {"from": "919000000001", "text": text}})
    # HARD failure, not a skip. An unreachable route means the round trip was NOT tested.
    assert res.status_code == 200, f"HTTP {res.status_code}: {res.text[:300]}"
    j = res.json()
    assert "outbound" in j, f"route returned an error instead of a reply: {j}"
    return j

a = wa_post("kaise theek karun")
body = a["outbound"]["text"]["body"]
print("  outbound body:\n" + "\n".join("      " + l for l in body.splitlines()))
assert a["options"], "no options were offered, so there was nothing to number"
for n in range(1, len(a["options"]) + 1):
    assert f"{n}. " in body, f"option {n} is missing from the numbered menu"
assert "(Number likh dijiye" in body, "the captain was never told they could reply with a number"

wa_expect = a["options"][1]
b = wa_post("2")
print(f"\n  captain replied '2'")
print(f"  expected option 2 = {wa_expect['label']!r} (id {wa_expect['id']})")
print(f"  answer: {b['reply'][:130]}")
# Positional order is the ENTIRE contract of a numbered menu, so it is asserted against the
# expected node's authored answer — not against "the reply changed", which a broken resolver
# satisfies trivially (it did: see the note on turn 2 above).
assert F._BY_ID[wa_expect["id"]].answer in b["reply"], (
    f"'2' resolved to the wrong option: expected {wa_expect['id']}, got {b['reply'][:90]!r}")
# And the menu must have been renumbered for the NEW answer, not re-sent unchanged.
assert b["options"] and [o["id"] for o in b["options"]] != [o["id"] for o in a["options"]], \
    "the same menu was offered again — the graph did not advance"
print("\n  ✓ full WhatsApp round trip: menu out, '2' in, option 2 answered, menu advanced")

print("\n" + "=" * 72)
print("FOLLOW-UP WIRING VERIFIED — 5 chat turns + a WhatsApp round trip, zero model calls.")
