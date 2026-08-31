import { useEffect, useRef, useState } from "react";
// FolderOpen and Sparkles were the crash: both are used in JSX below and neither came across
// when these two components were extracted out of GrowthDashboard, because the icons were on
// that file's own import line. React renders `undefined` as an element type and throws, so the
// whole Captain Panel fell into the error boundary — "This panel hit a snag" — rather than
// failing at build time. `vite build` cannot catch this: an undefined identifier in JSX is
// perfectly valid JavaScript until it is evaluated.
import { FolderOpen, Sparkles, MessageSquare, Send, Paperclip, Radar,
         CheckCircle2, Clock, Mic, Square, Volume2, VolumeX, Keyboard } from "lucide-react";
import { chatStream, getCaptainCases } from "../lib/api.js";
import { useVoice, VOICE_LANGS, phrases } from "../lib/useVoice.js";
import VoiceBars from "./VoiceBars.jsx";
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


/* ── What the engine is doing, in words a captain reads ──────────────────────────────────────

   A spinner says "wait". For someone who is not confident with apps, "wait" with no explanation
   is where they close the tab — so this says WHAT is happening instead, in Hinglish, and it is
   driven by the engine's own events rather than a timer. Every line appears because that node
   actually fired.

   That distinction matters beyond honesty: a fake progress animation runs at a fixed pace and
   desynchronises from reality, so a slow turn sits on "almost done" for ten seconds. This one
   cannot, because it IS the trace.

   The text is first-person and concrete — "aapka record nikaal raha hoon" (I'm pulling up your
   record) rather than "retrieving records". Somebody watching should feel that someone is working
   on their problem, which is the entire reason to show this instead of a spinner. */
const STEP_WORDS = {
  capture:   "Aapki baat samajh raha hoon…",
  extract:   "AWB / amount nikaal raha hoon…",
  firstpass: "Dekh raha hoon kis type ka issue hai…",
  knowledge: "SOP aur rules padh raha hoon…",
  query:     "Aapka record nikaal raha hoon…",
  ground:    "Aapke data se milaan kar raha hoon…",
  policy:    "Rule ke hisaab se check kar raha hoon…",
  gate:      "Confirm kar raha hoon ki jawab sahi hai…",
  verify:    "Dobara jaanch kar raha hoon…",
  act:       "Aage ka kadam le raha hoon…",
  escalate:  "Team ko bhej raha hoon…",
  explain:   "Jawab likh raha hoon…",
  cost:      "Bas ho gaya…",
  // The other three the CHAT path can emit. `error` is the one that matters: without a phrase the
  // list simply stops on its last step and the captain watches three dots forever on a turn that
  // has already failed. The remaining unmapped nodes — clear, compose, honesty, nudge, source,
  // stream — are monitor-only and never reach this widget, so they are deliberately absent rather
  // than missed. (Checked by listing every `_evt("…")` in engine/ and monitor/ against this map.)
  guard:     "Ek cheez aur confirm kar raha hoon…",
  learn:     "Yaad rakh raha hoon aage ke liye…",
  error:     "Kuch dikkat aayi — dobara dekh raha hoon…",
};

function ProgressSteps({ events }) {
  // De-duplicated: a node can fire twice on a multi-intent turn, and repeating the same sentence
  // reads as a stutter rather than as progress.
  const steps = [];
  for (const ev of events) {
    const w = STEP_WORDS[ev.node];
    if (w && steps[steps.length - 1] !== w) steps.push(w);
  }
  if (!steps.length) return <div className="sw-step sw-step-live"><span className="sw-dots"><i/><i/><i/></span>Shuru kar raha hoon…</div>;
  // Only the last three. The point is momentum, not a log — the full trace is TraceView's job.
  return (
    <div className="sw-steps">
      {steps.slice(-3).map((w, i, a) => (
        <div key={`${w}-${i}`}
          className={`sw-step ${i === a.length - 1 ? "sw-step-live" : "sw-step-done"}`}>
          {i === a.length - 1
            ? <span className="sw-dots"><i/><i/><i/></span>
            : <span className="sw-tick">✓</span>}
          {w}
        </div>
      ))}
    </div>
  );
}

/* Reveals an answer at reading pace. For a low-confidence reader a paragraph that ARRIVES is a
   wall; the same paragraph that BUILDS is followable, and the movement holds attention while it
   does. ~2 chars per 28ms puts a 200-character reply on screen in about 3 seconds.

   Two guards: it skips entirely under `prefers-reduced-motion`, and it only animates the NEWEST
   message — re-typing the whole history on every render would be both absurd and unreadable. */
function Typed({ text, animate }) {
  const [n, setN] = useState(animate ? 0 : text.length);
  useEffect(() => {
    if (!animate || window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
      setN(text.length);
      return;
    }
    setN(0);
    let i = 0;
    const t = setInterval(() => {
      i += 2;
      setN(i);
      if (i >= text.length) clearInterval(t);
    }, 28);
    return () => clearInterval(t);
  }, [text, animate]);
  return <>{text.slice(0, n)}{n < text.length && <span className="sw-caret" />}</>;
}

