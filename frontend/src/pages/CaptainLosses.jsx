import { useEffect, useMemo, useState } from "react";
import { Receipt, RotateCcw, Wallet, Clock, CheckCircle2, AlertTriangle,
  Layers, Hash, Database, ChevronRight } from "lucide-react";
import { getDemoCaptains, getCaptainLosses } from "../lib/api.js";

// ── Captain 360 · Losses & Debits ────────────────────────────────────────────
// Reads the REAL loss-attribution ledger (valmo.db) per captain — not the seeded
// chat captains. This is the "we run on real data" proof screen: a million loss
// rows, 10k attributions, with the reversal state (attribution_state) the live LMS
// API omits but Metabase exposes.

const inr = (n) => "₹" + Number(n || 0).toLocaleString("en-IN");

// lifecycle word (from vetan payout status) → tone tokens
const TONE = {
  pending:   { c: "var(--warn)", bg: "var(--warn-soft)", b: "rgba(255,185,95,0.30)", label: "Pending" },
  recovered: { c: "var(--signal)", bg: "var(--signal-soft)", b: "var(--signal-line)", label: "Recovered" },
  reversed:  { c: "var(--good)", bg: "var(--good-soft)", b: "rgba(78,222,163,0.30)", label: "Reversed" },
  failed:    { c: "var(--bad)", bg: "var(--bad-soft)", b: "rgba(255,180,171,0.30)", label: "Failed" },
  unknown:   { c: "var(--text-faint)", bg: "var(--surface-2)", b: "var(--line)", label: "—" },
};

const DISP_LABEL = {
  hardstop_loss: "Hardstop", shipment_shortage: "Shipment shortage",
  bag_shortage: "Bag shortage", intransit: "In transit", others: "Other",
};

function Pill({ lifecycle, reversal }) {
  const t = TONE[lifecycle] || TONE.unknown;
  return (
    <span className="mono" style={{ display: "inline-flex", alignItems: "center", gap: 5,
      fontSize: 10.5, fontWeight: 700, padding: "3px 9px", borderRadius: 999,
      color: t.c, background: t.bg, border: `1px solid ${t.b}`, whiteSpace: "nowrap" }}>
      {reversal ? <RotateCcw size={11} /> : null}
      {reversal ? "Reversal" : t.label}
    </span>
  );
}

