import React, { useEffect, useMemo, useState } from "react";
import { getL3, resolveL3, submitNuance } from "../lib/api.js";

const sevBg = (s) => (s === "high" ? "bg-error/10 text-error border border-error/30" : s === "medium" ? "bg-warn/10 text-warn border border-warn/30" : "bg-tertiary/10 text-tertiary border border-tertiary/30");
const shortTeam = (t) => (t || "").replace(/\s*\(.*\)/, "");

// Status pill — Stitch "Command Center" language. Breached = solid Breach-Red
// "SLA BREACH"; otherwise amber "OPEN · Nh" (hours in queue). tabular-nums throughout.
function StatusPill({ it, big = false }) {
  const pad = big ? "px-3 py-1" : "px-2.5 py-1";
  if (it.breached) {
    return (
      <span className={`inline-flex items-center gap-1 rounded-full ${pad} text-[10px] font-bold tracking-wide whitespace-nowrap`}
        style={{ background: "var(--bad)", color: "#3a0a06", fontVariantNumeric: "tabular-nums" }}>
        <span className="material-symbols-outlined" style={{ fontSize: 13 }}>error</span>SLA BREACH
      </span>
    );
  }
  return (
    <span className={`inline-flex items-center rounded-full ${pad} text-[10px] font-bold tracking-wide whitespace-nowrap bg-warn/10 text-warn border border-warn/40`}
      style={{ fontFamily: "JetBrains Mono", fontVariantNumeric: "tabular-nums" }}>
      OPEN · {Math.round(it.age_hours || 0)}h
    </span>
  );
}

