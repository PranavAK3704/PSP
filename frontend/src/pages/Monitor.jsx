import React, { useEffect, useState } from "react";
import Pipeline from "../components/Pipeline.jsx";
import DecisionCore from "../components/DecisionCore.jsx";
import PulseNet from "../components/PulseNet.jsx";
import { stream, getCaptains } from "../lib/api.js";
import { useNav } from "../App.jsx";

export default function Monitor() {
  const [captains, setCaptains] = useState([]);
  const [captainId, setCaptainId] = useState("VLMO-CPT-4471");
  const [events, setEvents] = useState([]);
  const [busy, setBusy] = useState(false);
  const nav = useNav();

  useEffect(() => { getCaptains().then((d) => setCaptains(d.captains || [])); }, []);
  async function run() {
    setEvents([]); setBusy(true);
    await stream({ url: `/api/monitor/${captainId}`, method: "GET" },
      (ev) => setEvents((p) => [...p, ev]), () => setBusy(false));
  }

  return (
    <div className="h-full flex flex-col gap-gutter">
      {/* Pointer banner */}
      <div className="glass-card rounded-xl border-l-4 border-l-secondary-container/60 relative overflow-hidden">
        <div className="absolute inset-0 opacity-40 pointer-events-none"><PulseNet height={260} nodes={40} /></div>
        <div className="relative p-lg flex items-center gap-lg">
        <div className="hidden md:block"><DecisionCore size={120} /></div>
        <div>
          <div className="flex items-center gap-2 text-[11px] font-bold uppercase tracking-[0.1em] text-secondary-container">
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>dns</span> Backend service · always-on
          </div>
          <h3 className="text-lg font-bold mt-1">Proactive monitoring runs on the event stream, not per captain.</h3>
          <p className="text-sm text-on-surface-variant mt-1 max-w-3xl">
            A headless, always-on consumer of the <b className="text-on-surface">Log10 / Captain-Panel</b> event
            stream — a cheap deterministic first-pass filters everything, and an LLM only fires when a real risk is detected.
            Every nudge is written to the <b className="text-on-surface">Concern Log</b>, so proactive protection shows up in the
            Ledger and Audit alongside reactive resolutions. At scale it runs continuously across all captains on <b className="text-secondary-container">Log10</b>.
          </p>
        </div>
        </div>
      </div>

      {/* Concept cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-gutter">
        {[
          ["savings", "Cost discipline", "10k captains generate huge event volume, but only a small % become real risks. LLM cost scales with risk-events, not captain count."],
          ["visibility_off", "Shadow-first", "Warns internally until precision is proven, so captain-facing alerts never become noise."],
          ["shield_moon", "Partner-protective", "Every nudge prevents a breach/penalty before it costs the partner — opt-in, never surveillance."],
        ].map(([icon, t, d]) => (
          <div key={t} className="glass-card rounded-xl p-lg">
            <span className="material-symbols-outlined text-tertiary">{icon}</span>
            <div className="font-bold text-sm mt-2">{t}</div>
            <p className="text-xs text-on-surface-variant mt-1 leading-relaxed">{d}</p>
          </div>
        ))}
      </div>

      {/* Preview */}
      <div className="flex-1 grid grid-cols-12 gap-gutter min-h-0">
        <div className="col-span-12 lg:col-span-5 glass-card rounded-xl p-lg flex flex-col">
          <div className="text-[11px] font-bold uppercase tracking-[0.1em] text-on-surface-variant mb-md">Event scan</div>
          <div className="flex gap-sm">
            <select value={captainId} onChange={(e) => setCaptainId(e.target.value)}
              className="flex-1 bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded-lg px-md py-sm text-sm focus:outline-none focus:border-secondary-container" style={{ fontFamily: "JetBrains Mono" }}>
              {captains.map((c) => <option key={c.captain_id} value={c.captain_id}>{c.name} · {c.hub_name}</option>)}
            </select>
            <button onClick={run} disabled={busy}
              className="bg-secondary-container text-on-secondary px-lg py-sm rounded-lg font-bold text-sm flex items-center gap-2 hover:brightness-110 disabled:opacity-50">
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>play_arrow</span>{busy ? "Scanning…" : "Run scan"}</button>
          </div>
          <p className="text-xs text-on-surface-variant mt-md leading-relaxed">
            Scans this captain's shipment events for hardstop-breach risk; each nudge is logged to the Concern Log. On Log10 it runs continuously across all captains.
          </p>
          <button onClick={() => nav("captain")}
            className="mt-auto self-start text-secondary-container text-xs flex items-center gap-1 hover:underline">
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>arrow_forward</span> a nudge uses the same engine as the Captain Advocate
          </button>
        </div>
        <div className="col-span-12 lg:col-span-7 glass-card rounded-xl p-lg overflow-y-auto custom-scrollbar">
          <div className="text-[11px] font-bold uppercase tracking-[0.1em] text-secondary-container mb-md">Monitor trace · live</div>
          <Pipeline events={events} />
        </div>
      </div>
    </div>
  );
}