function SupportWidget({ hub, partnerId, askRef, showEngine }) {
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  // ── VOICE FIRST ─────────────────────────────────────────────────────────────────────────────
  // The mic is the PRIMARY control and typing is the fallback, which is the inverse of how this
  // panel was built. For an audience where 100% of DCs prefer Hindi or a regional language and an
  // adoption pilot activated 16.3% because people "still couldn't log in or navigate", a text box
  // puts the hardest interaction first and the easiest one in a corner.
  const V = useVoice();
  const [typing, setTyping] = useState(false);   // the keyboard is opt-in
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
        if (ev.node === "reply" && ev.data?.reply && !ev.data?.engine_error) {
          // Read it out when the captain has asked for that. Fires once per turn, on the reply
          // event, so a long trace never delays the voice.
          V.speak(ev.data.reply);
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
        <h3>
          {/* Breathes when idle so the widget looks alive rather than switched off; spins up
              while a turn is in flight. A dead-still panel reads as "this doesn't work". */}
          <span className={`sw-spark ${busy ? "working" : ""}`}><Sparkles size={14} /></span>
          Partner Support
        </h3>
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
              {["why is my load low", "why did my capacity get cut", "how do I get more orders"].map((q, i) => (
                <button key={q} className="chip sw-in sw-tap" style={{ animationDelay: `${i * 70}ms` }}
                  onClick={() => send(q)} disabled={busy || !partnerId}>{q}</button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className="sw-in" style={{ alignSelf: m.who === "captain" ? "flex-end" : "flex-start",
            maxWidth: "88%", padding: "8px 11px", borderRadius: 10, fontSize: 12.5, lineHeight: 1.55,
            background: m.who === "captain" ? "var(--surface-3)"
              : m.failed ? "var(--warn-soft)" : "var(--surface-2)",
            border: `1px solid ${m.failed ? "rgba(255,185,95,.35)" : "var(--line-soft)"}`,
            color: "var(--text)" }}>
            {m.who === "psp" && !m.failed
              ? (V.speaking && i === msgs.length - 1
                  // KARAOKE. Each phrase lights as it is spoken, which couples the sound to the
                  // shape of the words — for a reader who finds Hinglish hard, that makes the text
                  // legible rather than decorative, and lets them follow along again afterwards.
                  ? phrases(m.text).map((ph, j) => (
                      <span key={j} className={j === V.spokenIdx ? "sw-said" : "sw-say"}>
                        {ph}{" "}
                      </span>
                    ))
                  : <Typed text={m.text} animate={i === msgs.length - 1} />)
              : m.text}
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
        {busy && <ProgressSteps events={events} />}
        <div ref={endRef} />
      </div>

      {/* ── THE MIC, AS THE PRIMARY CONTROL ──────────────────────────────────────────────────
          One large button, the language beside it, read-aloud beside that, and typing as a link
          underneath. No animation fixes a wrong hierarchy; getting the hierarchy right is what
          makes this panel feel like it was built for the person using it. */}
      <div className="sw-voice">
        {V.listening ? (
          <>
            <button className="sw-mic listening" onClick={V.stop}
              aria-label="Stop listening">
              <Square size={17} />
            </button>
            <div className="sw-voice-mid">
              <VoiceBars levelRef={V.levelRef} active={V.listening} />
              <div className="sw-heard">{V.heard || "sun raha hoon…"}</div>
            </div>
          </>
        ) : (
          <>
            <button className="sw-mic sw-tap" disabled={busy || !partnerId || !V.supported}
              onClick={() => V.start((text) => send(text))}
              aria-label="Hold to speak"
              title={V.supported ? "Bolkar poochhiye" : "Voice needs Chrome or Edge"}>
              <Mic size={19} />
            </button>
            <div className="sw-voice-mid">
              <div className="sw-voice-cta">
                {V.speaking ? "bol raha hoon…" : busy ? "dekh raha hoon…" : "Bolkar poochhiye"}
              </div>
              <div className="sw-voice-ctl">
                <select className="sw-lang" value={V.lang}
                  onChange={(e) => V.setLang(e.target.value)} aria-label="Language">
                  {VOICE_LANGS.map(([code, label]) => (
                    <option key={code} value={code}>{label}</option>
                  ))}
                </select>
                <button className={`sw-toggle ${V.readAloud ? "on" : ""}`}
                  onClick={() => V.readAloud ? (V.stopSpeaking(), V.setReadAloud(false))
                                             : V.setReadAloud(true)}
                  title={V.readAloud ? "Jawab bola jaayega — band karne ke liye dabaiye"
                                     : "Jawab sunne ke liye dabaiye"}>
                  {V.readAloud ? <Volume2 size={12} /> : <VolumeX size={12} />}
                  {V.readAloud ? "sunaayenge" : "sirf likha"}
                </button>
                {V.speaking && (
                  <button className="sw-toggle" onClick={V.stopSpeaking}>
                    <Square size={10} />rukiye
                  </button>
                )}
              </div>
            </div>
          </>
        )}
      </div>

      {V.denied && (
        <div className="sw-denied">
          Mic band hai. Browser ki settings mein microphone allow kijiye — ya neeche likh kar
          poochhiye.
        </div>
      )}

      {!typing ? (
        <button className="sw-typelink" onClick={() => setTyping(true)}>
          <Keyboard size={11} />likh kar poochhna hai?
        </button>
      ) : (
      <div style={{ display: "flex", gap: 7, padding: "10px 12px", borderTop: "1px solid var(--line-soft)" }}>
        <input value={input} onChange={(e) => setInput(e.target.value)} autoFocus
          onKeyDown={(e) => { if (e.key === "Enter") send(); }}
          placeholder={partnerId ? "Ask about your load…" : "no partner mapped to this hub"}
          disabled={busy || !partnerId}
          style={{ flex: 1, background: "var(--surface-0)", border: "1px solid var(--line)",
            borderRadius: 8, padding: "8px 10px", color: "var(--text)", fontSize: 12.5,
            fontFamily: "inherit", outline: "none" }} />
        <button className="icon-btn" onClick={() => send()} disabled={busy || !partnerId}
          title="Send" style={{ width: 34, height: 34 }}><Send size={14} /></button>
      </div>
      )}
    </div>
  );
}


export { SupportWidget, MyCases };
