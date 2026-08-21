import { useEffect, useMemo, useRef, useState } from "react";
import { BarChart3, Send, Sparkles, TriangleAlert, Info, ExternalLink } from "lucide-react";
import { getGrowthIndex, getGrowth, stream } from "../lib/api.js";

/* ── Growth Dashboard + docked support widget ─────────────────────────────────────────────
   THE POINT OF THIS SCREEN, in one sentence: the captain is looking at their own dashboard,
   does not understand it, types "why is my load low" into the widget, and gets an answer
   computed from THE SAME PAYLOAD the dashboard is drawing. Same data, two presentations.

   Deliberately NOT a pixel copy of valmo-partner-webview. @meesho/merlin-ui is a private
   package and cannot be installed, so this is built from PSP's own tokens and the df-* classes
   from DataFoundation.jsx. What IS copied verbatim is the label vocabulary
   (GROWTH_DASHBOARD_STRINGS) and the metric titles, because a captain recognises the words —
   an approximation of the layout with invented labels would read as a mock-up of something
   else.

   The widget DOCKS; it does not replace. Both have to be visible at once or the demo beat
   ("same data, two presentations") is a claim rather than a demonstration. ── */

// Verbatim from growth-dashboard/constants.ts — GROWTH_DASHBOARD_STRINGS.
const S = {
  PAGE_TITLE: "Growth Dashboard",
  YOUR_METRICS: "Your Metrics",
  YOUR_ORDERS_SUBTITLE: "Your Orders (Out of Max Potential):",
  CURRENT: "Current",
  TARGET: "Target",
  YOUR_ORDER_SUMMARY: "Your Order Summary",
  ORDER_SUMMARY_SUBTITLE: "View the reasons for missing out extra orders",
  GRAPH_DATE: "Graph Date:",
  MAXIMUM_POTENTIAL: "Maximum\nPotential",
  ORDERS_MISSED_ALLOCATION: "Orders Missed\nin Allocation",
  CURRENT_ELIGIBLE: "Current\nEligible",
  ORDERS_MISSED_CAPACITY: "Orders Missed in\nCapacity Cut",
  EXTRA_ORDERS: "Extra\nOrders",
  FINAL_MANIFESTED: "Final Manifested\nOrders",
};

// The four levers, in the panel's own order, with its own titles. `higherIsBetter` matters:
// day0-attempt is the ONLY one where a bigger number is better, and inverting it would tell a
// captain at 94% against a 70% target that they are failing.
const LEVERS = [
  { key: "pilot_rate_card", title: "Pilot Rate Card (CPS)", higherIsBetter: false },
  { key: "rto_performance", title: "RTO Performance", higherIsBetter: false },
  { key: "day0_attempt", title: "Day-0 Attempt %", higherIsBetter: true },
  { key: "pendency", title: "Pendency (DOH)", higherIsBetter: false },
];

// METRIC_TARGET_TOOLTIPS, verbatim. This is the authored explanation of WHY each target is
// what it is — a captain has to find and open a tooltip to read it, which is precisely the gap
// the widget closes by saying it out loud.
const WHY = {
  pilot_rate_card: "The target rate card is calculated based on your neighbouring DCs rate",
  rto_performance: "The target RTO is computed based on the best 3PL in your area",
  day0_attempt: "The target is to increase your delivery attempt, keep your performance above 70% to avoid capacity cut",
  pendency: "Target is set to avoid shipment pendency, please clear pendencies within 2.5 days to avoid capacity cut",
};

