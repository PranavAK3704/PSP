import { useEffect, useState } from "react";
import { ShieldAlert, Database, ChevronRight, TriangleAlert } from "lucide-react";
import { getAtRisk } from "../lib/api.js";
import { n0, inr } from "../lib/format.js";

/* ── At-risk shipments — a HINDSIGHT replay, and the labelling is the feature ───────────────

   This is the one panel on the Captain Panel reading REAL rows: `attribution ⋈ losses` out of
   valmo.db, keyed on a real partner id, with real AWBs and real money. LZ5 → 26 shipments,
   ₹11,567.

   AND EVERY ONE OF THOSE SHIPMENTS ALREADY BECAME A LOSS.

   Selection is on the outcome, so 100% of them "fire" the rule, and there is no negative class
   at all — shipments that were at risk and did NOT become losses are absent from `losses`
   entirely. Precision and recall are therefore UNDEFINED here, not merely unmeasured. There is
   no prevention rate to show, and the absence is structural rather than a gap in the work.

   So five things are deliberate, each mirroring something Calibration.jsx already does:

   1. **The card head names the genre** — "hindsight replay", with the chip "a reconstruction,
      not a watch list". That is the direct echo of Calibration's "a different quantity" caption.
   2. **The caption is always present**, never behind a toggle.
   3. **"Amount at Risk" is kept and immediately re-tensed.** It is upstream's label for this
      figure and a captain recognises it — but here it is money already debited, so the panel
      says so on the next line. Calibration's "Measures X — not Y" move, applied to money.
   4. **All four rungs are drawn, including the empty ones.** With the real numbers LZ5
      populates 2 of 4 and HKS populates 1 of 4. Fitting the display to the data would present a
      one-value ladder as a four-value system — Calibration's "a chart that fits itself to its
      data cannot show a gap in the data".
   5. **The clock rule is rendered.** Two different `as_of` choices give two different
      distributions, so tuning it per row to fill the rungs would itself be the overclaim. Making
      the rule visible makes the choice auditable rather than tasteful.

   FORBIDDEN in this file, and asserted by check_risk.py: any percentage, any "saved" /
   "prevented" / "avoided" figure, any count of nudges, any delta against a baseline, and any
   meter or progress bar — a meter implies a denominator, and the only denominator available
   here would be the prevented set, which is empty by construction. ── */

/* Severity, mirrored from the captain panel's own ShipmentsRiskCategory.

   BREACHED vs EXTREME need a SHAPE difference, not just a colour one. Upstream separates them as
   saturated red vs pale #FFCBC9 — a LIGHTNESS step that only exists on a white page; on this
   surface both would land in the same rose band. So BREACHED is a FILLED chip and EXTREME an
   outlined one. Fill-versus-outline survives greyscale, every CVD type, and a projector, which
   the original lightness step does not. */
export const SEV = {
  BREACHED: { order: 0, label: "Loss to be marked", filled: true,
              mark: "var(--c-rose)", text: "var(--bad)" },
  EXTREME: { order: 1, label: "Extreme-Risk", filled: false,
             mark: "var(--c-rose)", text: "var(--bad)" },
  HIGH: { order: 2, label: "High-Risk", filled: false,
          mark: "var(--c-amber)", text: "var(--warn)" },
  MODERATE: { order: 3, label: "Moderate-Risk", filled: false,
              mark: "var(--text-faint)", text: "var(--text-faint)" },
};
const ORDER = Object.keys(SEV).sort((a, b) => SEV[a].order - SEV[b].order);

function Chip({ sev }) {
  const s = SEV[sev];
  if (!s) return null;
  return (
    <span style={{ fontSize: 9, fontWeight: 700, padding: "2px 7px", borderRadius: 4,
      whiteSpace: "nowrap", letterSpacing: ".02em",
      background: s.filled ? s.mark : "transparent",
      border: s.filled ? "none" : `1px solid ${s.mark}`,
      color: s.filled ? "#0a0f1c" : s.text }}>
      {s.label}
    </span>
  );
}

