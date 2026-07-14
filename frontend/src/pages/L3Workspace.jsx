import React, { useEffect, useMemo, useState } from "react";
import { getL3, resolveL3, submitNuance } from "../lib/api.js";

const sev = (s) => (s === "high" ? "text-error" : s === "medium" ? "text-warn" : "text-tertiary");
const sevBg = (s) => (s === "high" ? "bg-error/10 text-error" : s === "medium" ? "bg-warn/10 text-warn" : "bg-tertiary/10 text-tertiary");

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

  const Metric = ({ label, value, sub, tone, danger }) => (
    <div className={`glass-card p-md rounded-xl flex flex-col justify-between scan-line ${danger ? "border-error/20 bg-error/5" : ""}`}>
      <span className={`text-[11px] font-bold uppercase tracking-[0.1em] ${danger ? "text-error" : "text-on-surface-variant"}`}>{label}</span>
      <div className="flex items-baseline gap-sm mt-xs">
        <span className={`text-[40px] font-bold leading-none ${tone}`}>{value}</span>
        <span className="text-on-surface-variant text-xs" style={{ fontFamily: "JetBrains Mono" }}>{sub}</span>
      </div>
    </div>
  );

  return (
    <div className="h-full flex flex-col gap-gutter">
      {/* Header metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-gutter">
        <Metric label="Active Escalations" value={data.items.length} sub={`${breached} breached`} tone="text-secondary-container" danger={breached > 0} />
        <Metric label="Breached / At-Risk" value={breached} sub="past SLA" tone="text-warn" />
        <Metric label="Functional Teams" value={data.teams.length} sub="engaged" tone="text-on-surface" />
        <Metric label="Mean Age" value={`${avgAge}h`} sub="in queue" tone="text-tertiary" />
      </div>

      {/* Bento */}
      <div className="flex-1 grid grid-cols-12 gap-gutter overflow-hidden min-h-0">
        {/* Queue */}
        <section className="col-span-12 lg:col-span-4 flex flex-col gap-md overflow-hidden h-full">
          <div className="flex justify-between items-center px-xs">
            <h2 className="text-secondary-container font-semibold flex items-center gap-sm text-sm">
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>format_list_bulleted</span> Escalation Queue
            </h2>
            <span className="text-[10px] bg-surface-variant px-sm py-0.5 rounded text-on-surface-variant" style={{ fontFamily: "JetBrains Mono" }}>PRIORITY SORT</span>
          </div>
          <div className="flex-1 overflow-y-auto custom-scrollbar space-y-sm pr-xs">
            {data.items.length === 0 && <div className="glass-card p-md rounded-xl text-on-surface-variant text-sm">No escalations — resolve or escalate a concern in the Captain Panel.</div>}
            {data.items.map((it) => {
              const active = it.concern_id === selId;
              return (
                <div key={it.concern_id} onClick={() => setSelId(it.concern_id)}
                  className={`p-md rounded-xl cursor-pointer border-l-4 transition-colors ${active ? "active-glass scan-line border-l-secondary-container" : "glass-card border-l-transparent hover:bg-surface-variant/20"}`}>
                  <div className="flex justify-between items-start mb-sm">
                    <span className={`text-[11px] font-bold tracking-wide ${active ? "text-secondary-container" : "text-on-surface-variant"}`}>{it.concern_id}</span>
                    <span className={`text-xs ${it.breached ? "text-error" : "text-on-surface-variant"}`} style={{ fontFamily: "JetBrains Mono" }}>{it.age_hours}h / {it.sla_hours}h</span>
                  </div>
                  <h3 className="text-sm text-on-surface font-medium line-clamp-1">{it.intent || it.disposition}</h3>
                  <p className="text-xs text-on-surface-variant mt-1">{it.captain_id} · {it.disposition}</p>
                  <div className="flex gap-xs mt-md flex-wrap">
                    <span className={`px-sm py-0.5 rounded text-[10px] font-bold ${sevBg(it.governance?.severity)}`}>{(it.governance?.severity || "low").toUpperCase()}</span>
                    <span className="bg-surface-variant text-on-surface-variant px-sm py-0.5 rounded text-[10px] font-bold">{(it.team || "").replace(/\s*\(.*\)/, "").toUpperCase()}</span>
                    {it.breached && <span className="bg-warn/10 text-warn px-sm py-0.5 rounded text-[10px] font-bold">{it.escalation_rung}</span>}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* Detail */}
        <section className="col-span-12 lg:col-span-8 h-full overflow-hidden">
          {!sel ? (
            <div className="active-glass h-full rounded-xl grid place-items-center text-on-surface-variant">Select a case</div>
          ) : (
            <div className="active-glass h-full rounded-xl flex flex-col overflow-hidden">
              {/* Header */}
              <div className="p-lg border-b border-on-primary-fixed-variant/20 flex justify-between items-center">
                <div>
                  <div className="flex items-center gap-sm mb-xs">
                    <span className="bg-secondary-container text-on-secondary px-sm py-0.5 rounded text-[10px] font-bold">ACTIVE CASE</span>
                    <h2 className="text-lg font-semibold text-secondary-container">{sel.concern_id}: {sel.disposition}</h2>
                  </div>
                  <p className="text-on-surface-variant text-xs" style={{ fontFamily: "JetBrains Mono" }}>{sel.captain_id} · logged {(sel.logged_at || "").slice(0, 19).replace("T", " ")} UTC</p>
                </div>
                <div className="text-right">
                  <p className="text-[9px] uppercase tracking-wide text-on-surface-variant">SLA THRESHOLD</p>
                  <p className={`font-bold border rounded px-sm py-1 mt-1 ${sel.breached ? "text-error pulse-border" : "text-tertiary border-tertiary/40"}`} style={{ fontFamily: "JetBrains Mono" }}>{sel.age_hours}h / {sel.sla_hours}h</p>
                </div>
              </div>

              {/* Content */}
              <div className="flex-1 overflow-y-auto custom-scrollbar p-lg space-y-lg">
                <div>
                  <h3 className="text-[11px] font-bold uppercase tracking-[0.1em] text-secondary-container border-l-2 border-secondary-container pl-md mb-md">Worked Case → {sel.team}</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-md">
                    <div className="glass-card p-md rounded-lg">
                      <p className="text-[10px] uppercase tracking-wide text-on-surface-variant mb-sm">Outcome & Governance</p>
                      <div className="flex flex-wrap gap-xs">
                        <span className={`px-sm py-0.5 rounded text-[10px] font-bold ${sevBg(sel.governance?.severity)}`}>severity: {sel.governance?.severity}</span>
                        <span className="bg-surface-variant text-on-surface-variant px-sm py-0.5 rounded text-[10px]">amount: ₹{sel.amount_inr ?? "—"}</span>
                      </div>
                      <p className="text-[9px] text-on-surface-variant/60 mt-sm">severity by disputed amount</p>
                    </div>
                    <div className="glass-card p-md rounded-lg">
                      <p className="text-[10px] uppercase tracking-wide text-on-surface-variant mb-sm">Escalation Ladder</p>
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
                    <h3 className="text-[11px] font-bold uppercase tracking-[0.1em] text-secondary-container border-l-2 border-secondary-container pl-md mb-md">Evidence Assembled</h3>
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
                      <h3 className="text-[11px] font-bold uppercase tracking-[0.1em]">What the partner was told</h3>
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
    </div>
  );
}
