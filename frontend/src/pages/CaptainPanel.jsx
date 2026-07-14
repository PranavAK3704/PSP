import React, { useEffect, useRef, useState } from "react";
import { Send, Mic, MicOff, Cpu, Radio, Plus, ThumbsUp, ThumbsDown, Paperclip, X,
  ChevronDown, ChevronRight, Clock, CheckCircle2, MessageSquare, FolderOpen, Trash2 } from "lucide-react";

// Indic voice-input languages (Web Speech API BCP-47 codes). North = Hindi/English;
// South + Maharashtra need their own — captains are across all regions.
const VOICE_LANGS = [
  ["hi-IN", "हिंदी"], ["en-IN", "English"], ["mr-IN", "मराठी"], ["ta-IN", "தமிழ்"],
  ["te-IN", "తెలుగు"], ["kn-IN", "ಕನ್ನಡ"], ["ml-IN", "മലയാളം"], ["bn-IN", "বাংলা"],
];
import Pipeline from "../components/Pipeline.jsx";
import DecisionCore from "../components/DecisionCore.jsx";
import { stream, getCaptains, sendSatisfaction, getCaptainCases } from "../lib/api.js";
import { useChatStore } from "../lib/chatStore.jsx";

// tiny, safe markdown → HTML for bot replies (bold, bullets, links, breaks).
function mdToHtml(s) {
  let h = (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  h = h.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  h = h.replace(/(https?:\/\/\S+)/g,
    '<a href="$1" target="_blank" rel="noreferrer" style="color:var(--signal);word-break:break-all">$1</a>');
  h = h.replace(/\n{2,}/g, "<br><br>").replace(/\n/g, "<br>");
  h = h.replace(/(^|<br>|\s)[*-]\s+/g, "$1<br>• ");
  return h;
}

// Grow a textarea to fit its content, capped (then it scrolls). Keeps the composer single-line
// until the captain actually needs more room.
function autoGrow(el) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 140) + "px";
}

// Read a File into attachment metadata + (for images) a small downscaled thumbnail dataURL.
function readAttachment(file) {
  return new Promise((resolve) => {
    const base = { filename: file.name, mime: file.type || "file", size: file.size, thumb: null };
    if (!file.type?.startsWith("image/")) return resolve(base);
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        const max = 256, scale = Math.min(1, max / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * scale); c.height = Math.round(img.height * scale);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        resolve({ ...base, thumb: c.toDataURL("image/jpeg", 0.6) });
      };
      img.onerror = () => resolve(base);
      img.src = reader.result;
    };
    reader.onerror = () => resolve(base);
    reader.readAsDataURL(file);
  });
}