function Tile({ icon: Icon, label, value, tone = "var(--text)", sub }) {
  return (
    <div style={{ flex: "1 1 130px", minWidth: 130, background: "var(--surface-2)",
      border: "1px solid var(--line)", borderRadius: 10, padding: "13px 15px" }}>
      <div className="mono" style={{ fontSize: 9.5, letterSpacing: ".12em", textTransform: "uppercase",
        color: "var(--text-faint)", display: "flex", alignItems: "center", gap: 6 }}>
        <Icon size={12} />{label}
      </div>
      <div style={{ fontSize: 21, fontWeight: 800, marginTop: 6, color: tone,
        fontVariantNumeric: "tabular-nums" }}>{value}</div>
      {sub && <div className="mono" style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

export default function CaptainLosses() {
  const [captains, setCaptains] = useState([]);
  const [sel, setSel] = useState(null);
  const [data, setData] = useState(null);       // { summary, losses, source }
  const [loading, setLoading] = useState(false);
  const [source, setSource] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => {
    getDemoCaptains().then((d) => {
      setCaptains(d.captains || []);
      setSource(d.source || "");
      if (d.captains?.length) setSel(d.captains[0].partner_id);
      else setErr("No loss ledger available — valmo.db not found (source: " + (d.source || "none") + ").");
    }).catch(() => setErr("Could not load captains."));
  }, []);

  useEffect(() => {
    if (!sel) return;
    setLoading(true); setErr("");
    getCaptainLosses(sel)
      .then((d) => setData(d))
      .catch(() => setErr("Could not load losses for " + sel))
      .finally(() => setLoading(false));
  }, [sel]);

  const s = data?.summary;
  const losses = data?.losses || [];
  const dispEntries = useMemo(
    () => Object.entries(s?.by_disposition || {}).sort((a, b) => b[1] - a[1]),
    [s]);

  return (
    <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 288px) minmax(0, 1fr)",
      gap: 20, height: "100%" }} className="c360">
      {/* ── LEFT: real captains, ranked for demo interest ── */}
      <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div className="card-head">
          <h3><Database size={14} /> Captains · real ledger</h3>
        </div>
        <div className="mono" style={{ fontSize: 10, color: "var(--text-faint)", padding: "10px 16px 4px" }}>
          {captains.length} captains · loss-attribution ledger
        </div>
        <div style={{ overflowY: "auto", padding: "4px 10px 12px" }}>
          {captains.map((c) => {
            const active = c.partner_id === sel;
            return (
              <button key={c.partner_id} onClick={() => setSel(c.partner_id)}
                style={{ width: "100%", textAlign: "left", marginBottom: 6, padding: "11px 12px",
                  borderRadius: 9, cursor: "pointer",
                  background: active ? "var(--signal-soft)" : "var(--surface-2)",
                  border: `1px solid ${active ? "var(--signal-line)" : "var(--line)"}` }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <span className="mono" style={{ fontSize: 12.5, fontWeight: 700,
                    color: active ? "var(--signal)" : "var(--text)" }}>{c.partner_id}</span>
                  {c.reversals > 0 && (
                    <span className="mono" style={{ display: "inline-flex", alignItems: "center", gap: 3,
                      fontSize: 9, fontWeight: 700, color: "var(--good)", background: "var(--good-soft)",
                      border: "1px solid rgba(78,222,163,0.30)", borderRadius: 999, padding: "2px 7px" }}>
                      <RotateCcw size={9} />{c.reversals}
                    </span>
                  )}
                </div>
                <div className="mono" style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 4,
                  display: "flex", gap: 10, flexWrap: "wrap" }}>
                  <span>{c.count} debits</span>
                  <span>{inr(c.total_inr)}</span>
                  {c.pending > 0 && <span style={{ color: "var(--warn)" }}>{c.pending} pending</span>}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* ── RIGHT: the selected captain's real exposure ── */}
      <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div className="card-head" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h3><Receipt size={14} /> {sel ? <span className="mono">{sel}</span> : "Losses & Debits"}</h3>
          <span className="mono" style={{ fontSize: 9.5, letterSpacing: ".1em", textTransform: "uppercase",
            color: source === "remote" ? "var(--good)" : "var(--text-faint)",
            display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span className="dot" style={{ color: source === "remote" ? "var(--good)" : "var(--signal)" }} />
            {source === "remote" ? "Turso (live)" : source === "local" ? "valmo.db (local)" : "no source"}
          </span>
        </div>

        <div style={{ overflowY: "auto", padding: 18 }}>
          {err && (
            <div className="mono" style={{ fontSize: 12, color: "var(--warn)", background: "var(--warn-soft)",
              border: "1px solid rgba(255,185,95,0.30)", borderRadius: 8, padding: "12px 14px" }}>{err}</div>
          )}

          {s && !err && (
            <>
              {/* summary tiles — honest money split */}
              <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginBottom: 16 }}>
                <Tile icon={Hash} label="Debits" value={s.count} />
                <Tile icon={Wallet} label="Total debited" value={inr(s.total_inr)} />
                <Tile icon={CheckCircle2} label="Recovered" value={inr(s.recovered_inr)} tone="var(--signal)"
                  sub={(s.by_status?.SUCCEEDED || 0) + " settled"} />
                <Tile icon={Clock} label="Pending" value={inr(s.pending_inr)} tone="var(--warn)"
                  sub={(s.by_status?.PENDING || 0) + " open"} />
                <Tile icon={RotateCcw} label="Reversed" value={inr(s.reversed_inr)} tone="var(--good)"
                  sub={s.reversals + " reversal" + (s.reversals === 1 ? "" : "s")} />
              </div>

              {/* disposition breakdown */}
              {dispEntries.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 7, marginBottom: 16, alignItems: "center" }}>
                  <span className="mono" style={{ fontSize: 9.5, letterSpacing: ".12em", textTransform: "uppercase",
                    color: "var(--text-faint)", display: "inline-flex", alignItems: "center", gap: 6 }}>
                    <Layers size={12} />Disposition</span>
                  {dispEntries.map(([d, n]) => (
                    <span key={d} className="chip" style={{ cursor: "default", fontSize: 11 }}>
                      {DISP_LABEL[d] || d} · {n}
                    </span>
                  ))}
                </div>
              )}

              {/* the real ledger */}
              <div style={{ border: "1px solid var(--line)", borderRadius: 10, overflow: "hidden" }}>
                <div style={{ overflowX: "auto" }}>
                  <table className="mono" style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                    <thead>
                      <tr style={{ background: "var(--surface-2)", color: "var(--text-faint)",
                        textAlign: "left", letterSpacing: ".08em", textTransform: "uppercase", fontSize: 9.5 }}>
                        <th style={{ padding: "10px 12px", fontWeight: 700 }}>AWB</th>
                        <th style={{ padding: "10px 12px", fontWeight: 700 }}>Disposition</th>
                        <th style={{ padding: "10px 12px", fontWeight: 700, textAlign: "right" }}>Amount</th>
                        <th style={{ padding: "10px 12px", fontWeight: 700 }}>Status</th>
                        <th style={{ padding: "10px 12px", fontWeight: 700 }}>Attribution state</th>
                        <th style={{ padding: "10px 12px", fontWeight: 700 }}>CN / DN</th>
                        <th style={{ padding: "10px 12px", fontWeight: 700 }}>Date</th>
                      </tr>
                    </thead>
                    <tbody>
                      {losses.map((l, i) => (
                        <tr key={l.awb + i} style={{ borderTop: "1px solid var(--line-soft)",
                          background: l.is_reversal ? "rgba(78,222,163,0.05)" : "transparent" }}>
                          <td style={{ padding: "10px 12px", color: "var(--text)", whiteSpace: "nowrap" }}>{l.awb}</td>
                          <td style={{ padding: "10px 12px", color: "var(--text-mute)" }}>
                            {DISP_LABEL[l.disposition] || l.disposition}
                            {l.reason ? <span style={{ color: "var(--text-faint)" }}> · {l.reason}</span> : null}
                          </td>
                          <td style={{ padding: "10px 12px", textAlign: "right", color: "var(--text)",
                            fontVariantNumeric: "tabular-nums" }}>{inr(l.amount_inr)}</td>
                          <td style={{ padding: "10px 12px" }}><Pill lifecycle={l.lifecycle} reversal={l.is_reversal} /></td>
                          <td style={{ padding: "10px 12px", color: "var(--text-faint)", whiteSpace: "nowrap" }}>{l.attribution_state || "—"}</td>
                          <td style={{ padding: "10px 12px", color: "var(--text-faint)", whiteSpace: "nowrap" }}>{l.cn_number || l.dn_number || "—"}</td>
                          <td style={{ padding: "10px 12px", color: "var(--text-faint)", whiteSpace: "nowrap" }}>{l.attribution_date || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="mono" style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 12,
                display: "flex", alignItems: "center", gap: 6 }}>
                <ChevronRight size={11} />
                <span><b style={{ color: "var(--text-mute)" }}>attribution_state</b> — the reversal-trail field the live LMS API omits (6 of 40 columns); Metabase exposes it. This is real ledger data, not seed.</span>
              </div>
            </>
          )}

          {loading && !s && (
            <div className="mono" style={{ fontSize: 12, color: "var(--text-faint)", padding: 20 }}>Loading ledger…</div>
          )}
        </div>
      </div>
    </div>
  );
}