/** The rung strip — the whole cohort legible without scrolling, empty rungs drawn. */
function Rungs({ by, amounts }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      {ORDER.map((k) => {
        const n = by?.[k] || 0;
        return (
          <div key={k} style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: 8,
            alignItems: "center", padding: "3px 0",
            opacity: n ? 1 : 0.45 }}>
            <span style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
              <span style={{ width: 9, height: 9, borderRadius: 2, flex: "none",
                background: n ? SEV[k].mark : "var(--surface-4)" }} />
              <span style={{ fontSize: 10.5, color: n ? "var(--text-mute)" : "var(--text-faint)",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {SEV[k].label}
              </span>
            </span>
            <span className="mono" style={{ fontSize: 10.5, fontVariantNumeric: "tabular-nums",
              color: n ? "var(--text)" : "var(--text-faint)" }}>
              {n ? n : "none in this cohort"}
            </span>
            <span className="mono" style={{ fontSize: 10.5, fontVariantNumeric: "tabular-nums",
              color: "var(--text-faint)", minWidth: 58, textAlign: "right" }}>
              {n ? inr(amounts?.[k] || 0) : ""}
            </span>
          </div>
        );
      })}
    </div>
  );
}

const CAP = { BREACHED: 5, EXTREME: 3, HIGH: 3, MODERATE: 3 };

