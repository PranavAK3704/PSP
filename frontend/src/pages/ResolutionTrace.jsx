import React from "react";
import { Cpu } from "lucide-react";
import Pipeline from "../components/Pipeline.jsx";
import DecisionCore from "../components/DecisionCore.jsx";
import { useLastTrace } from "../lib/traceStore.js";

// Standalone view of the LATEST resolution trace — the same DecisionCore orb +
// Pipeline the Captain Advocate rail shows, read from the shared trace store so it
// survives a view switch. Empty until a resolution has run in Captain Advocate.
export default function ResolutionTrace() {
  const { events, phase } = useLastTrace();
  const hasTrace = Array.isArray(events) && events.length > 0;

  // Trust-spine readout: the unique nodes the resolution traversed, first-seen order.
  const order = [];
  const seen = new Map();
  for (const e of events || []) {
    if (!seen.has(e.node)) order.push(e.node);
    seen.set(e.node, e);
  }
  const spine = order.map((n) => seen.get(n));
  const phaseLabel = phase === "thinking" ? "thinking…" : phase === "resolved" ? "resolved" : "idle";

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div className="card-head">
        <h3><Cpu size={15} /> Resolution Engine · latest trace</h3>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="mono faint" style={{ fontSize: 10 }}>{phaseLabel}</span>
          <div style={{ width: 34, height: 34 }}><DecisionCore size={34} state={phase} /></div>
        </div>
      </div>

      {hasTrace ? (
        <>
          {/* trust-spine readout — the pipeline of trust, at a glance */}
          <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--line-soft)" }}>
            <div className="mono faint" style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: 1.4, marginBottom: 7 }}>
              Trust spine · {spine.length} stage{spine.length === 1 ? "" : "s"}
            </div>
            <div className="flow-arch">
              {spine.map((ev, i) => {
                const blocked = (ev.node === "gate" && ev.data && !ev.data.passed) || ev.node === "escalate";
                return (
                  <React.Fragment key={ev.node}>
                    <span className="fbox" style={blocked ? { borderColor: "var(--warn)", color: "var(--warn)" } : undefined}>
                      {ev.label || ev.node}
                    </span>
                    {i < spine.length - 1 && <span className="farr">→</span>}
                  </React.Fragment>
                );
              })}
            </div>
          </div>

          <div style={{ overflow: "auto", padding: "14px 16px", flex: 1 }}>
            <Pipeline events={events} />
          </div>
        </>
      ) : (
        <div className="empty" style={{ flex: 1 }}>
          <div>
            <div style={{ marginBottom: 18 }}><DecisionCore size={120} state="idle" /></div>
            <div className="big">No live trace yet</div>
            <div style={{ fontSize: 13, maxWidth: 420, margin: "0 auto" }}>
              Send a message in Captain Advocate to see the live resolution trace here.
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
