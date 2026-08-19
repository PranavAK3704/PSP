import { useEffect, useMemo, useState } from "react";
import { Database, Ticket, Receipt, AlertTriangle, Table2 } from "lucide-react";
import { getDataFoundation } from "../lib/api.js";

// ── Data Foundation ──────────────────────────────────────────────────────────
// What the platform actually runs on, both databases, CORPUS-LEVEL ONLY.
//
// The deliberate constraint: no partner id, no AWB, no per-captain drill-down anywhere on this
// screen. That is the lesson from the Captain-360 panel this replaces — it listed every captain
// behind a picker, which made it unshippable inside a captain-facing widget. Aggregates carry
// the same "we run on real data" argument with none of that exposure.
//
// Forms follow the data's job, not decoration: headline counts are stat tiles (no plot needed),
// category comparisons are horizontal bars in ONE hue (length carries the comparison), the
// lifecycle is parts-of-a-whole so it is a single stacked bar with a labelled legend, and SLA is
// one proportion so it is a meter. Every row is directly labelled, so colour is never the only
// encoding — which is also what relieves the validator's contrast warning on the violet step.

const n0 = (v) => Number(v || 0).toLocaleString("en-IN");
const inr = (v) => "₹" + Number(v || 0).toLocaleString("en-IN");

