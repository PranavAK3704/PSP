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
from app.engine import tools
from app.llm.base import LLMProvider
from app.engine.algo import followups as F
from app.engine.algo.router import Ctx as _Ctx
from app.substrate import loss_db

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

print("\n─ TURN 2: a bare digit in the PANEL is data, not a menu pick ───────────")
# The panel renders labelled buttons and shows no numbers, so a typed "2" cannot be a chip
# choice — it is far more likely "2 din se", an amount, or a count. Resolving it against a menu
# the captain was never shown is a guess, not a fallback.
before_opts = list(sessmod.STORE.get_or_create(CONV, CAP).last_options)
r_digit, _o, t_digit, _c = turn("2")
print(f"  '2' on channel=chat -> tier={t_digit!r} (None = correctly NOT treated as a tap)")
assert t_digit != "followup", "a typed digit in the panel was treated as a chip tap"
# Re-arm for the tap test below: the turn above cleared last_options, as every turn does.
r, o, t, c = turn("kaise theek karun")
assert t == "followup", t

print("\n─ TURN 2b: the SAME chip, chosen by TAP on the panel ───────────────────")
# The panel's route to option 2 is the tap, not the digit — so that is what is asserted here.
# The digit path is asserted on the WhatsApp route below, where the numbers are actually shown.
expect_id = o[1]["id"]
r2, o2, t2, c2 = turn(o[1]["label"], selected=expect_id)
print(f"  tapped {expect_id}  tier={t2}")
print(f"  reply: {r2[:120]}")
print(f"  chips: {[x['label'] for x in o2]}")
assert t2 == "followup", t2
# THE DECISIVE ASSERTION. This first read `assert r2 != r` — "the answer differs from turn 1" —
# which is far too weak: a deliberately broken resolver that ALWAYS returned option 1 passed the
# whole script, because option 1's answer also differs from turn 1's. The script even PRINTED
# the expected option and then asserted something else. It has to name the expected node's own
# authored text.
assert F._BY_ID[expect_id].answer in r2, (
    f"the tap resolved to the wrong option: expected {expect_id} "
    f"({F._BY_ID[expect_id].ask!r}), got {r2[:90]!r}")
print(f"  ✓ the tap landed on {expect_id} ({F._BY_ID[expect_id].ask!r})")

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

print("\n─ SCOPE HYGIENE: what must NOT arm a follow-up graph ───────────────────")
# All three were reproduced by the adversarial review. Each is a state where the follow-up
# engine would answer as though a diagnosis had been delivered, when none had.

# (a) An ESCALATING decision still returns disposition "load_planning" from policy_exec._out —
#     including the branch that fires because the growth dashboard could not be read at all. So
#     a captain told "I couldn't read your data, routing you to a human" would then have their
#     next question answered with "your load is low because RTO and OCF".
#     Driven through the REAL engine, not by calling clear_disposition() directly — a unit test
#     of the method proves the method works, not that the wiring calls it. (It did not: an
#     earlier version of this check passed against a build with the escalate guard removed.)
#
#     The load path is ideal here precisely because it ESCALATES for every seed captain: their
#     hubs are DEL-DC-014-shaped and no growth fixture covers them, so `_exec_load_planning`
#     returns action="escalate" — while still returning disposition="load_planning", which is
#     exactly the combination that used to arm a graph.
class _CallsLoadPolicy(LLMProvider):
    def __init__(self):
        self.calls = 0

    def chat(self, contents, model=None, system=None, tools=None, **kw):   # noqa: D102
        self.calls += 1
        if self.calls == 1:
            return {"role": "model", "parts": [{"functionCall": {
                "name": "apply_policy",
                "args": {"disposition": "load_planning"}}}]}, {"in": 0, "out": 0}
        return {"role": "model", "parts": [{"text": "Main ise team ko bhej raha hoon."}]}, \
            {"in": 0, "out": 0}

    def chat_metered(self, contents, model=None, node=None, system=None, tools=None,
                     turn=None, **kw):                                     # noqa: D102
        return self.chat(contents, model=model, system=system, tools=tools)


_esc_stub = _CallsLoadPolicy()
_orig = conversation.llm_registry.for_node
conversation.llm_registry.for_node = lambda node: (_esc_stub, "stub-model")
try:
    CONV_E = "hy-a"
    esc_events = [ev for ev in conversation.handle_turn(CONV_E, CAP, "mera load kam hai")]
finally:
    conversation.llm_registry.for_node = _orig

esc_pol = next((e for e in esc_events if e.get("node") == "policy"), None)
esc_action = (esc_pol or {}).get("data", {}).get("action")
print(f"  the decision: action={esc_action!r} — and it still reports "
      f"disposition='load_planning'")
assert esc_action == "escalate", \
    f"expected an escalating decision to test against, got {esc_action!r}"
esc_sess = sessmod.STORE.get_or_create(CONV_E, CAP)
print(f"  session after it: disposition={esc_sess.disposition!r} "
      f"facts={esc_sess.answer_facts}")
assert esc_sess.disposition is None, \
    f"an ESCALATING decision armed follow-up scope {esc_sess.disposition!r} — the captain was " \
    "told a human would look at it, and the next turn would have answered as if it had been " \
    "diagnosed"
assert esc_sess.answer_facts == {}, f"and left facts: {esc_sess.answer_facts}"
v = F.tier(_Ctx(message="load kam kyun hua", entities={}, context={}, session=esc_sess))
assert v is None, "the follow-up tier answered after an escalation"
print("  ✓ an escalation leaves no scope — the next turn goes to the LLM")