export default function L3Workspace() {
  const [data, setData] = useState({ items: [], teams: [] });
  const [selId, setSelId] = useState(null);
  const [actioned, setActioned] = useState({});
  const [busyId, setBusyId] = useState(null);

  function load(keepSel) {
    return getL3().then((d) => {
      const items = [...(d.items || [])].sort((a, b) => (b.breached - a.breached) || (b.age_hours - a.age_hours));
      setData({ items, teams: d.teams || [] });
      setSelId((c) => (keepSel && items.some((i) => i.concern_id === c) ? c : items[0]?.concern_id || null));
    });
  }
  useEffect(() => { load(false); }, []);

  async function resolveCase(concernId) {
    const note = window.prompt("Resolution note to send the captain:", "") ;
    if (note === null) return;                       // cancelled
    setBusyId(concernId);
    await resolveL3(concernId, note).catch(() => {});
    setActioned((a) => ({ ...a, [concernId]: "Resolved & captain notified" }));
    setBusyId(null);
    await load(false);                                // resolved case drops out of the active queue
  }

  // Capture a correction from a live case: a plain-language rule the engine will follow next
  // time (no code). Goes to the KT approval queue tagged to this case's domain.
  async function captureCorrection(c) {
    const text = window.prompt("This should've been resolved — add a rule for next time:", "");
    if (!text) return;
    const required = window.prompt("Required from the captain for this (comma-separated, optional):", "") || "";
    await submitNuance({ text, domain: c.disposition || "other", contributor: "L3-ops",
      required_inputs: required.split(",").map((s) => s.trim()).filter(Boolean),
      from_concern_id: c.concern_id }).catch(() => {});
    setActioned((a) => ({ ...a, [c.concern_id + "-corr"]: 1 }));
    alert("Rule queued for approval → once approved, the engine follows it automatically.");
  }

  const sel = useMemo(() => data.items.find((i) => i.concern_id === selId), [data, selId]);
  const breached = data.items.filter((i) => i.breached).length;
  const avgAge = data.items.length ? (data.items.reduce((s, i) => s + (i.age_hours || 0), 0) / data.items.length).toFixed(1) : "0";

  const Metric = ({ icon, label, value, sub, tone, danger }) => (
    <div className={`glass-card p-md rounded-xl flex flex-col justify-between scan-line min-h-[104px] ${danger ? "border-error/40" : ""}`}
      style={danger ? { background: "rgba(255,180,171,0.06)" } : undefined}>
      <div className="flex items-start justify-between gap-sm">
        <span className={`text-[10px] font-bold uppercase tracking-[0.12em] ${danger ? "text-error" : "text-on-surface-variant"}`}
          style={{ fontFamily: "JetBrains Mono" }}>{label}</span>
        <span className={`material-symbols-outlined ${danger ? "text-error" : "text-secondary-container/70"}`} style={{ fontSize: 17 }}>{icon}</span>
      </div>
      <div className="flex items-baseline gap-sm mt-xs">
        <span className={`text-[34px] font-bold leading-none ${danger ? "text-error" : tone}`} style={{ fontVariantNumeric: "tabular-nums" }}>{value}</span>
        <span className="text-on-surface-variant text-[11px]" style={{ fontFamily: "JetBrains Mono" }}>{sub}</span>
      </div>
    </div>
  );

  const COLS = "grid-cols-[112px_minmax(0,1.4fr)_110px_96px_minmax(0,1fr)_110px]";

  return (
    <div className="h-full flex flex-col gap-gutter">
      {/* Header metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-gutter">
        <Metric icon="inbox" label="Active Escalations" value={data.items.length} sub={`${breached} breached`} tone="text-secondary-container" danger={breached > 0} />
        <Metric icon="gpp_maybe" label="Breached / At-Risk" value={breached} sub="past SLA" tone="text-warn" />
        <Metric icon="diversity_3" label="Functional Teams" value={data.teams.length} sub="engaged" tone="text-on-surface" />
        <Metric icon="timer" label="Mean Age" value={`${avgAge}h`} sub="in queue" tone="text-tertiary" />
      </div>

      {/* Escalation queue — dense table */}
      <section className="glass-card rounded-xl overflow-hidden">
        <div className="px-lg h-[46px] flex justify-between items-center border-b border-on-primary-fixed-variant/20">
          <h2 className="text-secondary-container font-bold flex items-center gap-sm text-[11px] uppercase tracking-[0.12em]" style={{ fontFamily: "JetBrains Mono" }}>
            <span className="material-symbols-outlined" style={{ fontSize: 17 }}>format_list_bulleted</span>
            Escalation Queue · {data.items.length}
          </h2>
          <span className="text-[10px] bg-surface-variant px-sm py-0.5 rounded-full text-on-surface-variant" style={{ fontFamily: "JetBrains Mono" }}>PRIORITY SORT</span>
        </div>

        {/* column header */}
        <div className={`grid ${COLS} gap-md px-lg h-[32px] items-center text-[9px] uppercase tracking-[0.1em] text-on-surface-variant border-b border-on-primary-fixed-variant/15`}
          style={{ fontFamily: "JetBrains Mono" }}>
          <span>Concern</span><span>Disposition</span><span>Captain</span><span>Age / SLA</span><span>Team</span><span className="text-right">Status</span>
        </div>

        <div className="max-h-[320px] overflow-y-auto custom-scrollbar">
          {data.items.length === 0 && (
            <div className="px-lg py-lg text-on-surface-variant text-sm">No escalations — resolve or escalate a concern in the Captain Panel.</div>
          )}
          {data.items.map((it) => {
            const active = it.concern_id === selId;
            return (
              <button key={it.concern_id} onClick={() => setSelId(it.concern_id)}
                className={`w-full text-left grid ${COLS} gap-md px-lg h-[46px] items-center border-l-2 border-b border-on-primary-fixed-variant/10 last:border-b-0 transition-colors ${
                  active ? "border-l-secondary-container bg-secondary-container/[0.07]" : "border-l-transparent hover:bg-surface-variant/20"}`}>
                <span className={`text-[11px] font-bold ${active ? "text-secondary-container" : "text-on-surface-variant"}`}
                  style={{ fontFamily: "JetBrains Mono", fontVariantNumeric: "tabular-nums" }}>{it.concern_id}</span>
                <span className="text-[13px] text-on-surface truncate">{it.disposition}</span>
                <span className="text-[11px] text-on-surface-variant truncate" style={{ fontFamily: "JetBrains Mono" }}>{it.captain_id}</span>
                <span className={`text-[11px] ${it.breached ? "text-error font-bold" : "text-on-surface-variant"}`} style={{ fontFamily: "JetBrains Mono", fontVariantNumeric: "tabular-nums" }}>{it.age_hours}h / {it.sla_hours}h</span>
                <span className="text-[11px] text-on-surface-variant truncate">{shortTeam(it.team)}</span>
                <div className="justify-self-end"><StatusPill it={it} /></div>
              </button>
            );
          })}
        </div>
      </section>

      {/* Case detail */}
      <section className="flex-1 min-h-[300px]">
        {!sel ? (
          <div className="active-glass h-full rounded-xl grid place-items-center text-on-surface-variant">Select a case</div>
        ) : (
          <div className="active-glass rounded-xl flex flex-col overflow-hidden">
            {/* Header */}
            <div className="p-lg border-b border-on-primary-fixed-variant/20 flex justify-between items-center gap-md flex-wrap">
              <div>
                <div className="flex items-center gap-sm mb-xs flex-wrap">
                  <span className="rounded-full bg-secondary-container text-on-secondary px-2.5 py-1 text-[10px] font-bold tracking-wide">ACTIVE CASE</span>
                  <StatusPill it={sel} big />
                  <h2 className="text-lg font-semibold text-secondary-container">{sel.concern_id}: {sel.disposition}</h2>
                </div>
                <p className="text-on-surface-variant text-xs" style={{ fontFamily: "JetBrains Mono" }}>{sel.captain_id} · logged {(sel.logged_at || "").slice(0, 19).replace("T", " ")} UTC</p>
              </div>
              <div className="text-right">
                <p className="text-[9px] uppercase tracking-[0.12em] text-on-surface-variant" style={{ fontFamily: "JetBrains Mono" }}>SLA THRESHOLD</p>
                <p className={`font-bold border rounded-full px-3 py-1 mt-1 inline-block ${sel.breached ? "text-error pulse-border" : "text-tertiary border-tertiary/40"}`} style={{ fontFamily: "JetBrains Mono", fontVariantNumeric: "tabular-nums" }}>{sel.age_hours}h / {sel.sla_hours}h</p>
              </div>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto custom-scrollbar p-lg space-y-lg">
              <div>
                <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] text-secondary-container border-l-2 border-secondary-container pl-md mb-md" style={{ fontFamily: "JetBrains Mono" }}>Worked Case → {sel.team}</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-md">
                  <div className="glass-card p-md rounded-lg">
                    <p className="text-[10px] uppercase tracking-[0.1em] text-on-surface-variant mb-sm" style={{ fontFamily: "JetBrains Mono" }}>Outcome & Governance</p>
                    <div className="flex flex-wrap gap-xs">
                      <span className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold ${sevBg(sel.governance?.severity)}`}>severity: {sel.governance?.severity}</span>
                      <span className="rounded-full bg-surface-variant text-on-surface-variant px-2.5 py-0.5 text-[10px]" style={{ fontVariantNumeric: "tabular-nums" }}>amount: ₹{sel.amount_inr ?? "—"}</span>
                    </div>
                    <p className="text-[9px] text-on-surface-variant/60 mt-sm">severity by disputed amount</p>
                  </div>
                  <div className="glass-card p-md rounded-lg">
                    <p className="text-[10px] uppercase tracking-[0.1em] text-on-surface-variant mb-sm" style={{ fontFamily: "JetBrains Mono" }}>Escalation Ladder</p>
                    <div className="flex items-center gap-1 flex-wrap text-xs" style={{ fontFamily: "JetBrains Mono" }}>
                      {(sel.ladder || []).map((r, i) => (
                        <React.Fragment key={r}>
                          <span className={r === sel.escalation_rung ? "text-warn font-bold" : "text-on-surface-variant"}>{r}</span>
                          {i < sel.ladder.length - 1 && <span className="text-secondary-container">→</span>}
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              {(sel.worked_case?.evidence_trail || []).length > 0 && (
                <div>
                  <h3 className="text-[11px] font-bold uppercase tracking-[0.12em] text-secondary-container border-l-2 border-secondary-container pl-md mb-md" style={{ fontFamily: "JetBrains Mono" }}>Evidence Assembled</h3>
                  <div className="space-y-sm">
                    {sel.worked_case.evidence_trail.map((e, i) => (
                      <div key={i} className="glass-card p-md rounded-lg border-l-2 border-l-secondary-fixed">
                        <p className="text-xs font-bold text-secondary-fixed">{e.label}</p>
                        <p className="text-xs text-on-surface-variant mt-1">{e.value}</p>
                        <p className="text-[9px] text-on-surface-variant/60 mt-1" style={{ fontFamily: "JetBrains Mono" }}>◦ via {e.source}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {sel.worked_case?.reply && (
                <div className="glass-card p-lg rounded-xl border-l-4 border-l-warn/50 bg-warn/5">
                  <div className="flex items-center gap-sm mb-md text-warn">
                    <span className="material-symbols-outlined" style={{ fontSize: 18 }}>handshake</span>
                    <h3 className="text-[11px] font-bold uppercase tracking-[0.12em]" style={{ fontFamily: "JetBrains Mono" }}>What the partner was told</h3>
                  </div>
                  <p className="text-sm text-on-surface leading-relaxed italic">“{sel.worked_case.reply}”</p>
                </div>
              )}
            </div>

            {/* Footer actions */}
            <div className="p-lg bg-surface-container-high/60 border-t border-on-primary-fixed-variant/20 flex gap-md">
              {actioned[sel.concern_id] ? (
                <div className="flex-1 bg-tertiary/10 text-tertiary py-md rounded-lg font-bold flex items-center justify-center gap-sm">
                  <span className="material-symbols-outlined" style={{ fontSize: 18 }}>check_circle</span> {actioned[sel.concern_id]}
                </div>
              ) : (
                <>
                  <button onClick={() => resolveCase(sel.concern_id)} disabled={busyId === sel.concern_id}
                    className="flex-1 bg-tertiary text-on-tertiary py-md rounded-lg font-bold flex items-center justify-center gap-sm hover:brightness-110 transition-all active:scale-[0.98] disabled:opacity-50">
                    <span className="material-symbols-outlined" style={{ fontSize: 18 }}>check_circle</span>
                    {busyId === sel.concern_id ? "Resolving…" : "Resolve & notify captain"}
                  </button>
                  <button onClick={() => captureCorrection(sel)}
                    className="flex-none px-lg border border-secondary-container text-secondary-container py-md rounded-lg font-bold flex items-center justify-center gap-sm hover:bg-secondary-container/10 transition-all active:scale-[0.98]"
                    title="Add a plain-language rule the engine will follow next time (no code)">
                    <span className="material-symbols-outlined" style={{ fontSize: 18 }}>auto_fix_high</span> Capture rule
                  </button>
                </>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
