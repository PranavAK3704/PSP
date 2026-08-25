"""Assertions for the Orders & Planning path. NO LLM CALLS, NO NETWORK.

    python scripts/check_op.py

This is the one queue that resolves with no stub — the resolution IS the reply — so the things
worth proving are different from the money path:

  · the fixtures still match the captain panel's contract (a drifted fixture answers a captain
    with a shape the real API would never send)
  · the engine reads the dashboard's verdicts and never re-derives them
  · the gate passes on evidence that was ACTUALLY READ, and can still FAIL — the point being
    that this executor does not use `_eval_real_loss`'s `present = required_evidence` shortcut,
    which makes the evidence check incapable of failing
  · `money_moving` is False and the adversarial verifier never fires — asserted, not assumed
  · an unreadable metric is UNKNOWN, never "good"
  · nothing ever reports the fixture source as the live one

Exit code 0 = clean.
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts._contain import contain; contain()   # MUST precede every `app.` import — see _contain.py
os.environ.setdefault("PSP_DATA_PROVIDER", "localdb")

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILED.append(label)


def head(n: str) -> None:
    print(f"\n{'─' * 78}\n{n}\n{'─' * 78}")


def main() -> int:
    from app.engine import data_queries, dataplane, policy_exec, tools
    from app.knowledge import policies as pol
    from app.substrate import captain_context as CC, loss_db
    from app.substrate.adapters.growth import GrowthConnector
    from app.substrate.adapters.growth import contract as gc
    from app.tri import Tri
    from app.trust import gate as trust_gate

    g = GrowthConnector()
    hubs = g.known_hubs()

    # ── 1. the fixtures against the panel's contract ─────────────────────────────
    head("[1] fixtures vs the captain panel's contract")
    check("fixtures exist", bool(hubs), ", ".join(hubs))
    for hub in hubs:
        ym, osum = g.your_metrics(hub), g.order_summary(hub)
        problems = gc.validate(ym, "your_metrics") + gc.validate(osum, "order_summary")
        check(f"{hub} matches the contract", not problems, "; ".join(problems[:3]))
        # The waterfall must be internally consistent or the widget and the engine will
        # disagree with each other on stage.
        wf_ok = (osum["max_potential"] - osum["missed_in_allocation"] == osum["current_eligible"]
                 and osum["current_eligible"] + osum["bar4_value"] == osum["final_manifested"])
        check(f"{hub} waterfall arithmetic closes", wf_ok,
              f"{osum['max_potential']} − {osum['missed_in_allocation']} = "
              f"{osum['current_eligible']}; + {osum['bar4_value']:+d} = {osum['final_manifested']}")
        check(f"{hub} _provenance stripped from the payload",
              not any(k.startswith("_") for k in {**ym, **osum}),
              "the real API never sends it, so a consumer must never see it")
        check(f"{hub} has a _provenance block on disk", bool(g.provenance(hub)),
              "a fixture must say it is one")
    dominance = {h: gc.dominant_reason(g.order_summary(h))[0] for h in hubs}
    check("at least one allocation_miss-dominant fixture",
          "allocation_miss" in dominance.values(), json.dumps(dominance))
    check("at least one capacity_loss-dominant fixture",
          "capacity_loss" in dominance.values(),
          "the negative bar4_value case — the sign is the interesting part")
    check("at least one healthy fixture",
          any(g.your_metrics(h).get("is_good") is True for h in hubs),
          "the control case: a load question here must not be answered with an invented problem")

    # ── 2. provenance labelling ──────────────────────────────────────────────────
    head("[2] provenance — fake data never wears a real label")
    check("connector source is the fixture label", g.source == "growth-dashboard-fixture",
          g.source)
    check("source is NOT the live label", g.source != "growth-dashboard")
    saved = dict(os.environ)
    os.environ["PSP_GROWTH_SOURCE"] = "live"
    live = GrowthConnector()
    raised = ""
    try:
        live.your_metrics("LZ5")
    except NotImplementedError as e:
        raised = str(e)
    check("live raises rather than falling back to fixtures", bool(raised))
    check("the error names the endpoint to wire",
          "/v1/captain/growth-dashboard/LZ5/your-metrics" in raised, raised[:88])
    os.environ.clear(); os.environ.update(saved)

    # ── 3. the divergence from the panel's client rule ───────────────────────────
    head("[3] an unreadable metric is UNKNOWN, not 'good'")
    check("unparseable current, lower-is-better → UNKNOWN",
          gc.lever_status("n/a", "19%", False) is Tri.UNKNOWN,
          "the panel's parseNumeric returns 0, so 0 <= 19 would read as GOOD")
    check("unparseable target → UNKNOWN", gc.lever_status("31%", "", False) is Tri.UNKNOWN)
    check("a real zero is still a value, not a failure",
          gc.lever_status("0%", "19%", False) is Tri.YES,
          "which is exactly why None, not 0, is the failure signal in parse_metric")
    check("higher-is-better is not inverted",
          gc.lever_status("94%", "70%", True) is Tri.YES
          and gc.lever_status("58%", "70%", True) is Tri.NO,
          "day0_attempt is the only higher-is-better lever; inverting it would tell a captain "
          "at 94% against a 70% target that they are failing")
    # Tested by DOING IT. The previous version asked `hasattr(Tri, "__bool__")`, which is True
    # via the enum metaclass, then took a branch that referenced `object.__bool__` — a name that
    # does not exist. It short-circuited before evaluating it and so always passed, while the
    # property it named was false: Tri(str, Enum) inherited str truthiness and bool(Tri.NO) was
    # True. A guard that cannot fail is worse than no guard.
    refused = []
    for member in (Tri.YES, Tri.NO, Tri.UNKNOWN):
        try:
            bool(member)
        except TypeError:
            refused.append(member.name)
    check("every Tri member REFUSES implicit truthiness", len(refused) == 3,
          f"refused: {refused} — `if tri:` must raise, not silently treat NO as a pass")
    check("   but identity comparison and .value still work",
          Tri.of(True) is Tri.YES and Tri.NO.value == "NO" and Tri.UNKNOWN.known is False)

    # ── 4. the executable policy exists and routes correctly ─────────────────────
    head("[4] the load_planning policy — the gap that made escalation route to the wrong team")
    p = pol.get_policy("load_planning")
    check("policies.get_policy('load_planning') is no longer None", bool(p),
          p.get("id") if p else "still None → apply_policy would return confidence 0.0")
    if p:
        check("escalation.team is Orders & Planning (L2)",
              (p.get("escalation") or {}).get("team") == "Orders & Planning (L2)",
              "it used to default to Losses & Debits (L2) — the wrong team for a load question")
        # NOTE this passes for ANY prose, because resolution.action on this policy is a
        # paragraph rather than a verb. The property that actually matters — that the DECISION
        # is not money-moving — is asserted per hub in section [5] via the gate verdict.
        check("resolution.action is not a money verb",
              (p.get("resolution") or {}).get("action") not in trust_gate.MONEY_ACTIONS,
              "weak by itself; section [5] asserts money_moving=False on the real verdict")
        check("required_evidence is machine-checkable, not documentation",
              all(" " not in e and "/" not in e for e in p.get("required_evidence", [])),
              ", ".join(p.get("required_evidence", [])))
        check("the authored evidence list was preserved, not discarded",
              bool(p.get("required_evidence_authored")),
              f"{len(p.get('required_evidence_authored') or [])} authored names kept")
        check("it came through the real compiler", p.get("compiled_by") == "sop_compiler",
              f"{p.get('compiled_by')} · {len(p.get('checks') or [])} checks")
    # Proven by MUTATING the overlay and re-reading. The old version asserted
    # `pol.invalidate() >= 18` — which is just `len(registry())` and is true whether or not the
    # cache was cleared, so it tested nothing. No file was written and nothing was re-read.
    import json as _json
    store = Path(pol.store_path())
    _before = store.read_text() if store.exists() else None
    try:
        overlay = _json.loads(_before) if _before else []
        probe = dict(overlay[0]) if overlay else None
        if probe:
            probe["disposition"] = "_calib_probe_"
            probe["id"] = "pol_probe_do_not_ship"
            store.write_text(_json.dumps(overlay + [probe], indent=1))
            stale = pol.get_policy("_calib_probe_")          # cache still warm -> None
            n = pol.invalidate()
            fresh = pol.get_policy("_calib_probe_")          # cache cleared -> present
            check("invalidate() is what makes a promotion visible in-process",
                  stale is None and fresh is not None and fresh["id"] == "pol_probe_do_not_ship",
                  f"before={stale is not None} after={fresh is not None} registry={n} — "
                  f"registry() is lru_cached and was never invalidated before")
    finally:
        if _before is not None:
            store.write_text(_before)
        else:
            store.unlink(missing_ok=True)
        pol.invalidate()
        check("   and the probe is cleaned up", pol.get_policy("_calib_probe_") is None)

    # ── 5. end to end through the REAL executor and gate ─────────────────────────
    head("[5] every fixture through the real policy_exec.execute + trust gate")
    by_hub = {}
    for pid in loss_db.known_partners():
        h = loss_db.captain_summary(pid).get("hub")
        if h in hubs and h not in by_hub:
            by_hub[h] = pid
    check("each fixture hub maps to a real partner in valmo.db",
          len(by_hub) == len(hubs), json.dumps(by_hub))

    for hub, pid in sorted(by_hub.items()):
        ctx = CC.get_context(pid)
        d = policy_exec.execute("load_planning", ctx, {})
        v = trust_gate.evaluate(d.get("policy") or {}, d, ctx)
        tag = f"{hub}"
        check(f"{tag} resolves in-conversation", d["action"] == "respond",
              f"action={d['action']} conf={d['confidence']}")
        check(f"{tag} gate PASSES", v["passed"], "; ".join(v["blocks"]))
        # The claim from the brief, asserted rather than assumed.
        check(f"{tag} money_moving is False", v["money_moving"] is False)
        check(f"{tag} verifier is NOT required", v["requires_adversarial_verify"] is False,
              "action=respond is not in gate.MONEY_ACTIONS, so no skeptic call is made")
        check(f"{tag} every check that ran actually passed",
              all(c["passed"] for c in d["checks_run"]),
              " | ".join(c["id"] for c in d["checks_run"]))
        check(f"{tag} evidence trail is populated from real reads",
              len(d["evidence_trail"]) >= 4, f"{len(d['evidence_trail'])} rows")
        check(f"{tag} the reply names the hub and a real number",
              hub in d["reason"] and any(ch.isdigit() for ch in d["reason"]))
        # No fabricated grievance on a healthy hub.
        if ctx["growth"]["your_metrics"].get("is_good") is True:
            check(f"{tag} healthy hub is told it is healthy",
                  "meeting its target" in d["reason"],
                  "the executor must not manufacture a problem the data does not show")

    # ── 6. the gate can still FAIL — the whole point of not declaring evidence ────
    head("[6] the evidence gate is capable of failing (no `present = required_evidence`)")
    # Asserted BEHAVIOURALLY, not by grepping the source — the executor's docstring explains
    # the shortcut it avoids, so a text search for the shortcut finds the explanation of it.
    hub, pid = sorted(by_hub.items())[0]
    good_ctx = CC.get_context(pid)
    _full = policy_exec.execute("load_planning", good_ctx, {})
    _req = set((_full.get("policy") or {}).get("required_evidence") or [])
    check("on a complete read, evidence_present covers required_evidence",
          _req and _req <= set(_full["evidence_present"]),
          f"{len(_full['evidence_present'])} of {len(_req)} — which is why the gate passes")

    # The discriminating case: degrade ONE input. If the executor declared the policy's own
    # requirements satisfied (as _eval_real_loss does at :83-84) this set would be unchanged
    # and the gate's evidence check could never fail on any input at all.
    _bad_ctx = copy.deepcopy(good_ctx)
    _bad_ctx["growth"]["order_summary"]["max_potential"] = None
    _bad = policy_exec.execute("load_planning", _bad_ctx, {})
    check("degrading one field SHRINKS evidence_present",
          set(_bad["evidence_present"]) < set(_full["evidence_present"]),
          f"{len(_full['evidence_present'])} → {len(_bad['evidence_present'])} "
          f"(declared evidence would have stayed at {len(_req)})")
    check("   so evidence_present is derived from reads, not declared",
          not (_req <= set(_bad["evidence_present"])),
          "the gate has something real to fail on")

    # (a) a hub with no growth data at all
    ctx = copy.deepcopy(good_ctx); ctx["growth"] = {"available": False}
    d = policy_exec.execute("load_planning", ctx, {})
    check("no growth data → escalate, not a guess",
          d["action"] == "escalate" and d["confidence"] < 0.8,
          f"conf {d['confidence']} · {d['reason'][:60]}")
    check("   and it names the right team", "Orders & Planning (L2)" in d["reason"])

    # (b) a truncated waterfall — the gate must block on missing evidence
    ctx = copy.deepcopy(good_ctx)
    ctx["growth"]["order_summary"]["current_eligible"] = None
    d = policy_exec.execute("load_planning", ctx, {})
    v = trust_gate.evaluate(d.get("policy") or {}, d, ctx)
    check("incomplete waterfall → escalate", d["action"] == "escalate")
    check("   and the GATE blocks on missing required evidence",
          not v["passed"] and any("evidence" in b.lower() for b in v["blocks"]),
          "; ".join(v["blocks"]) or "gate passed, which would prove the check is decorative")

    # (c) every lever unreadable — must not read as "your metrics are fine"
    ctx = copy.deepcopy(good_ctx)
    for spec in gc.LEVERS:
        ctx["growth"]["your_metrics"][spec["key"]] = {"current": "n/a", "target": "n/a"}
    d = policy_exec.execute("load_planning", ctx, {})
    check("all levers unreadable → escalate, never 'all good'",
          d["action"] == "escalate" and "meeting its target" not in d["reason"],
          d["reason"][:70])

    # ── 7. dispatch: an unrelated AWB must not hijack a load question ────────────
    head("[7] dispatch — a stray AWB in a load message must not answer the wrong question")
    real_awb = str(loss_db._query("SELECT awb FROM losses LIMIT 1", ())[0]["awb"])
    d = policy_exec.execute("load_planning", good_ctx, {"awb": real_awb})
    check("load_planning wins over the AWB branch", d["disposition"] == "load_planning",
          f"got disposition={d['disposition']} — captains do paste an unrelated AWB "
          f"into the same message")
    check("   and still resolves", d["action"] == "respond")

    # ── 8. the named query and the data plane ────────────────────────────────────
    head("[8] load_status named query + data-plane boundary")
    check("load_status is in the whitelist", "load_status" in data_queries.QUERIES)
    check("it has a composer", "load_status" in data_queries._COMPOSERS)
    decl = [d for d in tools.DECLARATIONS if d["name"] == "run_data_query"][0]
    check("the model is told load_status exists", "load_status" in decl["description"])
    check("the LLM still cannot write SQL",
          "sql" not in json.dumps(decl).lower(),
          "it selects a whitelisted name; no SQL reaches the tool surface")
    for hub, pid in sorted(by_hub.items()):
        ctx = CC.get_context(pid)
        res, _ev, _c, _a = tools.dispatch("run_data_query", {"query_name": "load_status"}, pid, ctx)
        leaks = dataplane.violations(res, set())
        check(f"{hub} tool result has no unsupplied identifiers", not leaks, str(leaks[:3]))
        check(f"{hub} the model gets an answer, not rows",
              set(res) == {"query", "answer", "rows_found"}, ", ".join(sorted(res)))
        check(f"{hub} the answer names the fixture source or a real figure",
              any(ch.isdigit() for ch in res["answer"]), res["answer"][:60])

    print(f"\n{'═' * 78}")
    if FAILED:
        print(f"FAILED {len(FAILED)}:")
        for f in FAILED:
            print(f"  · {f}")
        return 1
    print("O&P VERIFIED — resolves in-conversation on read verdicts, gate can still fail.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
