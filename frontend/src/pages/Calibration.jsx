import { useEffect, useState } from "react";
import { Gauge, TriangleAlert, Users, Info, Table2 } from "lucide-react";
import { getCalibration } from "../lib/api.js";

/* ── Calibration ──────────────────────────────────────────────────────────────────────────
   The gate blocks anything under CONFIDENCE_THRESHOLD = 0.80, and the comment above that
   constant says a 0.9-confident decision should be right ~90% of the time. This screen
   measures whether that is true. It isn't — and showing that is the point.

   Two deliberate rendering choices, both about not overclaiming:

   · EMPTY BINS ARE DRAWN. Ten fixed bins over [0,1], so the fact that only four confidence
     values exist is visible as seven blank rows rather than hidden by drawing exactly four
     bars. A chart that fits itself to its data cannot show a gap in the data.
   · AN UNLABELLED BIN IS NOT A ZERO. `observed: null` renders as "no label", never as 0% —
     otherwise a bin nobody has verified looks identical to a bin that got everything wrong.

   The Kapture panel beside it is the one place real paired machine/human verdicts exist
   (n=1,089). It is captioned as a DIFFERENT quantity, because presenting audit agreement as
   decision-confidence calibration is exactly the sleight of hand this screen exists to avoid. ── */

const pct = (v) => (v == null ? null : `${Math.round(v * 100)}%`);

function Bin({ b, maxN }) {
  const w = maxN ? Math.max(b.n ? 2 : 0, (b.n / maxN) * 100) : 0;
  const tone = b.above_gate ? "var(--c-teal)" : "var(--c-amber)";
  return (
    <div className="df-row" style={{ gridTemplateColumns: "62px 1fr 96px 78px", padding: "4px 0" }}>
      <span className="k mono" style={{ fontSize: 10.5,
        color: b.n ? "var(--text-mute)" : "var(--text-faint)" }}>{b.label}</span>
      <span className="df-bar" style={{ background: "var(--c-track)" }}>
        {b.n > 0 && <i style={{ width: `${w}%`, background: tone }} />}
      </span>
      <span className="v mono" style={{ fontSize: 10.5,
        color: b.n ? "var(--text)" : "var(--text-faint)" }}>
        {b.n === 0 ? "—" : `n=${b.n}`}
        {b.labelled > 0 && <span style={{ color: "var(--c-teal)" }}> · {b.labelled} labelled</span>}
      </span>
      <span className="v mono" style={{ fontSize: 10.5,
        color: b.observed == null ? "var(--text-faint)" : "var(--text)" }}>
        {b.n === 0 ? "" : b.observed == null ? "no label" : pct(b.observed)}
      </span>
    </div>
  );
}

