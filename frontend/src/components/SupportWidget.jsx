import { useEffect, useRef, useState } from "react";
import { MessageSquare, Send, Paperclip, Radar, CheckCircle2, Clock } from "lucide-react";
import { chatStream, getCaptainCases } from "../lib/api.js";
import TraceView from "./TraceView.jsx";

/* ── The docked support widget, and the captain's own case strip ───────────────────────────────

   EXTRACTED from GrowthDashboard so the whole Captain Panel can host it, not just one module.
   That matters because of what the real panel does today: `Valmo Support` is the FIRST row in
   its sidebar, above every module, and it is a `window.open()` to

       https://selfserveapp.kapturecrm.com/support-portals/valmo/index.php

   A captain who needs help is ejected out of the app into an external ticket portal in a new
   tab. That is the incumbent, in one line, in their own repo — and it is why the widget belongs
   on the SHELL rather than beside one dashboard: the replacement has to be reachable from
   wherever the captain already is, which is usually Payments.
   ── */

function MyCases({ partnerId }) {
  const [cases, setCases] = useState([]);
  useEffect(() => {
    if (!partnerId) { setCases([]); return; }
    let alive = true;
    const pull = () => getCaptainCases(partnerId)
      .then((d) => { if (alive) setCases(d.cases || []); })
      .catch(() => {});
    pull();
    const t = setInterval(pull, 6000);
    return () => { alive = false; clearInterval(t); };
  }, [partnerId]);

  if (!cases.length) return null;
  return (
    <div className="card">
      <div className="card-head">
        <h3><FolderOpen size={14} />My cases</h3>
        <span className="mono" style={{ fontSize: 9, color: "var(--text-faint)" }}>
          {cases.length} on record
        </span>
      </div>
      <div style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 7 }}>
        {cases.slice(0, 4).map((c) => {
          const done = !!c.resolved || c.status === "resolved";
          return (
            <div key={c.concern_id} style={{ display: "flex", gap: 8, alignItems: "flex-start",
              padding: "8px 10px", borderRadius: 8, background: "var(--surface-2)",
              border: `1px solid ${done ? "rgba(78,222,163,0.30)" : "var(--line)"}` }}>
              {done ? <CheckCircle2 size={13} style={{ color: "var(--good)", flex: "none",
                marginTop: 1 }} />
                    : <Clock size={13} style={{ color: "var(--warn)", flex: "none",
                        marginTop: 1 }} />}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="mono" style={{ fontSize: 10, color: "var(--text-mute)" }}>
                  {c.concern_id}{c.team ? ` · ${c.team}` : ""}
                </div>
                <div style={{ fontSize: 11.5, color: done ? "var(--good)" : "var(--text-faint)",
                  marginTop: 2, lineHeight: 1.5 }}>
                  {done ? (c.resolution_note || "Resolved by the team.")
                        : "With the team — you'll be updated."}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}


function SupportWidget({ hub, partnerId, askRef, showEngine }) {
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  // The RAW event list, not a pre-flattened list of strings.
  //
  // This was `steps` — an array of one-line mono strings built by an 8-line mapping that read
  // five fields and threw the rest away: the individual checks, the evidence rows, the gate's
  // confidence and threshold, every `blocks` string, and the entire `escalate` and `verify`
  // nodes. Those are precisely the things that make the engine's reasoning legible. Keeping the
  // events lets <TraceView> render the same three visual elements the L3 desk and the ledger
  // render, off the same record.
  const [events, setEvents] = useState([]);
  const convRef = useRef(null);
  const endRef = useRef(null);
  const abortRef = useRef(null);
  // Monotonic turn id. An in-flight stream from a previous hub can still deliver events after
  // abort() resolves, and its `reply` would land in a conversation that has been cleared — so
  // every callback checks it is still the current turn before touching state.
  const turnRef = useRef(0);

  // Cancel any in-flight stream when the captain/hub changes OR the component unmounts.
  // Without this the read loop kept running against a cleared conversation, and `busy` never
  // reset, so the input stayed disabled until the orphaned turn happened to finish.
  useEffect(() => {
    return () => { abortRef.current?.abort(); };
  }, []);
  useEffect(() => {
    abortRef.current?.abort();
    turnRef.current += 1;
    setMsgs([]); setEvents([]); setBusy(false); convRef.current = null;
  }, [hub, partnerId]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, events]);
  // Publish `send` upward so the dashboard's own CTAs can drive this widget. Kept as a ref
  // assignment rather than lifted state: the widget owns its conversation id, its AbortController
  // and its turn guard, and hoisting those into the page would mean re-plumbing three things
  // that already work correctly.
  useEffect(() => { if (askRef) askRef.current = send; });

  async function send(text) {
    const msg = (text ?? input).trim();
    if (!msg || busy || !partnerId) return;
    setInput(""); setMsgs((m) => [...m, { who: "captain", text: msg }]);
    setEvents([]); setBusy(true);
    if (!convRef.current) convRef.current = crypto.randomUUID();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const myTurn = turnRef.current;
    const mine = () => turnRef.current === myTurn && !ctrl.signal.aborted;
    // try/finally, because setBusy(false) used to live ONLY in onEnd — so any throw from
    // stream() (network drop, null body, reader reset) left the widget permanently disabled.
    try {
      // Accumulated across the turn, because the ESCALATION and the REPLY can describe different
      // intents: conversation.py sets `terminal_action`/`terminal_concern` from the LAST tool it
      // dispatched, so on a two-intent turn where the escalation came first, the reply event says
      // `respond` and carries the other intent's id. Reading the line off `reply` alone therefore
      // gave a captain who WAS escalated no reference, no team and no ETA — the one thing the
      // engine-hidden view is supposed to guarantee. The escalate event is the authority on its
      // own escalation, so it is captured here and wins below.
      let escalated = null;
      await chatStream(
      // `partner` — this widget IS the captain's surface, so its rows are the only ones in the
      // ledger that represent a real partner asking for help.
      { captainId: partnerId, message: msg, conversationId: convRef.current,
        source: "partner", signal: ctrl.signal },
      (ev) => {
        if (!mine()) return;
        // Keep EVERY event. TraceView decides what to show at this width; the widget's job is
        // not to pre-digest the trace, and the old version's 8-line filter is exactly how the
        // checks and the gate verdict went missing.
        setEvents((e) => [...e, ev]);
        if (ev.node === "escalate" && ev.data?.reference_id) {
          escalated = { ref: ev.data.reference_id, team: ev.data.team || "" };
        }
        if (ev.node === "reply" && ev.data?.reply) {
          setMsgs((m) => [...m, { who: "psp", text: ev.data.reply,
                                  failed: !!ev.data.engine_error,
                                  ref: escalated?.ref || ev.data.concern_id || "",
                                  team: escalated?.team || "",
                                  action: escalated ? "escalate"
                                                    : (ev.data.decision_action || "") }]);
        }
      },
      () => { if (mine()) setBusy(false); });
    } catch {
      if (mine()) setMsgs((m) => [...m, { who: "psp", failed: true,
        text: "The connection dropped mid-answer. Please ask again." }]);
    } finally {
      if (mine()) setBusy(false);
    }
  }


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
            {/* THE LINE A REAL CAPTAIN GETS. It survives with the engine trace hidden, because
                it is the only part of the escalation that concerns them: it went somewhere, here
                is the reference, here is roughly when to expect an answer. */}
            {m.action === "escalate" && m.ref && (
              <div className="mono" style={{ fontSize: 10, color: "var(--text-faint)",
                marginTop: 6, paddingTop: 5, borderTop: "1px solid var(--line-soft)" }}>
                {m.team ? `${m.team} · ` : ""}ref {m.ref} · expect an update within ~24h
              </div>
            )}
          </div>
        ))}

        {/* ── the engine's own reasoning, for the audience only ──────────────────
            Rendered by the SAME component the L3 desk and the concern log use, at the
            `widget` scope. Capped at 340px with its own scroller: the column this lives in
            is sticky, so its overflow is unreachable — the thing that would scroll it is
            pinned — and an uncapped trace here would push the rest off screen for good. */}
        {showEngine && events.length > 0 && (
          <TraceView events={events} scope="widget" maxHeight={340} />
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


export { SupportWidget, MyCases };