export default function CaptainPanel() {
  const [captains, setCaptains] = useState([]);
  const [captainId, setCaptainId] = useState("VLMO-CPT-4471");
  const [events, setEvents] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [phase, setPhase] = useState("idle");
  const [recording, setRecording] = useState(false);
  const [voiceLang, setVoiceLang] = useState(() => localStorage.getItem("valmo.voiceLang") || "hi-IN");
  const [attachments, setAttachments] = useState([]);
  const [railOpen, setRailOpen] = useState(true);
  const [cases, setCases] = useState([]);
  const [casesOpen, setCasesOpen] = useState(false);   // slim summary by default; expand on demand
  const [flash, setFlash] = useState({});          // CN → true when it just resolved
  const scrollRef = useRef(null);
  const recRef = useRef(null);
  const fileRef = useRef(null);
  const taRef = useRef(null);
  const prevResolved = useRef(new Set());

  const store = useChatStore();
  useEffect(() => { store.ensure(captainId); }, [captainId]);
  const conversations = store.getConversations(captainId);
  const active = store.getActive(captainId);
  const messages = active.messages;
  const setMessages = (updater) => store.setMessages(captainId, updater);

  useEffect(() => { getCaptains().then((d) => setCaptains(d.captains || [])); }, []);
  useEffect(() => { scrollRef.current?.scrollTo(0, scrollRef.current.scrollHeight); }, [messages]);

  // Poll "My Cases" so an L3 resolution appears live (without a refresh). Reset baseline per captain.
  useEffect(() => {
    if (!captainId) return;
    prevResolved.current = new Set();
    let alive = true;
    const pull = () => getCaptainCases(captainId).then((d) => {
      if (!alive) return;
      const list = d.cases || [];
      const nowResolved = new Set(list.filter((c) => c.status === "resolved").map((c) => c.id));
      const fresh = {};
      nowResolved.forEach((id) => { if (!prevResolved.current.has(id)) fresh[id] = true; });
      if (Object.keys(fresh).length) { setFlash((f) => ({ ...f, ...fresh })); setCasesOpen(true);
        setTimeout(() => setFlash((f) => { const n = { ...f }; Object.keys(fresh).forEach((k) => delete n[k]); return n; }), 6000); }
      prevResolved.current = nowResolved;
      setCases(list);
    }).catch(() => {});
    pull();
    const t = setInterval(pull, 6000);
    return () => { alive = false; clearInterval(t); };
  }, [captainId]);

  async function send(text) {
    const msg = (text ?? input).trim();
    if ((!msg && attachments.length === 0) || busy) return;
    const atts = attachments;
    setInput(""); setAttachments([]);
    if (taRef.current) taRef.current.style.height = "auto";   // collapse the composer back to one line
    setMessages((m) => [...m, { who: "captain", text: msg, atts }]);
    setEvents([]);
    setBusy(true); setPhase("thinking");
    let cid = active.convId;
    if (!cid) { cid = crypto.randomUUID(); store.setConvId(captainId, cid); }
    await stream(
      { url: "/api/chat", method: "POST",
        body: { captain_id: captainId, message: msg, conversation_id: cid,
                attachments: atts.map((a) => ({ filename: a.filename, mime: a.mime, size: a.size })) } },
      (ev) => {
        if (ev.node === "reply") {
          setMessages((m) => [...m, { who: "bot", text: ev.data?.reply || ev.detail,
            action: ev.data?.decision_action, concernId: ev.data?.concern_id }]);
          setPhase("resolved");
        } else {
          setEvents((prev) => [...prev, ev]);
        }
      },
      () => { setBusy(false); getCaptainCases(captainId).then((d) => setCases(d.cases || [])).catch(() => {}); }
    );
  }

  async function pickFiles(e) {
    const files = Array.from(e.target.files || []);
    const read = await Promise.all(files.map(readAttachment));
    setAttachments((a) => [...a, ...read]);
    e.target.value = "";
  }

  async function rate(idx, concernId, satisfied) {
    const note = satisfied ? "" : (window.prompt("Kya missing tha? (helps us improve)") || "");
    await sendSatisfaction(concernId, captainId, satisfied, note);
    setMessages((m) => m.map((msg, i) => i === idx ? { ...msg, rated: satisfied ? "up" : "down" } : msg));
  }

  function toggleMic() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { alert("Voice input needs Chrome/Edge (Web Speech API)."); return; }
    if (recording) { recRef.current?.stop(); return; }
    const rec = new SR();
    rec.lang = voiceLang; rec.interimResults = true; rec.continuous = false;
    rec.onresult = (e) => setInput(Array.from(e.results).map((r) => r[0].transcript).join(""));
    rec.onend = () => setRecording(false);
    rec.onerror = () => setRecording(false);
    recRef.current = rec; rec.start(); setRecording(true);
  }

  const openCases = cases.filter((c) => c.status === "open").length;

  return (
    <div className="split">
      {/* ── Left: rail + chat ── */}
      <div className="card" style={{ display: "flex", flexDirection: "row", overflow: "hidden" }}>
        {/* Conversation rail */}
        <div style={{ width: railOpen ? 156 : 0, transition: "width .2s", overflow: "hidden",
          borderRight: railOpen ? "1px solid var(--line-soft)" : "none", flexShrink: 0,
          display: "flex", flexDirection: "column", background: "var(--surface-0)" }}>
          <div style={{ padding: 10 }}>
            <button className="icon-btn" onClick={() => store.newConversation(captainId)}
              title="New chat" style={{ width: "100%", height: 34, gap: 6, fontSize: 11, justifyContent: "center" }}>
              <Plus size={14} /> New chat
            </button>
          </div>
          <div style={{ overflow: "auto", flex: 1, padding: "0 8px 8px" }}>
            {conversations.map((c) => (
              <div key={c.id} onClick={() => store.switchConversation(captainId, c.id)}
                className="mono conv-item" style={{ padding: "8px 9px", borderRadius: 8, cursor: "pointer", fontSize: 11,
                  marginBottom: 4, display: "flex", gap: 6, alignItems: "center", lineHeight: 1.3,
                  color: c.id === active.id ? "var(--signal)" : "var(--text-mute)",
                  background: c.id === active.id ? "var(--signal-soft)" : "transparent",
                  border: c.id === active.id ? "1px solid var(--signal-line)" : "1px solid transparent" }}>
                <MessageSquare size={12} style={{ flexShrink: 0, opacity: 0.7 }} />
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>{c.title}</span>
                <button title="Delete chat" onClick={(e) => { e.stopPropagation(); store.deleteConversation(captainId, c.id); }}
                  className="conv-del" style={{ background: "none", border: "none", cursor: "pointer", padding: 0,
                    color: "var(--text-faint)", display: "grid", placeItems: "center", flexShrink: 0 }}>
                  <Trash2 size={12} /></button>
              </div>
            ))}
          </div>
        </div>

        {/* Chat column */}
        <div className="chat-wrap" style={{ flex: 1, minWidth: 0 }}>
          <div className="card-head">
            <h3>
              <button className="icon-btn" onClick={() => setRailOpen((v) => !v)} title="Conversations"
                style={{ width: 26, height: 26, marginRight: 2 }}><MessageSquare size={13} /></button>
              <Radio size={15} /> Captain Panel · Advocate
            </h3>
            <select value={captainId} onChange={(e) => setCaptainId(e.target.value)}
              className="mono" style={{ background: "var(--ink-0)", color: "var(--text-mute)",
                border: "1px solid var(--line)", borderRadius: 8, padding: "5px 8px", fontSize: 11 }}>
              {captains.map((c) => <option key={c.captain_id} value={c.captain_id}>{c.name} · {c.hub_name}</option>)}
            </select>
          </div>

          {/* My Cases widget — persistent, live-polled, expandable */}
          {cases.length > 0 && (
            <div style={{ borderBottom: "1px solid var(--line-soft)", background: "var(--surface-0)" }}>
              <div onClick={() => setCasesOpen((v) => !v)}
                style={{ padding: "9px 14px", display: "flex", alignItems: "center", gap: 8, cursor: "pointer",
                  fontFamily: "var(--mono)", fontSize: 11, color: "var(--text-mute)" }}>
                {casesOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                <FolderOpen size={13} style={{ color: "var(--signal)" }} />
                <span style={{ color: "var(--text)" }}>My cases</span>
                <span>· {cases.length}</span>
                {openCases > 0 && <span style={{ color: "var(--warn)" }}>· {openCases} open</span>}
              </div>
              {casesOpen && (
                <div style={{ padding: "0 12px 10px", display: "flex", flexDirection: "column", gap: 7, maxHeight: 190, overflow: "auto" }}>
                  {cases.map((c) => {
                    const resolved = c.status === "resolved";
                    return (
                      <div key={c.id} style={{ padding: "9px 11px", borderRadius: 9, fontSize: 12,
                        border: "1px solid " + (flash[c.id] ? "var(--good)" : "var(--line)"),
                        background: flash[c.id] ? "rgba(0,228,117,0.10)" : "var(--surface-2)",
                        transition: "background .5s, border-color .5s" }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                          <span className="mono" style={{ fontSize: 10.5, color: "var(--text-mute)" }}>{c.id}</span>
                          <span className="mono" style={{ fontSize: 9.5, fontWeight: 700, padding: "2px 7px", borderRadius: 20,
                            display: "inline-flex", alignItems: "center", gap: 4,
                            color: resolved ? "var(--good)" : "var(--warn)",
                            background: resolved ? "rgba(0,228,117,0.12)" : "rgba(255,191,0,0.12)" }}>
                            {resolved ? <><CheckCircle2 size={11} /> RESOLVED</> : <><Clock size={11} /> OPEN · ~{c.eta_hours}h</>}
                          </span>
                        </div>
                        <div style={{ marginTop: 5, color: "var(--text)" }}>{c.intent}</div>
                        <div className="mono" style={{ marginTop: 3, fontSize: 9.5, color: "var(--text-faint)" }}>
                          {c.team}{c.entities?.awb ? " · AWB " + c.entities.awb : ""}{c.attachments?.length ? ` · 📎 ${c.attachments.length}` : ""}
                        </div>
                        {resolved && c.resolution_note && (
                          <div style={{ marginTop: 7, paddingTop: 7, borderTop: "1px dashed var(--line)",
                            color: "var(--good)", fontSize: 11.5 }}>✓ {c.resolution_note}</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          <div className="chat-scroll" ref={scrollRef}>
            {messages.length === 0 && (
              <div className="msg system">Namaste 👋 Aapki koi bhi problem — bataiye. Voice, text ya photo, kisi bhi bhaasha mein.</div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`msg ${m.who}`}>
                <div className="who">{m.who === "captain" ? "You" : "Valmo Advocate"}</div>
                {m.who === "bot"
                  ? <span dangerouslySetInnerHTML={{ __html: mdToHtml(m.text) }} />
                  : m.text}
                {m.atts?.length > 0 && (
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: m.text ? 8 : 0 }}>
                    {m.atts.map((a, k) => a.thumb
                      ? <img key={k} src={a.thumb} alt={a.filename} title={a.filename}
                          style={{ width: 84, height: 84, objectFit: "cover", borderRadius: 8, border: "1px solid var(--line)" }} />
                      : <span key={k} className="mono" style={{ fontSize: 10, padding: "5px 9px", borderRadius: 8,
                          border: "1px solid var(--line)", background: "var(--surface-0)", display: "inline-flex", gap: 5, alignItems: "center" }}>
                          <Paperclip size={11} /> {a.filename}</span>)}
                  </div>
                )}
                {m.action && m.action !== "need_input" && (
                  <div className="evidence-chip">
                    {m.action === "reverse_debit" ? "✓ Debit reversed in-conversation"
                      : m.action === "clear_pendency" ? "✓ Pendency corrected"
                      : m.action === "escalate" ? <>→ Escalated with worked case{m.concernId && <> · ref <span className="mono">{m.concernId}</span></>}</>
                      : m.action === "respond" ? "ℹ Answered from SOP knowledge" : "✓ Resolved"}
                  </div>
                )}
                {m.who === "bot" && m.concernId && !m.rated && (
                  <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center" }}>
                    <span className="mono faint" style={{ fontSize: 10 }}>Helpful?</span>
                    <button className="icon-btn" style={{ width: 30, height: 30 }}
                      onClick={() => rate(i, m.concernId, true)}><ThumbsUp size={13} /></button>
                    <button className="icon-btn" style={{ width: 30, height: 30 }}
                      onClick={() => rate(i, m.concernId, false)}><ThumbsDown size={13} /></button>
                  </div>
                )}
                {m.rated && <div className="mono faint" style={{ fontSize: 10, marginTop: 8 }}>
                  {m.rated === "up" ? "✓ thanks for the feedback" : "✓ logged for CPD — we'll improve this"}</div>}
              </div>
            ))}
            {busy && <div className="msg system">● engine resolving…</div>}
          </div>

          {/* attachment chips */}
          {attachments.length > 0 && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", padding: "10px 13px 0" }}>
              {attachments.map((a, k) => (
                <div key={k} style={{ position: "relative", display: "inline-flex", gap: 6, alignItems: "center",
                  padding: a.thumb ? 0 : "5px 9px", borderRadius: 8, border: "1px solid var(--line)",
                  background: "var(--surface-0)", fontSize: 10 }} className="mono">
                  {a.thumb
                    ? <img src={a.thumb} alt={a.filename} style={{ width: 44, height: 44, objectFit: "cover", borderRadius: 8 }} />
                    : <><Paperclip size={11} /> {a.filename}</>}
                  <button onClick={() => setAttachments((s) => s.filter((_, j) => j !== k))}
                    style={{ position: "absolute", top: -6, right: -6, width: 16, height: 16, borderRadius: 20,
                      background: "var(--bad)", color: "#000", border: "none", cursor: "pointer", display: "grid", placeItems: "center" }}>
                    <X size={10} /></button>
                </div>
              ))}
            </div>
          )}

          <div className="composer">
            <input ref={fileRef} type="file" multiple accept="image/*,.pdf,.jpg,.jpeg,.png,.webp"
              style={{ display: "none" }} onChange={pickFiles} />
            <button className="icon-btn" onClick={() => fileRef.current?.click()} title="Attach photo / file" disabled={busy}>
              <Paperclip size={17} /></button>
            <button className={`icon-btn ${recording ? "rec" : ""}`} onClick={toggleMic} title={`Voice · ${voiceLang}`}>
              {recording ? <MicOff size={17} /> : <Mic size={17} />}
            </button>
            <select value={voiceLang} title="Voice language"
              onChange={(e) => { setVoiceLang(e.target.value); localStorage.setItem("valmo.voiceLang", e.target.value); }}
              className="mono" style={{ background: "var(--surface-0)", color: "var(--text-mute)",
                border: "1px solid var(--line)", borderRadius: 8, padding: "0 6px", fontSize: 11, height: 42 }}>
              {VOICE_LANGS.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
            </select>
            <textarea ref={taRef} value={input} rows={1}
              onChange={(e) => { setInput(e.target.value); autoGrow(e.target); }}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
              placeholder="Type in Hinglish, Hindi or English…  (Shift+Enter for a new line)" disabled={busy} />
            <button className="icon-btn send" onClick={() => send()} disabled={busy}><Send size={17} /></button>
          </div>
        </div>
      </div>

      {/* ── Right: live Resolution Engine ── */}
      <div className="card" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div className="card-head">
          <h3><Cpu size={15} /> Resolution Engine · live trace</h3>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="mono faint" style={{ fontSize: 10 }}>
              {phase === "thinking" ? "thinking…" : phase === "resolved" ? "resolved" : "idle"}</span>
            <div style={{ width: 34, height: 34 }}><DecisionCore size={34} state={phase} /></div>
          </div>
        </div>
        <div style={{ overflow: "auto", padding: "14px 16px", flex: 1 }}>
          <Pipeline events={events} />
        </div>
      </div>
    </div>
  );
}