const num = (v) => {
  if (typeof v === "number") return v;
  const m = String(v ?? "").replace(/,/g, "").match(/-?\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : null;
};
// Mirrors the engine's Tri: null when unreadable, so an unparseable value is never styled as
// passing. The panel's own parseNumeric returns 0 here, which for a lower-is-better metric
// renders as good — see backend adapters/growth/contract.py for why we diverge.
const statusOf = (cur, tgt, higherIsBetter) => {
  const c = num(cur), t = num(tgt);
  if (c === null || t === null) return null;
  return (higherIsBetter ? c >= t : c <= t) ? "good" : "not_good";
};
const n0 = (v) => Number(v || 0).toLocaleString("en-IN");
const inr = (v) => "₹" + Number(v || 0).toLocaleString("en-IN");

/* ── The waterfall. Five bars, and bar 4 changes MEANING with its sign: positive is extra
   orders won (green), negative is orders lost to a capacity cut (rose). Rendering the
   magnitude without the sign would show a capacity cut as a win. ── */
function Waterfall({ os }) {
  const bars = useMemo(() => {
    const b4 = Number(os.bar4_value || 0);
    return [
      { label: S.MAXIMUM_POTENTIAL, value: os.max_potential, tone: "var(--c-teal)" },
      { label: S.ORDERS_MISSED_ALLOCATION, value: os.missed_in_allocation, tone: "var(--c-amber)" },
      { label: S.CURRENT_ELIGIBLE, value: os.current_eligible, tone: "var(--c-teal)" },
      { label: b4 >= 0 ? S.EXTRA_ORDERS : S.ORDERS_MISSED_CAPACITY, value: Math.abs(b4),
        tone: b4 >= 0 ? "var(--c-teal)" : "var(--c-rose)", signed: b4 },
      { label: S.FINAL_MANIFESTED, value: os.final_manifested, tone: "var(--c-violet)" },
    ];
  }, [os]);
  const max = Math.max(...bars.map((b) => Number(b.value) || 0), 1);

  return (
    <div style={{ display: "flex", alignItems: "flex-end", gap: 10, height: 190, marginTop: 6 }}>
      {bars.map((b, i) => (
        <div key={i} style={{ flex: 1, display: "flex", flexDirection: "column",
          alignItems: "center", justifyContent: "flex-end", height: "100%", minWidth: 0 }}>
          <div className="mono" style={{ fontSize: 12, fontWeight: 800, color: "var(--text)",
            marginBottom: 4, fontVariantNumeric: "tabular-nums" }}>
            {b.signed != null && b.signed !== 0 ? (b.signed > 0 ? "+" : "−") : ""}{n0(b.value)}
          </div>
          {/* 2px surface gap between fills, 4px rounded data-end anchored to the baseline. */}
          <div style={{ width: "100%", height: `${Math.max(4, (Number(b.value) || 0) / max * 128)}px`,
            background: b.tone, borderRadius: "4px 4px 0 0", boxShadow: "0 0 0 2px var(--surface-1)" }} />
          <div className="mono" style={{ fontSize: 9, color: "var(--text-faint)", marginTop: 7,
            textAlign: "center", whiteSpace: "pre-line", lineHeight: 1.35 }}>{b.label}</div>
        </div>
      ))}
    </div>
  );
}

function MetricCard({ title, current, target, status, why }) {
  const tone = status === "good" ? "var(--c-teal)"
    : status === "not_good" ? "var(--c-rose)" : "var(--text-faint)";
  return (
    <div className="df-tile" style={{ borderTop: `2px solid ${tone}` }} title={why}>
      <div className="lab" style={{ display: "flex", alignItems: "center", gap: 4 }}>
        {title}<Info size={9} style={{ opacity: 0.5 }} />
      </div>
      <div className="val" style={{ fontSize: 18, color: tone }}>{current ?? "—"}</div>
      <div className="sub">
        {S.TARGET} {target ?? "—"}
        {status === null && <span style={{ color: "var(--warn)" }}> · unreadable</span>}
      </div>
    </div>
  );
}

function MetricRow({ m }) {
  const tone = m.status === "good" ? "var(--c-teal)" : "var(--c-rose)";
  return (
    <div className="df-row" style={{ gridTemplateColumns: "1fr auto" }}>
      <span className="k" style={{ color: "var(--text-mute)" }}>
        {m.label} <b style={{ color: tone }}>{m.value}</b>
        {m.comparison && <span style={{ color: "var(--text-faint)" }}> — {m.comparison}</span>}
      </span>
      <span className="v" style={{ color: tone }}>{m.status === "good" ? "on target" : "off target"}</span>
    </div>
  );
}

/* ── The docked widget. Deliberately minimal — it posts to the SAME /api/chat the Captain
   Advocate uses and streams the same trace, but shows only what a captain would see plus the
   two things that make the point on stage: which named query ran, and what the engine decided.
   Not a second chat implementation; a second VIEW of the one that exists. ── */
function SupportWidget({ hub, partnerId }) {
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [steps, setSteps] = useState([]);
  const convRef = useRef(null);
  const endRef = useRef(null);

  useEffect(() => { setMsgs([]); setSteps([]); convRef.current = null; }, [hub, partnerId]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, steps]);

  async function send(text) {
    const msg = (text ?? input).trim();
    if (!msg || busy || !partnerId) return;
    setInput(""); setMsgs((m) => [...m, { who: "captain", text: msg }]);
    setSteps([]); setBusy(true);
    if (!convRef.current) convRef.current = crypto.randomUUID();
    await stream(
      { url: "/api/chat", method: "POST",
        body: { captain_id: partnerId, message: msg, conversation_id: convRef.current } },
      (ev) => {
        const d = ev.data || {};
        if (ev.node === "query" && d.query) setSteps((s) => [...s, { kind: "query", text: `named query: ${d.query}` }]);
        if (ev.node === "policy") setSteps((s) => [...s, { kind: "policy", text: `policy → ${d.action} · confidence ${d.confidence}` }]);
        if (ev.node === "gate") setSteps((s) => [...s, { kind: "gate", text: `trust gate ${d.passed ? "PASS" : "BLOCK"}${d.money_moving ? " · money-moving" : " · no money at stake"}` }]);
        if (ev.node === "act") setSteps((s) => [...s, { kind: d.simulated ? "warn" : "ok", text: ev.label }]);
        if (ev.node === "cost" && d.cost_usd != null) setSteps((s) => [...s, { kind: "cost", text: `turn cost $${Number(d.cost_usd).toFixed(4)} · ${d.calls} call(s)` }]);
        if (ev.node === "reply" && d.reply) {
          setMsgs((m) => [...m, { who: "psp", text: d.reply, failed: !!d.engine_error }]);
        }
      },
      () => setBusy(false));
  }

  const TONE = { query: "var(--c-teal)", policy: "var(--c-violet)", gate: "var(--c-amber)",
                 ok: "var(--c-teal)", warn: "var(--warn)", cost: "var(--text-faint)" };

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div className="card-head">
        <h3><Sparkles size={14} />Partner Support</h3>
        <span className="mono" style={{ fontSize: 9.5, color: "var(--text-faint)" }}>
          {partnerId ? `hub ${hub}` : "no partner mapped"}
        </span>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "12px 14px", display: "flex",
        flexDirection: "column", gap: 9, minHeight: 190 }}>
        {msgs.length === 0 && (
          <div className="df-note" style={{ border: "none", padding: 0 }}>
            The dashboard shows the numbers. Ask what they mean.
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
              {["why is my load low", "why did my capacity get cut", "how do I get more orders"].map((q) => (
                <button key={q} className="chip" onClick={() => send(q)} disabled={busy || !partnerId}>{q}</button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} style={{ alignSelf: m.who === "captain" ? "flex-end" : "flex-start",
            maxWidth: "88%", padding: "8px 11px", borderRadius: 10, fontSize: 12.5, lineHeight: 1.55,
            background: m.who === "captain" ? "var(--surface-3)"
              : m.failed ? "var(--warn-soft)" : "var(--surface-2)",
            border: `1px solid ${m.failed ? "rgba(255,185,95,.35)" : "var(--line-soft)"}`,
            color: "var(--text)" }}>
            {m.text}
          </div>
        ))}
        {steps.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 2 }}>
            {steps.map((s, i) => (
              <div key={i} className="mono" style={{ fontSize: 9.5, color: TONE[s.kind] || "var(--text-faint)" }}>
                ▸ {s.text}
              </div>
            ))}
          </div>
        )}
        {busy && <div className="mono" style={{ fontSize: 10, color: "var(--text-faint)" }}>thinking…</div>}
        <div ref={endRef} />
      </div>

      <div style={{ display: "flex", gap: 7, padding: "10px 12px", borderTop: "1px solid var(--line-soft)" }}>
        <input value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") send(); }}
          placeholder={partnerId ? "Ask about your load…" : "no partner mapped to this hub"}
          disabled={busy || !partnerId}
          style={{ flex: 1, background: "var(--surface-0)", border: "1px solid var(--line)",
            borderRadius: 8, padding: "8px 10px", color: "var(--text)", fontSize: 12.5,
            fontFamily: "inherit", outline: "none" }} />
        <button className="icon-btn" onClick={() => send()} disabled={busy || !partnerId}
          title="Send" style={{ width: 34, height: 34 }}><Send size={14} /></button>
      </div>
    </div>
  );
}