# (b) STALE FACTS on the SAME disposition. A second decision that read nothing returns
#     followup_facts={}, which tools.py then omits from the result entirely. The old
#     `if facts: … elif changed:` kept the previous decision's figures, so "mera number kya hai"
#     quoted a rate card the engine had just failed to read, as though it were current.
s2 = sessmod.Session(conversation_id="hy-b", captain_id=CAP)
s2.set_disposition("load_planning", FACTS)
assert s2.answer_facts["current"] == FACTS["current"]
s2.set_disposition("load_planning", {})          # same scope, read nothing
print(f"  same disposition, no facts read -> answer_facts={s2.answer_facts}")
assert s2.answer_facts == {}, f"stale facts survived: {s2.answer_facts}"
v2 = F.tier(_Ctx(message="mera number kya hai", entities={}, context={}, session=s2))
assert v2 is None, "a fact-filled node was offered with no facts to fill it"
print("  ✓ the newest decision's facts are the facts; a decision that read nothing leaves none")

# (c) A DEAD TURN. Tested by BEHAVIOUR, not by source layout: the tool call succeeds (arming a
#     scope in the old code) and then the next model call raises, so the captain sees only the
#     degradation message. Nothing was explained, so nothing may be followed up on.
#
#     An earlier version of this check compared source positions of the stash and the commit.
#     That proved nothing — they sit in different branches of the same loop, so source order is
#     not execution order — and it failed on correct code. Running the failure is the only test
#     that means anything here.
# A REAL seeded captain whose decision RESOLVES, so a scope genuinely would be armed. This
# matters more than it looks: `_exec_load_planning` ESCALATES for all three seed captains — their
# hubs are DEL-DC-014-shaped, not the 3-char hubs the growth fixtures cover — so a load-question
# version of this check can never arm anything and passes vacuously. It did: an earlier version
# used the load path and passed against a deliberately broken build.
#
# VLMO-CPT-3310 / VL0093310077 resolves at 0.92 with action=raise_for_reversal on
# disposition=hardstop_loss, which is exactly the shape that arms a scope.
DEAD_CAP, DEAD_AWB = "VLMO-CPT-3310", "VL0093310077"


class _DiesAfterTool(LLMProvider):
    """Step 1: call apply_policy on the resolving AWB. Step 2: raise, as an outage does."""

    def __init__(self):
        self.calls = 0

    def chat(self, contents, model=None, system=None, tools=None, **kw):   # noqa: D102
        self.calls += 1
        if self.calls == 1:
            return {"role": "model", "parts": [{"functionCall": {
                "name": "apply_policy",
                "args": {"disposition": "hardstop_loss", "awb": DEAD_AWB}}}]}, \
                {"in": 0, "out": 0}
        raise RuntimeError("provider outage mid-turn")

    def chat_metered(self, contents, model=None, node=None, system=None, tools=None,
                     turn=None, **kw):                                     # noqa: D102
        return self.chat(contents, model=model, system=system, tools=tools)


# The verifier is stubbed rather than driven: this is a money action, so `_apply_policy` runs an
# adversarial verify, and that is a second LLM call with nothing to do with the thing under test.
# Stubbing it keeps the test about the scope commit and keeps the run offline.
_orig_verify = tools.verifier.verify
_orig_for_node = conversation.llm_registry.for_node
_stub = _DiesAfterTool()
tools.verifier.verify = lambda *a, **k: {"passed": True, "agrees": True,
                                         "verdict": "agree", "reason": "stubbed for the test",
                                         "model": "stub", "proposed_by": "stub",
                                         "verified_by": "stub"}
conversation.llm_registry.for_node = lambda node: (_stub, "stub-model")
try:
    CONV_D = "hy-c"
    saw = [ev for ev in conversation.handle_turn(CONV_D, DEAD_CAP, "mera loss galat laga hai")]
finally:
    conversation.llm_registry.for_node = _orig_for_node
    tools.verifier.verify = _orig_verify

# Prove the decision RESOLVED, so the arming path was actually live on this turn.
pol = next((e for e in saw if e.get("node") == "policy"), None)
pol_action = (pol or {}).get("data", {}).get("action")
print(f"  decision: action={pol_action!r} conf={(pol or {}).get('data', {}).get('confidence')}")
assert pol_action and pol_action != "escalate", \
    f"the decision did not resolve (action={pol_action!r}), so this turn could never have " \
    "armed a scope and the check would pass vacuously"

reply_ev = next((e for e in saw if e.get("node") == "reply"), None)
print(f"  provider calls: {_stub.calls} (1 = tool, 2 = the one that died)")
print(f"  captain saw: engine_error={(reply_ev or {}).get('data', {}).get('engine_error')}")
assert _stub.calls == 2, f"the failure path was not reached ({_stub.calls} call(s))"
assert (reply_ev or {}).get("data", {}).get("engine_error") is True, \
    "expected the degradation reply, got a normal answer"
dead = sessmod.STORE.get_or_create(CONV_D, DEAD_CAP)
print(f"  session after the dead turn: disposition={dead.disposition!r} "
      f"facts={dead.answer_facts}")
assert dead.disposition is None, \
    f"a turn the captain never got an answer to armed scope {dead.disposition!r}"
assert dead.answer_facts == {}, f"and left facts behind: {dead.answer_facts}"
print("  ✓ the scope commits only where a reply reaches the captain")

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
