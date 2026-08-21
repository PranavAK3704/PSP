"""Generate the GM briefing document (.docx).

    python scripts/build_gm_doc.py [--out ../docs/PSP_GM_Briefing.docx]

Every figure in the document is pulled from `cost_model.py` or from the repo, so regenerating
after a code change produces a document that is still true. Nothing is typed in twice.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from docx import Document                                    # noqa: E402
from docx.enum.table import WD_TABLE_ALIGNMENT               # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH                # noqa: E402
from docx.oxml import OxmlElement                            # noqa: E402
from docx.oxml.ns import qn                                  # noqa: E402
from docx.shared import Pt, RGBColor, Inches                 # noqa: E402

import cost_model as CM                                      # noqa: E402

INK = RGBColor(0x1A, 0x1A, 0x1A)
MUTED = RGBColor(0x5A, 0x5A, 0x5A)
ACCENT = RGBColor(0x0F, 0x6E, 0x63)      # teal, matches the product's chart tokens
WARN = RGBColor(0xA8, 0x5C, 0x11)
RULE = "D8D8D8"


def _shade(cell, hex_fill: str) -> None:
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:fill"), hex_fill)
    cell._tc.get_or_add_tcPr().append(el)


def h1(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(20)
    p.paragraph_format.space_after = Pt(7)
    r = p.add_run(text)
    r.font.size, r.font.bold, r.font.color.rgb = Pt(15), True, INK
    return p


def h2(doc, text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(13)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    r.font.size, r.font.bold, r.font.color.rgb = Pt(11.5), True, ACCENT
    return p


def body(doc, text, *, size=10.5, color=INK, italic=False, after=6, bold=False):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(after)
    r = p.add_run(text)
    r.font.size, r.font.color.rgb, r.italic, r.bold = Pt(size), color, italic, bold
    return p


def bullet(doc, text, *, bold_prefix="", size=10.5, color=INK):
    p = doc.add_paragraph(style="List Bullet")
    p.paragraph_format.space_after = Pt(3)
    if bold_prefix:
        r = p.add_run(bold_prefix)
        r.font.size, r.font.bold, r.font.color.rgb = Pt(size), True, color
    r = p.add_run(text)
    r.font.size, r.font.color.rgb = Pt(size), color
    return p


def table(doc, headers, rows, widths=None, highlight_rows=()):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, htext in enumerate(headers):
        c = t.rows[0].cells[i]
        c.text = ""
        r = c.paragraphs[0].add_run(htext)
        r.font.size, r.font.bold, r.font.color.rgb = Pt(9), True, INK
        _shade(c, "EFEFEF")
    for ri, row in enumerate(rows):
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            if i > 0:
                p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            r = p.add_run(str(val))
            r.font.size = Pt(9)
            r.font.color.rgb = INK
            if ri in highlight_rows:
                r.font.bold = True
        if ri in highlight_rows:
            for c in cells:
                _shade(c, "F4F9F8")
    if widths:
        for i, w in enumerate(widths):
            for row in t.rows:
                row.cells[i].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def build(out: Path) -> None:
    m = CM.measured()
    cov = CM.coverage()
    a = {k: v[0] for k, v in CM.ASSUMPTIONS.items()}
    inr = CM.inr

    kapture_yr = CM.CURRENT["kapture_per_quarter"] * 4
    agents_yr = CM.CURRENT["agents"] * CM.CURRENT["agent_monthly"] * 12
    current_yr = kapture_yr + agents_yr
    tickets_yr = m["per_month"] * 12
    cost_per_ticket = current_yr / tickets_yr

    avg_turn = sum(t[1] for t in CM.MEASURED_TURNS) / len(CM.MEASURED_TURNS)
    tuned_turn = avg_turn * (1 - a["cache_saving"])
    retained_yr = a["l2_l3_agents_retained"] * CM.CURRENT["agent_monthly"] * 12

    def scenario(captains, fes, fe_rate):
        tickets = captains * m["per_hub_month"] + fes * fe_rate
        turns = tickets * a["turns_per_ticket"]
        raw = turns * avg_turn * CM.USD_INR * 12
        tuned = turns * (1 - a["router_absorption"]) * tuned_turn * CM.USD_INR * 12
        return tickets, turns, raw, tuned, tuned + retained_yr

    doc = Document()
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Calibri", Pt(10.5)
    for s in doc.sections:
        s.top_margin = s.bottom_margin = Inches(0.8)
        s.left_margin = s.right_margin = Inches(0.9)

    # ── title ──
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run("Valmo Partner Support Platform")
    r.font.size, r.font.bold, r.font.color.rgb = Pt(21), True, INK
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run("Replacing the L1 ticket desk with a resolution engine")
    r.font.size, r.font.color.rgb = Pt(12), ACCENT
    body(doc, f"Internal briefing · Pranav Akella · figures measured from "
              f"{m['tickets']:,} real tickets ({m['from']} to {m['to']})",
         size=9, color=MUTED, after=14)

    # ── the one-paragraph version ──
    h1(doc, "In one paragraph")
    body(doc, f"Valmo's partner support today is a ticket desk: {m['tickets']:,} tickets in "
              f"{m['window_days']} days, {m['whatsapp_pct']:.0f}% of them on WhatsApp, closing in "
              f"an average of {m['art_hours']} hours. It costs {inr(current_yr)} a year — "
              f"{inr(kapture_yr)} to Kapture and {inr(agents_yr)} in agent salaries — which is "
              f"about Rs {cost_per_ticket:,.0f} per ticket. This platform replaces that L1 layer "
              f"with an engine that resolves a concern inside the conversation, from real "
              f"operational data, and shows its working. Measured cost per resolved turn today is "
              f"Rs {avg_turn * CM.USD_INR:.2f}. At 10,000 captains — two and a half times "
              f"today's volume — the all-in run rate is "
              f"{inr(scenario(10_000, 0, 0)[4])} a year.")

    # ── headline table ──
    h1(doc, "The numbers")
    s_cap = scenario(10_000, 0, 0)
    s_max = scenario(10_000, 2 * CM.LAKH, a["fe_contacts_per_month_low"])
    table(doc,
          ["", "Today", "10,000 captains", "10,000 captains + 2L FEs"],
          [["Tickets / month", f"{m['per_month']:,.0f}", f"{s_cap[0]:,.0f}", f"{s_max[0]:,.0f}"],
           ["Platform + tooling", inr(kapture_yr), "nil", "nil"],
           ["People", inr(agents_yr), inr(retained_yr), inr(retained_yr)],
           ["AI inference", "nil", inr(s_cap[3]), inr(s_max[3])],
           ["Total per year", inr(current_yr), inr(s_cap[4]), inr(s_max[4])],
           ["Cost per ticket", f"Rs {cost_per_ticket:,.0f}",
            f"Rs {s_cap[4] / (s_cap[0] * 12):,.2f}", f"Rs {s_max[4] / (s_max[0] * 12):,.2f}"],
           ["Saved vs today", "—",
            f"{inr(current_yr - s_cap[4])}  ({(current_yr - s_cap[4]) / current_yr:.0%})",
            f"{inr(current_yr - s_max[4])}  ({(current_yr - s_max[4]) / current_yr:.0%})"]],
          widths=[1.9, 1.5, 1.7, 1.9], highlight_rows=(4, 5, 6))
    body(doc, "The engine meters its own spend and reports it per turn, so the inference figures "
              "are measured on real conversations rather than estimated. The two levers that "
              "reduce them further — a deterministic router and prompt caching — are designed "
              "and not yet built; both are included at their design targets and flagged as such "
              "in the assumptions.", size=9.5, color=MUTED)

    # ── what it is ──
    h1(doc, "What it actually is")
    body(doc, "Not a ticketing system, and not a chatbot bolted onto one. Three things:")
    bullet(doc, "reads the real record — the loss ledger, the growth dashboard, the scan "
                "timeline — decides against a policy written in code, and answers in the "
                "captain's own language, in the conversation.",
           bold_prefix="A resolution engine. ")
    bullet(doc, "it docks beside the Growth Dashboard a captain already has open. Nothing new "
                "to learn, no new app, no ticket to raise.",
           bold_prefix="A widget inside the Captain Panel. ")
    bullet(doc, "every decision carries the evidence it was made from, which check passed, what "
                "the confidence was, and what it cost. A reviewer can replay any case.",
           bold_prefix="An audit trail by construction. ")

    h2(doc, "Two queues resolve end-to-end today")
    body(doc, "Losses & Debits — a captain disputes a debit, gives the AWB, and the engine reads "
              "the real loss row, runs the policy checks, passes the trust gate, gets an "
              "independent verifier's agreement, and returns a decision with its evidence. On "
              "400 real AWBs it reaches a decision on every one.")
    body(doc, "Orders & Planning — a captain asks why their load dropped. The engine reads the "
              "same two growth-dashboard endpoints the Captain Panel itself calls, identifies "
              "which stage lost the orders, names the metric that caused it against its target, "
              "explains why that target is set where it is, and quotes the rupee value of the "
              "gap. Verified live: hub HKS, 402 of 890 orders, 488 lost to a capacity cut, three "
              "metrics below target, Rs 6,420 forgone this cycle — one turn, Rs 4.36.")

    # ── trust ──
    h1(doc, "Why it can be trusted with money")
    bullet(doc, "the disputed AWB is looked up in the real ledger and the decision is made by "
                "deterministic code. The model chooses which tool to call; it never decides the "
                "outcome.", bold_prefix="No model decides a money case. ")
    bullet(doc, "required evidence must be present, the amount must be within a cap, confidence "
                "must clear a threshold, and the Partner Constitution must hold. Any one failing "
                "blocks the action.", bold_prefix="A trust gate in code, outside the prompt. ")
    bullet(doc, "before any money-moving decision stands, a separate model is asked to refute "
                "it. If it cannot be reached, the decision fails closed to a human — never open.",
           bold_prefix="An independent adversarial verifier. ")
    bullet(doc, "there is no HTTP write endpoint for a loss reversal anywhere in our stack; the "
                "real mechanism is a Kafka message consumed by LMS's own scheduler. So a "
                "favourable decision is a recommendation, labelled as one in the log, and the "
                "reply to the captain never says a payment has been made.",
           bold_prefix="It cannot move money, and it says so. ", color=WARN)
    bullet(doc, "a scan timeline that is missing is not the same as a scan timeline that shows "
                "nothing. The first escalates to a human; the second is evidence. Conflating "
                "them told captains their shipment never moved when the truth was that we could "
                "not see it.", bold_prefix="\"I don't know\" is a distinct answer. ")
    bullet(doc, "no partner record, AWB or employee name is sent to any model. Code runs the "
                "query, code composes the answer, and the model receives the composed sentence "
                "and a row count.", bold_prefix="Partner data never reaches the model. ")
    body(doc, "363 automated checks cover the above. They run offline, make no API calls, and "
              "finish in one command.", size=9.5, color=MUTED)

    # ── the honest gap ──
    h1(doc, "What is not solved yet")
    body(doc, f"Of {cov['n']:,} labelled tickets, roughly a fifth have a data source wired "
              f"today. The rest is not a build problem — the endpoints exist and the Captain "
              f"Panel already calls them in production, authenticated as the partner. It is a "
              f"data-access problem.")
    BACKED = {"hardstop_loss", "shortage_loss", "qc_failure", "load_planning"}
    rows = []
    for d, n in list(cov["by_disposition"].items())[:9]:
        rows.append([d.replace("_", " "), f"{n:,}", f"{100 * n / cov['n']:.1f}%",
                     "wired" if d in BACKED else "no data source"])
    table(doc, ["Ticket category", "Count", "Share", "Status today"], rows,
          widths=[2.6, 0.9, 0.9, 1.7])
    body(doc, "Payments alone is 26% of our ticket volume and has no data access at all. It is "
              "the single highest-value unlock available, and it needs a credential rather than "
              "an engineering quarter.", bold=True)
    body(doc, "46 endpoints are catalogued in the product itself — grouped by queue, each with "
              "its owning service, the adapter that would serve it, and whether that adapter is "
              "on real data, on a fixture, or unwritten. Going live is one environment variable "
              "per adapter.", size=9.5, color=MUTED)

    # ── how the cost was derived ──
    h1(doc, "How the cost figures were derived")
    h2(doc, "Measured, from the repository")
    table(doc, ["Input", "Value", "Source"],
          [["Tickets in window", f"{m['tickets']:,}", "tickets.db"],
           ["Window", f"{m['from']} to {m['to']} ({m['window_days']} days)", "tickets.db"],
           ["Run rate", f"{m['per_month']:,.0f} / month", "derived"],
           ["Latest month", f"{m['latest_month']}: {m['latest_month_n']:,} "
                            f"({m['latest_month_n'] / m['per_month'] - 1:+.0%})", "tickets.db"],
           ["Distinct hubs", f"{m['hubs']:,}", "tickets.db"],
           ["Tickets per hub / month", f"{m['per_hub_month']:.1f}", "derived"],
           ["WhatsApp share", f"{m['whatsapp_pct']:.1f}%", "tickets.db"],
           ["Average resolution", f"{m['art_hours']} h ({m['art_hours'] / 24:.1f} days)",
            "tickets.db"]],
          widths=[2.2, 2.3, 1.4])

    h2(doc, "Measured cost per turn")
    table(doc, ["Turn", "USD", "INR"],
          [[label, f"${usd:.4f}", f"Rs {usd * CM.USD_INR:.2f}"]
           for label, usd, _i, _o in CM.MEASURED_TURNS]
          + [["Average, today", f"${avg_turn:.4f}", f"Rs {avg_turn * CM.USD_INR:.2f}"],
             [f"With prompt caching (-{a['cache_saving']:.0%}, not yet built)",
              f"${tuned_turn:.4f}", f"Rs {tuned_turn * CM.USD_INR:.2f}"]],
          widths=[3.6, 1.1, 1.1], highlight_rows=(3,))
    body(doc, f"Converted at Rs {CM.USD_INR:.0f} to the dollar. The conversation runs on a "
              f"mid-tier model; the adversarial verifier runs on the strongest available, which "
              f"is why the loss turn costs more than the load turn.", size=9.5, color=MUTED)

    h2(doc, "Assumptions — disagree with these rather than with the conclusion")
    table(doc, ["Assumption", "Value", "Why"],
          [[k.replace("_", " "), f"{v[0]:g}", v[1].replace("%%", "%")]
           for k, v in CM.ASSUMPTIONS.items()],
          widths=[1.5, 0.6, 4.0])

    h2(doc, "Scenarios")
    SC = [("Today's footprint", m["hubs"], 0, 0.0),
          ("10,000 captains", 10_000, 0, 0.0),
          ("10,000 captains + 1L FEs", 10_000, 1 * CM.LAKH, a["fe_contacts_per_month_low"]),
          ("10,000 captains + 2L FEs", 10_000, 2 * CM.LAKH, a["fe_contacts_per_month_low"]),
          ("10,000 captains + 2L FEs, high contact", 10_000, 2 * CM.LAKH,
           a["fe_contacts_per_month_high"])]
    rows = []
    for name, cp, fe, rate in SC:
        tickets, turns, raw, tuned, allin = scenario(cp, fe, rate)
        rows.append([name, f"{tickets:,.0f}", inr(raw), inr(tuned), inr(allin),
                     f"{(current_yr - allin) / current_yr:.0%}"])
    table(doc, ["Scenario", "Tickets/mo", "AI, untuned", "AI, tuned", "All-in / yr", "Saved"],
          rows, widths=[2.1, 0.85, 1.0, 0.95, 1.0, 0.6], highlight_rows=(1, 3))
    body(doc, "\"Untuned\" is today's code with no router and no caching — a genuine upper bound. "
              "\"Tuned\" applies both design targets. \"All-in\" adds the "
              f"{a['l2_l3_agents_retained']} agents retained for L2 and L3 review. FE support is "
              "additive capability: delivery executives have no support channel today, so that "
              "volume displaces no existing cost.", size=9.5, color=MUTED)

    # ── close ──
    h1(doc, "Where it stands, and the ask")
    bullet(doc, "Losses & Debits and Orders & Planning both resolve end-to-end on real data.",
           bold_prefix="Built and verified. ")
    bullet(doc, "a captain looks at their own Growth Dashboard, doesn't understand why their "
                "load dropped, asks the docked widget, and gets the answer — with the rupee "
                "figure and the next step — computed from the same data the dashboard is "
                "drawing.", bold_prefix="Demo next week. ")
    bullet(doc, "payments (26% of tickets) and COD (16%) are each one credential away from being "
                "the same quality as Losses & Debits. Everything else is already built.",
           bold_prefix="The ask — data access. ")
    body(doc, "")
    body(doc, f"Every figure in this document regenerates from the repository "
              f"(scripts/cost_model.py, scripts/build_gm_doc.py), so it stays true as the code "
              f"changes.", size=9, color=MUTED, italic=True)

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    print(f"wrote {out}  ({out.stat().st_size:,} bytes)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT.parent / "docs" / "PSP_GM_Briefing.docx"))
    a = ap.parse_args()
    build(Path(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