export default function GrowthDashboard() {
  const [index, setIndex] = useState(null);
  const [hub, setHub] = useState("");
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    getGrowthIndex().then((r) => {
      setIndex(r);
      // Default to an allocation-miss hub if one is present — that is the demo's main path.
      setHub((r.hubs || []).includes("LZ5") ? "LZ5" : (r.hubs || [])[0] || "");
    }).catch(() => setErr("Could not read the growth index."));
  }, []);

  useEffect(() => {
    if (!hub) return;
    setData(null);
    getGrowth(hub).then(setData).catch(() => setErr(`No growth data for hub ${hub}.`));
  }, [hub]);

  if (err) {
    return <div className="df-note" style={{ color: "var(--warn)", background: "var(--warn-soft)",
      border: "1px solid rgba(255,185,95,.3)", padding: "13px 15px" }}>{err}</div>;
  }
  if (!index || !data) {
    return <div className="mono" style={{ fontSize: 12, color: "var(--text-faint)", padding: 18 }}>
      Reading the growth dashboard…</div>;
  }

  const ym = data.your_metrics || {};
  const os = data.order_summary || {};
  const rp = os.right_panel || {};
  const partnerId = (index.partners || {})[hub];
  const levers = LEVERS.map((l) => {
    const raw = ym[l.key] || {};
    return { ...l, current: raw.current, target: raw.target,
             status: statusOf(raw.current, raw.target, l.higherIsBetter), why: WHY[l.key] };
  });
  const bannerTone = { green: "var(--c-teal)", grey: "var(--text-faint)",
                       red: "var(--c-rose)", red_capacity_cut: "var(--c-rose)" }[os.banner_type]
                     || "var(--text-faint)";
  const totalMissed = (rp.allocation_miss?.count || 0) + (rp.capacity_loss?.count || 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 13 }}>
      {/* Provenance FIRST and unmissable. Everything below is fixture data shaped to the real
          contract; a viewer must know that before they read a single number. */}
      <div className="df-note" style={{ display: "flex", alignItems: "center", gap: 10,
        flexWrap: "wrap", borderColor: "rgba(255,185,95,.3)", background: "var(--warn-soft)",
        color: "var(--warn)", padding: "9px 12px" }}>
        <TriangleAlert size={12} />
        <b>{data.source}</b>
        <span style={{ color: "var(--text-faint)" }}>
          — shaped to the captain panel's real contract, served from a file. Live is one env var
          ({data.mode === "fixture" ? "PSP_GROWTH_SOURCE=live" : data.mode}) away.
        </span>
        {Object.values(data.endpoints || {}).map((u) => (
          <span key={u} className="tag mono" style={{ fontSize: 9 }}>
            <ExternalLink size={8} style={{ marginRight: 3 }} />GET {u}
          </span>
        ))}
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 16, display: "flex", alignItems: "center", gap: 7 }}>
          <BarChart3 size={16} />{S.PAGE_TITLE}
        </h2>
        <div style={{ display: "flex", gap: 5 }}>
          {(index.hubs || []).map((h) => (
            <button key={h} className="chip" onClick={() => setHub(h)}
              style={h === hub ? { borderColor: "var(--c-teal)", color: "var(--c-teal)" } : undefined}>
              {h}
            </button>
          ))}
        </div>
        <span className="mono" style={{ fontSize: 10, color: "var(--text-faint)", marginLeft: "auto" }}>
          {S.GRAPH_DATE} {os.start_date} – {os.end_date}
        </span>
      </div>

      {/* Dashboard on the left, widget docked on the right. Side by side is the whole point:
          one payload, two presentations, both on screen at once. */}
      <div style={{ display: "grid", gap: 13, gridTemplateColumns: "minmax(0, 1.55fr) minmax(320px, 1fr)",
        alignItems: "start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 13, minWidth: 0 }}>
          <div className="card">
            <div className="card-head"><h3>{S.YOUR_METRICS}</h3></div>
            <div style={{ padding: "13px 15px" }}>
              <div className="mono" style={{ fontSize: 11, color: "var(--text-mute)", marginBottom: 11 }}>
                {S.YOUR_ORDERS_SUBTITLE} <b style={{ color: "var(--text)" }}>
                  {n0(ym.current_orders)} / {n0(ym.max_potential)}</b>
                {ym.is_good === true && <span style={{ color: "var(--c-teal)" }}> · performing</span>}
                {ym.is_good === false && <span style={{ color: "var(--c-rose)" }}> · below target</span>}
              </div>
              <div className="df-tiles">
                {levers.map((l) => <MetricCard key={l.key} {...l} />)}
              </div>
              {/* order_change_text is a DISPLAY string — rendered as prose, never branched on. */}
              {ym.order_change_text && (
                <div className="df-note" style={{ border: "none", padding: "10px 0 0" }}>
                  {ym.order_change_text}
                </div>
              )}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <h3>{S.YOUR_ORDER_SUMMARY}</h3>
              <span className="mono" style={{ fontSize: 9.5, color: "var(--text-faint)" }}>
                {S.ORDER_SUMMARY_SUBTITLE}
              </span>
            </div>
            <div style={{ padding: "13px 15px" }}>
              <Waterfall os={os} />
              {os.financial_summary && (
                <div className="df-note" style={{ marginTop: 13, color: bannerTone,
                  borderColor: bannerTone, background: "var(--surface-2)" }}>
                  {os.financial_summary}
                  {os.extra_earnings_loss > 0 &&
                    <b style={{ marginLeft: 6 }}>{inr(os.extra_earnings_loss)} forgone</b>}
                </div>
              )}
            </div>
          </div>

          <div className="card">
            <div className="card-head">
              <h3>Missed Orders</h3>
              <span className="mono" style={{ fontSize: 9.5, color: "var(--text-faint)" }}>
                {n0(totalMissed)} total
              </span>
            </div>
            <div style={{ padding: "13px 15px", display: "flex", flexDirection: "column", gap: 14 }}>
              {[["allocation_miss", S.ORDERS_MISSED_ALLOCATION.replace("\n", " ")],
                ["capacity_loss", S.ORDERS_MISSED_CAPACITY.replace("\n", " ")],
              ].map(([key, label]) => {
                const sec = rp[key];
                if (!sec) return null;
                return (
                  <div key={key}>
                    <div className="lab mono" style={{ fontSize: 9.5, letterSpacing: ".12em",
                      textTransform: "uppercase", color: "var(--text-faint)", marginBottom: 5 }}>
                      {label} — {n0(sec.count)}
                    </div>
                    {(sec.metrics || []).map((m, i) => <MetricRow key={i} m={m} />)}
                    {sec.low_capacity_message && (
                      <div className="df-note" style={{ border: "none", padding: "6px 0 0" }}>
                        {sec.low_capacity_message}
                      </div>
                    )}
                  </div>
                );
              })}
              {rp.extra_orders && (
                <div>
                  <div className="lab mono" style={{ fontSize: 9.5, letterSpacing: ".12em",
                    textTransform: "uppercase", color: "var(--c-teal)", marginBottom: 5 }}>
                    {S.EXTRA_ORDERS.replace("\n", " ")} — +{n0(rp.extra_orders.count)}
                  </div>
                  <div className="df-note" style={{ border: "none", padding: 0 }}>
                    {rp.extra_orders.message}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        <div style={{ position: "sticky", top: 0, display: "flex", flexDirection: "column", gap: 13 }}>
          <SupportWidget hub={hub} partnerId={partnerId} />
          <div className="df-note">
            The widget answers from the <b style={{ color: "var(--text-mute)" }}>same payload</b>
            {" "}this page is drawing — one <span className="mono">load_status</span> named query,
            no SQL, and the decision runs through the trust gate. Nothing here moves money.
          </div>
        </div>
      </div>
    </div>
  );
}
