import { useEffect, useRef, useState } from "react";
import { Radar, Play } from "lucide-react";
import { stream } from "../lib/api.js";
import Pipeline from "./Pipeline.jsx";

/* ── Proactive monitoring, docked on the captain's own panel ───────────────────────────────────

   This was a separate top-level tab, which made it read as a demo of itself rather than as part of
   the system. A nudge reaches the CAPTAIN, so it belongs on the captain's screen.

   THREE THINGS THAT ARE DELIBERATE:

   1. **BUTTON-ONLY.** Never on mount, never on hub change. A scan makes a real model call, so
      auto-running it would bill one per page view of the app's DEFAULT surface — and two nervous
      presses on stage would be two charges and two cohort nudges. Disabled while running, and it
      relabels to "Scan again" afterwards so a second press is a decision rather than a reflex.

   2. **`partnerId` comes in as a prop.** The standalone page carried its own captain <select> and
      a getCaptains() fetch, defaulted to the seed id `VLMO-CPT-4471` — which matches no real
      partner, and is precisely why the monitor had never once produced a nudge record. The panel
      already resolves hub → real partner id; taking it as a prop deletes the picker and the bug
      together.

   3. **`Pipeline` wholesale is right HERE and wrong in the widget.** A scan yields 7-9 one-line
      nodes, and `monitor.py` stamps a monotonic `seq`, so Pipeline's collapse map keys on
      `node#seq` and its release queue paces the reveal. The chat path is a different shape: an
      escalating turn is ~1,540px of trace, which is why the widget uses the trimmed TraceView. ── */
export default function MonitorDock({ partnerId, hub }) {
  const [events, setEvents] = useState([]);
  const [busy, setBusy] = useState(false);
  const [ran, setRan] = useState(false);
  const abortRef = useRef(null);

  // Cancel in flight on unmount AND on partner change — an orphaned read loop would otherwise
  // keep pushing another hub's events into this trace.
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {
    abortRef.current?.abort();
    setEvents([]); setBusy(false); setRan(false);
  }, [partnerId]);

  async function run() {
    if (busy || !partnerId) return;
    setEvents([]); setBusy(true); setRan(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      await stream(
        { url: `/api/monitor/${encodeURIComponent(partnerId)}`, signal: ctrl.signal },
        (ev) => { if (!ctrl.signal.aborted) setEvents((e) => [...e, ev]); },
        () => {},
      );
    } catch { /* stream() already ends cleanly on abort; nothing to add */ }
    finally { if (!ctrl.signal.aborted) setBusy(false); }
  }

  return (
    <div className="card">
      <div className="card-head">
        <h3><Radar size={14} />Proactive monitoring</h3>
        <span className="mono" style={{ fontSize: 9, color: "var(--text-faint)" }}>
          nobody asked for this one
        </span>
      </div>
      <div style={{ padding: "12px 14px", display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="df-note" style={{ border: "none", padding: 0, color: "var(--text-mute)",
          lineHeight: 1.6 }}>
          The engine reads the shipment ledger without being asked. Rules band every row for free;
          a model is called <b style={{ color: "var(--text)" }}>once for the cohort</b>, not once
          per shipment. A ticketing tool cannot raise this — there is no ticket.
        </div>
        <div>
          <button className="chip" onClick={run} disabled={busy || !partnerId}
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <Play size={11} />{busy ? "scanning…" : ran ? "Scan again" : "Run a risk scan"}
          </button>
          {!partnerId && (
            <span className="mono" style={{ fontSize: 9.5, color: "var(--text-faint)",
              marginLeft: 8 }}>no partner mapped to {hub}</span>
          )}
        </div>
        {events.length > 0 && (
          <div style={{ maxHeight: 340, overflowY: "auto" }} className="custom-scrollbar">
            <Pipeline events={events} />
          </div>
        )}
      </div>
    </div>
  );
}
