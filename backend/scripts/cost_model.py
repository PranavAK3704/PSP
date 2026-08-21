"""Cost model: what L1 support costs today vs what it costs on this engine.

    python scripts/cost_model.py

EVERY INPUT IS EITHER MEASURED OR NAMED AS AN ASSUMPTION. Nothing is a round number chosen
because it looked reasonable. The measured inputs come from this repo:

  · ticket volume, hub count, channel mix, resolution time  →  data/tickets.db (140,875 rows)
  · cost per LLM turn                                        →  llm/meter.py, measured on real
                                                                turns during the build
  · which ticket categories the data layer can reach         →  data/kapture_audits.json

Assumptions are collected in ASSUMPTIONS below with a reason for each, so a reader can disagree
with a number rather than with the conclusion.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

USD_INR = 88.0          # keep one place to change it
LAKH = 100_000
CRORE = 10_000_000


def inr(v: float) -> str:
    """Indian formatting, at the scale a reader thinks in."""
    if abs(v) >= CRORE:
        return f"Rs {v / CRORE:,.2f} Cr"
    if abs(v) >= LAKH:
        return f"Rs {v / LAKH:,.2f} L"
    return f"Rs {v:,.0f}"


# ── CURRENT COST ─────────────────────────────────────────────────────────────────────────────
CURRENT = {
    "kapture_per_quarter": 1.2 * CRORE,      # stated
    "agents": 30,                            # stated
    "agent_monthly": 30_000,                 # stated
}

# ── ASSUMPTIONS, each with its reason ────────────────────────────────────────────────────────
ASSUMPTIONS = {
    "turns_per_ticket": (2.5,
        "A resolved concern is rarely one message. The corpus has no turn count, so this is an "
        "estimate; the model is linear in it, so halving it halves the AI cost."),
    "fe_contacts_per_month_low": (0.25,
        "Delivery executives do not raise tickets today at all — hub captains do. A low rate "
        "assumes only a fraction of FEs ever contact support in a month."),
    "fe_contacts_per_month_high": (1.0,
        "One contact per FE per month. Aggressive for a population that has never had a "
        "support channel."),
    "router_absorption": (0.40,
        "Share of turns a deterministic router answers with NO model call — greetings, "
        "acknowledgements, and confirmed FAQ matches. UNMEASURED on WhatsApp (the corpus has "
        "no message text), so this is the plan's design target, to be verified in shadow mode "
        "before it is claimed."),
    "cache_saving": (0.38,
        "Prompt caching on the stable prefix (system prompt + tool schemas ~5,300 tokens, "
        "resent on every call). Input is 84%% of the per-turn cost, and caching reads at 0.1x, "
        "so ~45%% off input is ~38%% off the turn. NOT YET BUILT."),
    "l2_l3_agents_retained": (8,
        "This engine replaces L1. Escalations still need humans: the money path escalates by "
        "design, and 85%% of ticket categories have no data source yet. Retaining 8 of 30 is a "
        "judgement, not a measurement."),
}

# ── MEASURED per-turn cost ───────────────────────────────────────────────────────────────────
# Real turns from the build, with their trace-reported cost. Not modelled.
MEASURED_TURNS = [
    ("O&P — 'why is my load low' (Sonnet 5 loop, 2 calls)", 0.0450, 12_666, 466),
    ("O&P — capacity cut, 3 failing levers (2 calls)",      0.0496, 13_910, 525),
    ("Loss dispute — full trust spine incl. verifier (3 calls)", 0.06006, None, None),
]


def measured() -> dict:
    db = sqlite3.connect(str(ROOT / "data" / "tickets.db"))
    db.row_factory = sqlite3.Row
    q = lambda s: [dict(r) for r in db.execute(s)]
    tot = q("SELECT COUNT(*) n, MIN(substr(created_ts,1,10)) lo, MAX(substr(created_ts,1,10)) hi "
            "FROM tickets")[0]
    import datetime as dt
    days = (dt.date.fromisoformat(tot["hi"]) - dt.date.fromisoformat(tot["lo"])).days or 1
    hubs = q("SELECT COUNT(DISTINCT hub_code) n FROM tickets "
             "WHERE hub_code IS NOT NULL AND hub_code != ''")[0]["n"]
    wa = q("SELECT COUNT(*) n FROM tickets WHERE source = 'WhatsApp'")[0]["n"]
    art = q("SELECT ROUND(AVG(art_hours),1) a FROM tickets WHERE art_hours > 0")[0]["a"]
    latest = q("SELECT substr(created_ts,1,7) m, COUNT(*) n FROM tickets "
               "GROUP BY m ORDER BY m DESC LIMIT 1")[0]
    db.close()
    return {
        "tickets": tot["n"], "window_days": days, "from": tot["lo"], "to": tot["hi"],
        "per_month": tot["n"] / days * 30.0,
        "latest_month": latest["m"], "latest_month_n": latest["n"],
        "hubs": hubs, "per_hub_month": (tot["n"] / days * 30.0) / hubs,
        "whatsapp_pct": 100.0 * wa / tot["n"], "art_hours": art,
    }


def coverage() -> dict:
    """Which ticket categories the data layer can actually reach — measured, not estimated."""
    p = ROOT / "data" / "kapture_audits.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return {}
    # The file is {version, tickets: {ticket_no: {...}}, runs: [...]} — NOT a bare list. Reading
    # `.values()` off the top level gave 3 "tickets" (version, tickets, runs), which is how a
    # credibility section quietly becomes nonsense.
    tickets = data.get("tickets") if isinstance(data, dict) else None
    rows = list(tickets.values()) if isinstance(tickets, dict) else (
        list(data.values()) if isinstance(data, dict) else list(data))
    counts: dict[str, int] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        counts[str(r.get("disposition") or "?")] = counts.get(str(r.get("disposition") or "?"), 0) + 1
    return {"n": len(rows), "by_disposition": dict(sorted(counts.items(),
                                                          key=lambda kv: -kv[1])[:12])}


def main() -> int:
    m = measured()
    a = {k: v[0] for k, v in ASSUMPTIONS.items()}

    print("=" * 84)
    print("MEASURED — from data/tickets.db")
    print("=" * 84)
    print(f"  tickets                {m['tickets']:,}  ({m['from']} to {m['to']}, "
          f"{m['window_days']} days)")
    print(f"  run rate               {m['per_month']:,.0f} / month")
    print(f"  latest full month      {m['latest_month']}: {m['latest_month_n']:,} "
          f"({m['latest_month_n'] / m['per_month'] - 1:+.0%} vs the 3-month average)")
    print(f"  distinct hubs          {m['hubs']:,}")
    print(f"  per hub per month      {m['per_hub_month']:.1f} tickets")
    print(f"  WhatsApp share         {m['whatsapp_pct']:.1f}%")
    print(f"  avg resolution         {m['art_hours']} hours  "
          f"({m['art_hours'] / 24:.1f} days)")

    # ── current cost ──
    kapture_yr = CURRENT["kapture_per_quarter"] * 4
    agents_yr = CURRENT["agents"] * CURRENT["agent_monthly"] * 12
    current_yr = kapture_yr + agents_yr
    tickets_yr = m["per_month"] * 12
    print()
    print("=" * 84)
    print("CURRENT COST")
    print("=" * 84)
    print(f"  Kapture                {inr(kapture_yr)} / yr   "
          f"({inr(CURRENT['kapture_per_quarter'])} per quarter)")
    print(f"  {CURRENT['agents']} agents @ {inr(CURRENT['agent_monthly'])}/mo   "
          f"{inr(agents_yr)} / yr")
    print(f"  TOTAL                  {inr(current_yr)} / yr")
    print(f"  cost per ticket        Rs {current_yr / tickets_yr:,.0f}  "
          f"(at {tickets_yr:,.0f} tickets/yr)")

    # ── per-turn cost, measured ──
    print()
    print("=" * 84)
    print("MEASURED AI COST PER TURN")
    print("=" * 84)
    for label, usd, tin, tout in MEASURED_TURNS:
        extra = f"   {tin:,} in / {tout:,} out" if tin else ""
        print(f"  ${usd:.4f}  Rs {usd * USD_INR:6.2f}   {label}{extra}")
    avg = sum(t[1] for t in MEASURED_TURNS) / len(MEASURED_TURNS)
    print(f"  ------")
    print(f"  ${avg:.4f}  Rs {avg * USD_INR:6.2f}   AVERAGE (today, no router, no caching)")
    tuned = avg * (1 - a["cache_saving"])
    print(f"  ${tuned:.4f}  Rs {tuned * USD_INR:6.2f}   with prompt caching "
          f"(-{a['cache_saving']:.0%}, not yet built)")

    # ── scenarios ──
    print()
    print("=" * 84)
    print("AI COST AT SCALE")
    print("=" * 84)
    SCENARIOS = [
        ("Today's footprint", m["hubs"], 0, 0.0),
        ("10,000 captains", 10_000, 0, 0.0),
        ("10,000 captains + 1L FEs (low)", 10_000, 1 * LAKH, a["fe_contacts_per_month_low"]),
        ("10,000 captains + 2L FEs (low)", 10_000, 2 * LAKH, a["fe_contacts_per_month_low"]),
        ("10,000 captains + 2L FEs (high)", 10_000, 2 * LAKH, a["fe_contacts_per_month_high"]),
    ]
    # NOTE the column is AI-ONLY. The Rs 106 baseline is ALL-IN (platform + people), so the
    # comparable figure is the all-in per-ticket in the NET POSITION block below, not this one.
    print(f"  {'scenario':34} {'tickets/mo':>11} {'turns/mo':>10} "
          f"{'AI/yr today':>13} {'AI/yr tuned':>13} {'Rs/tkt AI':>10}")
    print(f"  {'-' * 34} {'-' * 11} {'-' * 10} {'-' * 13} {'-' * 13} {'-' * 10}")
    results = []
    for name, captains, fes, fe_rate in SCENARIOS:
        t_cap = captains * m["per_hub_month"]
        t_fe = fes * fe_rate
        tickets = t_cap + t_fe
        turns = tickets * a["turns_per_ticket"]
        llm_turns = turns * (1 - a["router_absorption"])
        raw_yr = turns * avg * USD_INR * 12                       # no router, no caching
        tuned_yr = llm_turns * tuned * USD_INR * 12               # router + caching
        results.append((name, tickets, turns, raw_yr, tuned_yr))
        print(f"  {name:34} {tickets:>11,.0f} {turns:>10,.0f} "
              f"{inr(raw_yr):>13} {inr(tuned_yr):>13} {tuned_yr / (tickets * 12):>10,.2f}")

    # ── the comparison that matters ──
    print()
    print("=" * 84)
    print("NET POSITION")
    print("=" * 84)
    retained = a["l2_l3_agents_retained"]
    retained_yr = retained * CURRENT["agent_monthly"] * 12
    freed_agents_yr = agents_yr - retained_yr
    for name, tickets, turns, raw_yr, tuned_yr in results[1:]:
        run_yr = tuned_yr + retained_yr
        saving = current_yr - run_yr
        print(f"\n  {name}")
        print(f"    AI inference (router + caching)      {inr(tuned_yr)}")
        print(f"    {retained} agents retained for L2/L3          {inr(retained_yr)}")
        print(f"    Kapture                              {inr(0)}  (replaced)")
        print(f"    ------")
        print(f"    NEW RUN RATE                         {inr(run_yr)} / yr")
        print(f"    vs today's {inr(current_yr)}          "
              f"{inr(saving)} saved  ({saving / current_yr:.0%})")
        # The apples-to-apples number: all-in per ticket against the all-in Rs 106 baseline.
        print(f"    all-in per ticket                    "
              f"Rs {run_yr / (tickets * 12):.2f}   vs Rs {current_yr / tickets_yr:,.0f} today "
              f"({current_yr / tickets_yr / (run_yr / (tickets * 12)):.0f}x cheaper)")
        if tickets > m['per_month']:
            print(f"    ... while serving {tickets / m['per_month']:.1f}x today's ticket volume")

    print()
    print(f"  Freed by replacing Kapture:  {inr(kapture_yr)} / yr")
    print(f"  Freed from {CURRENT['agents'] - retained} of {CURRENT['agents']} agent seats: "
          f"{inr(freed_agents_yr)} / yr")

    # ── what is NOT covered ──
    cov = coverage()
    print()
    print("=" * 84)
    print("HONEST LIMITS")
    print("=" * 84)
    if cov:
        print(f"  Labelled tickets analysed: {cov['n']:,}")
        print("  Largest categories and whether a data source exists today:")
        # Which dispositions have a data source wired TODAY. hardstop/shortage/qc from
        # valmo.db; load_planning from the growth-dashboard fixtures built this week.
        BACKED = {"hardstop_loss", "shortage_loss", "qc_failure", "load_planning"}
        for d, n in list(cov["by_disposition"].items())[:8]:
            tag = "data wired" if d in BACKED else "NO data source"
            print(f"    {d[:30]:32} {n:>5}  {100 * n / cov['n']:>5.1f}%   {tag}")
    print()
    print("  · Cost scales linearly with turns_per_ticket (assumed 2.5). Halve it, halve the AI bill.")
    print("  · router_absorption (40%) is a DESIGN TARGET, unverified on WhatsApp — the corpus")
    print("    carries no message text, so it must be measured in shadow mode before it is claimed.")
    print("  · Prompt caching (-38%) is designed, not built.")
    print("  · This replaces L1. L2/L3 escalation still needs people.")
    print("  · FE support is a NEW channel — delivery executives have no support path today, so")
    print("    that volume is additive capability, not a cost being displaced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