function Tile({ icon: Icon, label, value, sub }) {
  return (
    <div className="df-tile">
      <div className="lab"><Icon size={12} />{label}</div>
      <div className="val">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

/* Horizontal magnitude bars. Sorted descending — an unsorted category axis makes the reader do
   the ranking themselves. `hue` is one colour for the whole chart: these compare ONE measure
   across categories, so per-bar colours would imply an identity that isn't there. */
function Bars({ data, hue, fmt = n0, max: maxProp }) {
  const rows = useMemo(
    () => Object.entries(data || {}).filter(([, v]) => Number(v) > 0).sort((a, b) => b[1] - a[1]),
    [data]);
  if (!rows.length) return <div className="df-note" style={{ border: "none" }}>No rows.</div>;
  const max = maxProp || Math.max(...rows.map(([, v]) => Number(v) || 0)) || 1;
  return (
    <div style={{ "--df-hue": hue }}>
      {rows.map(([k, v]) => (
        <div className="df-row" key={k}>
          <span className="k" title={k}>{k}</span>
          <span className="df-bar"><i style={{ width: `${Math.max(1.5, (v / max) * 100)}%` }} /></span>
          <span className="v">{fmt(v)}</span>
        </div>
      ))}
    </div>
  );
}

// Status hues are RESERVED — they mean state, and are never reused as "series 4".
const LIFECYCLE_HUE = {
  SUCCEEDED: "var(--c-teal)", PENDING: "var(--c-amber)",
  FAILED: "var(--c-rose)", REVERSED: "var(--c-violet)",
};
const LIFECYCLE_WORD = {
  SUCCEEDED: "recovered", PENDING: "open", FAILED: "failed", REVERSED: "reversed",
};

function Lifecycle({ counts, money }) {
  const rows = useMemo(
    () => Object.entries(counts || {}).filter(([, v]) => Number(v) > 0).sort((a, b) => b[1] - a[1]),
    [counts]);
  const total = rows.reduce((s, [, v]) => s + Number(v), 0);
  if (!total) return <div className="df-note" style={{ border: "none" }}>No lifecycle rows.</div>;
  return (
    <>
      <div className="df-stack" role="img"
        aria-label={rows.map(([k, v]) => `${k} ${v}`).join(", ")}>
        {rows.map(([k, v]) => (
          <span key={k} title={`${k} · ${n0(v)}`}
            style={{ width: `${(v / total) * 100}%`,
              background: LIFECYCLE_HUE[k] || "var(--c-track)" }} />
        ))}
      </div>
      {/* Legend is always present for ≥2 series, and carries the number — so the stack never
          has to be decoded by colour alone. */}
      <div className="df-legend">
        {rows.map(([k, v]) => (
          <span className="li" key={k}>
            <span className="sw" style={{ background: LIFECYCLE_HUE[k] || "var(--c-track)" }} />
            {LIFECYCLE_WORD[k] || k.toLowerCase()} <b>{n0(v)}</b>
            {money?.[k] != null && <span style={{ color: "var(--text-faint)" }}>· {inr(money[k])}</span>}
          </span>
        ))}
      </div>
    </>
  );
}

function Card({ title, icon: Icon, hue, children, foot }) {
  return (
    <div className={`card ${hue}`} style={{ display: "flex", flexDirection: "column" }}>
      <div className="card-head"><h3><Icon size={14} />{title}</h3></div>
      <div style={{ padding: "14px 16px", flex: 1 }}>{children}</div>
      {foot && <div className="df-note" style={{ margin: 0, padding: "10px 16px 13px" }}>{foot}</div>}
    </div>
  );
}

export default function DataFoundation() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const [showTable, setShowTable] = useState(false);

  useEffect(() => {
    getDataFoundation().then(setD)
      .catch(() => setErr("Could not load the data foundation."));
  }, []);

  if (err) {
    return (
      <div className="mono" style={{ fontSize: 12, color: "var(--warn)", background: "var(--warn-soft)",
        border: "1px solid rgba(255,185,95,0.3)", borderRadius: 8, padding: "13px 15px" }}>{err}</div>
    );
  }
  if (!d) return <div className="mono" style={{ fontSize: 12, color: "var(--text-faint)", padding: 18 }}>Reading both databases…</div>;

  const L = d.losses || {}, T = d.tickets || {};
  const lossRows = L.tables?.losses, attrRows = L.tables?.attribution, qcRows = L.tables?.qc_fail;
  const slaPct = T.sla?.within_pct;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Headline counts — no plot earns its place here; these are single numbers. */}
      <div className="df-tiles">
        <Tile icon={Ticket} label="Support tickets" value={n0(T.total)}
          sub={T.available ? `source: ${T.source}` : "tickets.db absent"} />
        <Tile icon={Receipt} label="Loss rows" value={n0(lossRows)}
          sub={`attribution ${n0(attrRows)}`} />
        <Tile icon={AlertTriangle} label="QC-fail evidence" value={n0(qcRows)} sub="rows" />
        <Tile icon={Database} label="Partners · hubs" value={`${n0(L.partners)} · ${n0(L.hubs)}`}
          sub="distinct in the ledger" />
      </div>

      <div className="df-grid">
        <Card title="Ticket landscape" icon={Ticket} hue="hue-teal"
          foot={`Where partners actually ask. ${T.by_source?.WhatsApp ? "WhatsApp dominates, and it is the channel that carries no structured fields — which is why identifiers have to be parsed out of free text." : ""}`}>
          {slaPct != null && (
            <div style={{ marginBottom: 14 }}>
              <div className="df-row" style={{ gridTemplateColumns: "1fr auto", padding: "0 0 6px" }}>
                <span className="k">Within SLA</span><span className="v">{slaPct}%</span>
              </div>
              <div className="df-meter"><i style={{ width: `${Math.min(100, slaPct)}%` }} /></div>
              <div className="sub mono" style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 5 }}>
                {n0(T.sla?.within)} within · {n0(T.sla?.breached)} breached
                {T.avg_resolution_hours ? ` · avg ${T.avg_resolution_hours}h to resolve` : ""}
              </div>
            </div>
          )}
          <div className="lab mono" style={{ fontSize: 9.5, letterSpacing: ".12em", textTransform: "uppercase",
            color: "var(--text-faint)", marginBottom: 4 }}>By channel</div>
          <Bars data={T.by_source} hue="var(--c-teal)" />
          <div className="lab mono" style={{ fontSize: 9.5, letterSpacing: ".12em", textTransform: "uppercase",
            color: "var(--text-faint)", margin: "12px 0 4px" }}>By status</div>
          <Bars data={T.by_status} hue="var(--c-teal)" />
        </Card>

        <Card title="Loss ledger" icon={Receipt} hue="hue-warn"
          foot={`Reversal rate ${L.reversal_rate_pct ?? 0}% (${n0(L.reversals)} of ${n0(attrRows)}). The engine reads these same rows per-AWB when it decides a disputed debit.`}>
          <div className="lab mono" style={{ fontSize: 9.5, letterSpacing: ".12em", textTransform: "uppercase",
            color: "var(--text-faint)", marginBottom: 4 }}>Money lifecycle</div>
          <Lifecycle counts={L.lifecycle} money={L.money} />
          <div className="lab mono" style={{ fontSize: 9.5, letterSpacing: ".12em", textTransform: "uppercase",
            color: "var(--text-faint)", margin: "14px 0 4px" }}>By loss type</div>
          <Bars data={L.dispositions} hue="var(--c-amber)" />
        </Card>

        {d.top_hubs?.length > 0 && (
          <Card title="Busiest hubs by ticket volume" icon={Database} hue="hue-teal"
            foot="Hub codes only — no partner is identifiable from this view.">
            <Bars hue="var(--c-teal)"
              data={Object.fromEntries(d.top_hubs.slice(0, 10)
                .map((h) => [h.hub || h.hub_code || "—", h.tickets ?? h.n ?? h.count ?? 0]))} />
          </Card>
        )}
      </div>

      {/* A table view is required relief for the validator's contrast warning, and it is how
          anyone gets exact figures without reading a bar. */}
      <div>
        <button className="chip" onClick={() => setShowTable((v) => !v)}
          style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Table2 size={12} />{showTable ? "Hide" : "Show"} figures as a table
        </button>
        {showTable && (
          <div style={{ marginTop: 10, border: "1px solid var(--line)", borderRadius: 10, overflow: "hidden" }}>
            <div style={{ overflowX: "auto" }}>
              <table className="df-table">
                <thead><tr><th>Dataset</th><th>Measure</th><th style={{ textAlign: "right" }}>Value</th></tr></thead>
                <tbody>
                  {[
                    ["tickets.db", "total tickets", n0(T.total)],
                    ["tickets.db", "within SLA", `${slaPct ?? "—"}%`],
                    ...Object.entries(T.by_source || {}).map(([k, v]) => ["tickets.db", `channel · ${k}`, n0(v)]),
                    ...Object.entries(T.by_status || {}).map(([k, v]) => ["tickets.db", `status · ${k}`, n0(v)]),
                    ["valmo.db", "loss rows", n0(lossRows)],
                    ["valmo.db", "attribution rows", n0(attrRows)],
                    ["valmo.db", "qc_fail rows", n0(qcRows)],
                    ["valmo.db", "distinct partners", n0(L.partners)],
                    ["valmo.db", "distinct hubs", n0(L.hubs)],
                    ["valmo.db", "reversal rate", `${L.reversal_rate_pct ?? 0}%`],
                    ...Object.entries(L.lifecycle || {}).map(([k, v]) => ["valmo.db", `lifecycle · ${k}`, n0(v)]),
                    ...Object.entries(L.money || {}).map(([k, v]) => ["valmo.db", `money · ${k}`, inr(v)]),
                    ...Object.entries(L.dispositions || {}).map(([k, v]) => ["valmo.db", `loss type · ${k}`, n0(v)]),
                  ].map(([a, b, c], i) => (
                    <tr key={i}><td>{a}</td><td>{b}</td><td className="n">{c}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <div className="df-note">
        Engine account provider: <b style={{ color: "var(--text-mute)" }}>{d.engine_provider}</b>.
        Aggregates only — this screen carries no partner id, AWB or per-captain breakdown by design.
      </div>
    </div>
  );
}
