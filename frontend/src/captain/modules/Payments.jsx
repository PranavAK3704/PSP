import { useMemo, useState } from "react";
import { Info } from "lucide-react";
import { paymentCycles, paymentTotals, pendingPayment } from "../figures.js";

/* ── Payments — the module a captain LANDS on ─────────────────────────────────────────────────

   UPSTREAM: valmo-partner-webview/src/modules/captain/payments/payment-records/
             (constants.tsx: QUICK_FILTERS, getPaymentRecordsTableColumns, MAIN_SUMMARY_TOOLTIP,
              TABLE_FOOTER_INFO, NO_DATA_PLACEHOLDER; components/PaymentCycleStatus)

   Every string below is quoted from that module. Two details are upstream's own and look like
   mistakes if you do not know that:

   · `Incentives` is a real column, shown only when a `showIncentives` flag is on, and FOLDED
     INTO Adjustments when it is off (`mergeIncentive={!showIncentives}` on AdjustmentsCell).
     The live panel has it off, so this reproduces the four-column layout, not five.
   · The two most recent cycles read "Under Computation" and have NO `Payment Details` button —
     upstream returns `null` for that cell rather than a disabled button.

   And the tooltip on the header card is the load-bearing one for this project:

       "This widget displays the total outstanding payment for the cycles where payouts have been
        calculated. This does not include the 'Under Computation' cycles. If you encounter any
        discrepancies, please raise a ticket using Kapture or connect with your AM."

   "Raise a ticket using Kapture or connect with your AM" is the entire current support model for
   a payment discrepancy, written into the product's own help text. It is reproduced verbatim,
   because the widget one row up in the sidebar is the answer to it. ── */

const QUICK_FILTERS = { THIS_MONTH: "This Month", LAST_MONTH: "Previous Month",
                        LAST_3_MONTHS: "Last 3 Months" };
const WINDOWS = { [QUICK_FILTERS.THIS_MONTH]: 4, [QUICK_FILTERS.LAST_MONTH]: 8,
                  [QUICK_FILTERS.LAST_3_MONTHS]: 12 };

const MAIN_SUMMARY_TOOLTIP =
  'This widget displays the total outstanding payment for the cycles where payouts have been ' +
  'calculated. This does not include the "Under Computation" cycles. If you encounter any ' +
  'discrepancies, please raise a ticket using Kapture or connect with your AM.';
const TABLE_FOOTER_INFO =
  "Only last 3 months data is being displayed here, for older data visit ";

const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const rupee = (n, sign = true) => {
  const s = Math.abs(n).toLocaleString("en-IN", { minimumFractionDigits: 2,
                                                  maximumFractionDigits: 2 });
  if (!sign) return `₹${s}`;
  return n < 0 ? `-₹${s}` : `+₹${s}`;
};
/** Upstream renders a cycle as "24 Aug - 30 Aug'26". */
const cycleLabel = (a, b) =>
  `${a.getUTCDate()} ${MON[a.getUTCMonth()]} - ${b.getUTCDate()} ${MON[b.getUTCMonth()]}'` +
  String(b.getUTCFullYear()).slice(2);
const creditLabel = (d, acct) => {
  const h = d.getUTCHours() % 12 || 12;
  const ap = d.getUTCHours() < 12 ? "am" : "pm";
  const ord = (n) => (n % 10 === 1 && n !== 11 ? "st" : n % 10 === 2 && n !== 12 ? "nd"
                    : n % 10 === 3 && n !== 13 ? "rd" : "th");
  return `Payment credited on ${d.getUTCDate()}${ord(d.getUTCDate())} ${MON[d.getUTCMonth()]}, ` +
         `${h}:${String(d.getUTCMinutes()).padStart(2, "0")} ${ap} to ${acct}`;
};

function Tip({ text }) {
  return (
    <span className="cp-tip" tabIndex={0}>
      <Info size={12} />
      <span className="cp-tip-body">{text}</span>
    </span>
  );
}

export default function Payments({ hub }) {
  const [win, setWin] = useState(QUICK_FILTERS.LAST_3_MONTHS);
  const all = useMemo(() => paymentCycles(hub || "LZ5", 12), [hub]);
  const rows = all.slice(0, WINDOWS[win]);
  const totals = paymentTotals(rows);
  const pending = pendingPayment(all);

  return (
    <>
      {/* ── the header card ── */}
      <section className="cp-card cp-pending">
        <div className="cp-pending-label">
          TOTAL PENDING PAYMENT TILL 25 AUG <Tip text={MAIN_SUMMARY_TOOLTIP} />
        </div>
        <div className="cp-pending-amt">{rupee(pending)}</div>
      </section>

      {/* ── All Payments ── */}
      <section className="cp-card">
        <div className="cp-card-top">
          <h2>All Payments</h2>
          <div className="cp-seg">
            {Object.values(QUICK_FILTERS).map((f) => (
              <button key={f} className={f === win ? "on" : ""} onClick={() => setWin(f)}>{f}</button>
            ))}
          </div>
        </div>

        <div className="cp-totals">
          {[["Total Base Pay", totals.base, true],
            ["Total Adjustments", totals.adj, true],
            ["Total Losses", -totals.loss, true],
            ["Total Earnings", totals.total, true]].map(([label, val]) => (
            <div key={label} className="cp-total">
              <div className="cp-total-label">{label} <Tip text={
                label === "Total Losses"
                  ? "Debits raised against your DC in this window — shortages, damages and " +
                    "hard-stop deductions. Each one is disputable."
                  : `Sum of ${label.replace("Total ", "").toLowerCase()} across every cycle in ` +
                    `the selected window.`} /></div>
              <div className={`cp-total-amt ${val < 0 ? "neg" : ""}`}>{rupee(val)}</div>
            </div>
          ))}
        </div>

        <div className="cp-tablewrap">
          <table className="cp-table">
            <thead>
              <tr>
                <th>Payment Cycle</th><th>Base Pay</th><th>Adjustments</th>
                <th>Losses</th><th>Total Earnings</th><th>Payment Details</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.key}>
                  <td>
                    <div className="cp-cycle">{cycleLabel(c.start, c.end)}</div>
                    {c.underComputation
                      ? <div className="cp-underc">Under Computation <Tip text={
                          "This cycle's payout has not been calculated yet. It is excluded from " +
                          "the total pending payment above." } /></div>
                      : <div className="cp-credited">{creditLabel(c.creditedAt, c.account)}</div>}
                  </td>
                  <td>{rupee(c.base)}</td>
                  <td>{c.adjustments ? rupee(c.adjustments, false) : "₹0"}</td>
                  <td className={c.losses ? "neg" : ""}>
                    {c.losses ? rupee(-c.losses) : "₹0.00"}
                  </td>
                  <td>{rupee(c.total)}</td>
                  {/* Upstream renders NOTHING here for an Under Computation row. */}
                  <td>{c.underComputation ? null
                        : <button className="cp-btn-out">View Details</button>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="cp-tablefoot">{TABLE_FOOTER_INFO}<a href="#older">Older Payments</a></div>
      </section>
    </>
  );
}