export default function Calibration() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const [showPairs, setShowPairs] = useState(false);

  useEffect(() => {
    getCalibration().then(setD).catch(() => setErr("Could not read the calibration report."));
  }, []);

  if (err) return <div className="df-note" style={{ color: "var(--warn)" }}>{err}</div>;
  if (!d) return <div className="mono" style={{ fontSize: 12, color: "var(--text-faint)", padding: 18 }}>
    Reading the concern log…</div>;

  const r = d.reliability || {};
  const k = d.kapture || {};
  const f = r.finding || {};
  const maxN = Math.max(...(r.bins || []).map((b) => b.n), 1);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 13 }}>
      {/* The finding, first. A reader should not have to derive it from the chart. */}
      <div className="df-note" style={{ display: "flex", gap: 10, alignItems: "flex-start",
        borderColor: "rgba(255,185,95,.3)", background: "var(--warn-soft)", padding: "11px 13px" }}>
        <TriangleAlert size={13} style={{ color: "var(--warn)", flex: "none", marginTop: 2 }} />
        <div>
          <div style={{ color: "var(--warn)", fontWeight: 700, marginBottom: 4 }}>{f.headline}</div>
          <div style={{ color: "var(--text-mute)", lineHeight: 1.65 }}>{f.why}</div>
          <div style={{ color: "var(--text-mute)", lineHeight: 1.65, marginTop: 5 }}>{f.gate_effect}</div>
        </div>
      </div>

      <div className="df-tiles">
        <div className="df-tile"><div className="lab"><Gauge size={11} />gate threshold</div>
          <div className="val">{r.threshold}</div>
          <div className="sub">trust/gate.py</div></div>
        <div className="df-tile"><div className="lab">decisions scored</div>
          <div className="val">{r.with_confidence}</div>
          <div className="sub">of {r.concerns} concerns</div></div>
        <div className="df-tile" style={{ borderTop: "2px solid var(--c-rose)" }}>
          <div className="lab">distinct values</div>
          <div className="val" style={{ color: "var(--c-rose)" }}>
            {(r.distinct_confidence_values || []).length}</div>
          <div className="sub mono">{(r.distinct_confidence_values || []).join("  ")}</div></div>
        <div className="df-tile" style={{ borderTop: "2px solid var(--c-rose)" }}>
          <div className="lab">outcome labels</div>
          <div className="val" style={{ color: "var(--c-rose)" }}>{r.labelled}</div>
          <div className="sub">plottable of {r.labels_total} collected</div></div>
      </div>

      <div className="df-grid">
        <div className="card">
          <div className="card-head">
            <h3><Gauge size={14} />Reliability by confidence bin</h3>
            <span className="mono" style={{ fontSize: 9.5, color: "var(--text-faint)" }}>
              empty bins shown
            </span>
          </div>
          <div style={{ padding: "13px 15px" }}>
            <div className="df-row" style={{ gridTemplateColumns: "62px 1fr 96px 78px",
              padding: "0 0 6px" }}>
              {["confidence", "", "decisions", "observed"].map((h, i) => (
                <span key={i} className="lab mono" style={{ fontSize: 9,
                  letterSpacing: ".1em", textTransform: "uppercase", color: "var(--text-faint)" }}>{h}</span>
              ))}
            </div>
            {(r.bins || []).map((b) => <Bin key={b.label} b={b} maxN={maxN} />)}
            <div className="df-note" style={{ border: "none", padding: "10px 0 0" }}>
              Amber bins are below the gate and block; teal bins pass. Seven of ten are empty
              because the four values are constants, not measurements — a chart fitted to its
              own data would have drawn four bars and hidden that.
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-head"><h3><Info size={14} />Why there is nothing to calibrate against</h3></div>
          <div style={{ padding: "13px 15px" }}>
            {Object.entries(r.label_semantics || {}).map(([src, meaning]) => (
              <div key={src} style={{ marginBottom: 9 }}>
                <div className="df-row" style={{ gridTemplateColumns: "1fr auto", padding: 0 }}>
                  <span className="k mono" style={{ fontSize: 10.5, color: "var(--text-mute)" }}>
                    {src.replace(/_/g, " ")}
                  </span>
                  <span className="v mono" style={{ fontSize: 10.5, color: "var(--c-teal)" }}>
                    {src === "audit_composite"
                      ? `${r.audit_scores} score${r.audit_scores === 1 ? "" : "s"}`
                      : `${(r.labels_by_source || {})[src] || 0} label${
                          ((r.labels_by_source || {})[src] || 0) === 1 ? "" : "s"}`}
                  </span>
                </div>
                <div className="df-note" style={{ border: "none", padding: "2px 0 0" }}>{meaning}</div>
              </div>
            ))}
            {f.structural_gap && (
              <div className="df-note" style={{ marginTop: 4, color: "var(--warn)",
                borderColor: "rgba(255,185,95,.3)" }}>
                {f.structural_gap}
              </div>
            )}
            {f.to_fix && (
              <div className="df-note" style={{ marginTop: 8 }}>
                <b style={{ color: "var(--text-mute)" }}>To fix: </b>{f.to_fix}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* The one real paired dataset — captioned as a DIFFERENT quantity. */}
      {k.available && (
        <div className="card">
          <div className="card-head">
            <h3><Users size={14} />Kapture: machine vs human, n={Number(k.n).toLocaleString("en-IN")}</h3>
            <span className="mono" style={{ fontSize: 9.5, color: "var(--c-amber)" }}>
              a different quantity
            </span>
          </div>
          <div style={{ padding: "13px 15px" }}>
            <div className="df-note" style={{ marginBottom: 11, borderColor: "rgba(255,185,95,.3)",
              color: "var(--text-mute)" }}>
              Measures <b style={{ color: "var(--text)" }}>{k.measures}</b> — <i>not</i> {k.not_measures}.
              Different population, no join to the concern log. Included because it is the only
              place in this system where a machine score and a human verdict sit on the same row.
            </div>
            <div className="df-tiles" style={{ marginBottom: 12 }}>
              <div className="df-tile"><div className="lab">raw agreement</div>
                <div className="val">{k.agreement_pct}%</div>
                <div className="sub">looks strong</div></div>
              <div className="df-tile" style={{ borderTop: "2px solid var(--c-rose)" }}>
                <div className="lab">Cohen&apos;s κ</div>
                <div className="val" style={{ color: "var(--c-rose)" }}>{k.cohen_kappa}</div>
                <div className="sub">is not</div></div>
              <div className="df-tile"><div className="lab">engine fail rate</div>
                <div className="val">{k.engine_fail_rate}%</div><div className="sub">of tickets</div></div>
              <div className="df-tile"><div className="lab">human fail rate</div>
                <div className="val">{k.human_fail_rate}%</div><div className="sub">of tickets</div></div>
            </div>
            <div className="df-note" style={{ borderColor: "var(--c-rose)", color: "var(--text-mute)" }}>
              {k.kappa_reading}
            </div>
            {(k.paired_sample || []).length > 0 && (
              <div style={{ marginTop: 11 }}>
                <button className="chip" onClick={() => setShowPairs((v) => !v)}
                  style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                  <Table2 size={12} />{showPairs ? "Hide" : "Show"} {k.paired_sample.length} disagreements
                </button>
                {showPairs && (
                  <div style={{ marginTop: 9, overflowX: "auto" }}>
                    <table className="df-table">
                      <thead><tr><th>ticket</th><th>engine</th><th>human</th>
                        <th style={{ textAlign: "right" }}>engine quality</th></tr></thead>
                      <tbody>
                        {k.paired_sample.map((s, i) => (
                          <tr key={i}>
                            <td className="mono">{s.ticket}</td>
                            <td style={{ color: s.engine === "FAIL" ? "var(--c-rose)" : "var(--c-teal)" }}>
                              {s.engine}</td>
                            <td style={{ color: s.human === "FAIL" ? "var(--c-rose)" : "var(--c-teal)" }}>
                              {s.human}</td>
                            <td className="n">{s.engine_quality_pct}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