function Rows({ rows, sev }) {
  const [open, setOpen] = useState(false);
  const cap = CAP[sev] ?? 3;
  const shown = open ? rows : rows.slice(0, cap);
  const hidden = rows.length - shown.length;
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 7, padding: "8px 0 5px" }}>
        <Chip sev={sev} />
        {/* Subjunctive in the rung header. The rows keep upstream's own present-tense
            "(N days left)" — the clock is stated once here rather than 26 times below. */}
        <span style={{ fontSize: 10, color: "var(--text-faint)" }}>
          {rows.length} shipment{rows.length === 1 ? "" : "s"} · would already have been here
        </span>
      </div>
      <div style={{ border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
        {shown.map((r, i) => (
          <div key={r.awb || i} style={{ display: "flex", gap: 8, alignItems: "center",
            padding: "7px 10px",
            borderBottom: i < shown.length - 1 ? "1px solid var(--line-soft)" : "none" }}>
            <span className="mono" style={{ fontSize: 10, color: "var(--text-mute)",
              flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" }}>
              {r.awb}
            </span>
            <span style={{ fontSize: 9.5, color: "var(--text-faint)", whiteSpace: "nowrap" }}>
              {r.days_left_label}
            </span>
            <span className="mono" style={{ fontSize: 10.5, fontWeight: 700,
              fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
              {inr(r.amount_at_risk_inr)}
            </span>
          </div>
        ))}
      </div>
      {hidden > 0 && (
        <button className="chip" onClick={() => setOpen(true)}
          style={{ marginTop: 6, fontSize: 10 }}>
          + {hidden} more in this rung <ChevronRight size={10} />
        </button>
      )}
    </div>
  );
}

/** The severity banner. Returns null in every state where a severity would be unearned. */
export function SeverityBanner({ data, onOpen }) {
  const s = data?.summary;
  if (!s?.available || !s?.total_shipments) return null;
  // A ladder with no stated clock is exactly the overclaim this design exists to prevent.
  if (!s.as_of || !s.clock_rule) return null;
  const top = ORDER.find((k) => (s.by_category || {})[k]);
  if (!top) return null;
  const sev = SEV[top];
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "flex-start", padding: "10px 13px",
      borderRadius: 8, background: "var(--surface-2)",
      border: `1px solid ${sev.mark}` }}>
      <TriangleAlert size={14} style={{ color: sev.mark, flex: "none", marginTop: 2 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <span style={{ fontSize: 12, color: "var(--text-mute)", lineHeight: 1.5 }}>
          <b style={{ color: sev.text }}>{s.by_category[top]} of {s.total_shipments}</b>
          {" "}shipment{s.total_shipments === 1 ? "" : "s"} on record for this hub sit at
          {" "}<b style={{ color: sev.text }}>{sev.label}</b> —
          {" "}{inr(s.amount_by_category?.[top] || 0)} of {inr(s.total_amount_at_risk_inr)}.
        </span>
        <div className="mono" style={{ fontSize: 9.5, color: "var(--text-faint)", marginTop: 3 }}>
          hindsight replay · {s.clock_rule}
        </div>
      </div>
      {onOpen && (
        <button className="chip" onClick={onOpen} style={{ fontSize: 10, flex: "none" }}>
          See the list
        </button>
      )}
    </div>
  );
}

export default function AtRiskPanel({ hub, onAsk, onData, id = "at-risk" }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  // The same staleness guard the page uses on its growth fetch, and for the same reason: this is
  // a SECOND hub-keyed request, so without it LZ5's rows can land under an HKS heading.
  useEffect(() => {
    if (!hub) return;
    let live = true;
    setData(null); setErr("");
    getAtRisk(hub)
      .then((r) => { if (live) { setData(r); onData?.(r); } })
      .catch((e) => { if (live) { setErr(String(e?.message || e).slice(0, 120)); onData?.(null); } });
    return () => { live = false; };
  }, [hub]);

  const s = data?.summary;
  const rows = data?.shipments || [];
  const grouped = ORDER.map((k) => [k, rows.filter((r) => r.risk_category === k)])
    .filter(([, rs]) => rs.length)
    // within a rung, biggest money first — the top of the ladder and the top of the money are
    // both what a reader needs, so both are at the top of the list
    .map(([k, rs]) => [k, [...rs].sort((a, b) => b.amount_at_risk_inr - a.amount_at_risk_inr)]);

  return (
    <div className="card" id={id}>
      <div className="card-head">
        <h3><ShieldAlert size={14} />At-risk shipments · hindsight replay</h3>
        <span className="mono" style={{ fontSize: 9, color: "var(--c-amber)" }}>
          a reconstruction, not a watch list
        </span>
      </div>
      <div style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 11 }}>
        {/* ALWAYS PRESENT, never behind a toggle. */}
        <div className="df-note" style={{ border: "none", padding: 0, color: "var(--text-mute)",
          lineHeight: 1.6 }}>
          <b style={{ color: "var(--text)" }}>Every shipment below already became a loss.</b>
          {" "}This is the severity ladder the monitor <i>would have</i> produced
          {s?.as_of ? <> on <span className="mono">{s.as_of}</span></> : null}, replayed from
          {" "}<span className="mono">valmo.db</span>. Not a live watch list. No nudge was sent,
          nothing was prevented, and there is deliberately no success rate on this screen.
        </div>

        {err && (
          <div className="df-note" style={{ color: "var(--warn)",
            borderColor: "rgba(255,185,95,.3)", background: "var(--warn-soft)" }}>
            The at-risk replay failed to build ({err}). That is a reporting failure, not an
            absence of risk — the order figures above are unaffected.
          </div>
        )}
        {!data && !err && (
          <div className="mono" style={{ fontSize: 10.5, color: "var(--text-faint)" }}>
            Replaying {hub} against valmo.db…
          </div>
        )}
        {data && !s?.total_shipments && !err && (
          <div className="df-note" style={{ border: "none", padding: 0 }}>
            No loss rows join hub {hub} in valmo.db. That is an absence of{" "}
            <b style={{ color: "var(--text-mute)" }}>data</b>, not an absence of risk.
          </div>
        )}

        {data && s?.total_shipments > 0 && (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 10,
              alignItems: "baseline", flexWrap: "wrap" }}>
              <span style={{ fontSize: 11.5, color: "var(--text-mute)" }}>
                Total Amount at Risk{" "}
                <b className="mono" style={{ color: "var(--text)", fontSize: 13 }}>
                  {inr(s.total_amount_at_risk_inr)}</b>
              </span>
              <span className="mono" style={{ fontSize: 10.5, color: "var(--text-faint)" }}>
                Total {n0(s.total_shipments)} Shipments
              </span>
            </div>
            {/* (3) upstream's label, immediately re-tensed. */}
            <div className="df-note" style={{ border: "none", padding: 0,
              color: "var(--text-faint)" }}>
              “Amount at Risk” is the captain panel’s label for this figure. Here it is money
              already debited.
            </div>

            <Rungs by={s.by_category} amounts={s.amount_by_category} />

            {s.uncategorised?.n > 0 && (
              <div className="df-note" style={{ border: "none", padding: 0,
                color: "var(--warn)" }}>
                {s.uncategorised.n} shipment{s.uncategorised.n === 1 ? "" : "s"} could not be
                banded — no in-scan on record, or a leg with no SLA. Their money is still in the
                total above and they sit in no rung. UNKNOWN is not a clean bill of health.
              </div>
            )}

            <div style={{ maxHeight: 300, overflowY: "auto", display: "flex",
              flexDirection: "column", gap: 10 }} className="custom-scrollbar">
              {grouped.map(([sev, rs]) => <Rows key={sev} rows={rs} sev={sev} />)}
            </div>

            <div className="df-note" style={{ display: "flex", alignItems: "center", gap: 7,
              flexWrap: "wrap" }}>
              <Database size={10} style={{ color: "var(--info)" }} />
              <span className="mono" style={{ fontSize: 9 }}>{s.source}</span>
              <span style={{ color: "var(--text-faint)" }}>
                · {s.reversal_signal?.n}/{s.reversal_signal?.of} carry a reversal signal on
                record — the same two columns the loss engine reads. That means it would have
                had something to argue with, not that any debit was reversed.
              </span>
            </div>
            {onAsk && (
              <button className="chip" onClick={() => onAsk("risk")} style={{ fontSize: 10.5 }}>
                Ask the widget about these losses
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
