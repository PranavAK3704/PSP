import { useMemo, useRef, useState } from "react";
import { Send, Paperclip, X, Lock, Eye, AlertTriangle, CornerUpLeft,
         CheckCircle2, Ban } from "lucide-react";

/* ── Where an L3 member actually writes a resolution ──────────────────────────────────────────

   IT WAS A `window.prompt`. One line, no formatting, no attachment, no second field, and the
   browser's own dialog styling. The consequence is measurable rather than theoretical: all four
   real resolutions in the concern log are the literal string **"done"**. That is not laziness,
   it is what the input asked for — a one-line dialog with no context on screen produces one-word
   answers, and "done" is what a captain who has waited a day for their money receives.

   ── SO THIS SPLITS ONE FIELD INTO THE FOUR THINGS A RESOLUTION ACTUALLY IS ──────────────────

   1. THE PARTNER-FACING MESSAGE. Validated: non-empty, 25+ characters, and rejected outright if
      it is one of the twenty non-answers ("done", "ok", "fixed", "sorted", ...). Validated on
      the SERVER too — this component is one client and the ledger is permanent.

   2. AN INTERNAL NOTE that never reaches the captain. Without somewhere to put it, the
      partner-facing message becomes the dumping ground for both, and a captain gets "checked
      w/ finance, ref TKT-882, prob a scan issue at NRL, told him next cycle" instead of an
      answer. This is what one L3 member tells the next one.

   3. ATTACHMENTS — the evidence relied on. Metadata only (filename, mime, size); the file is
      never stored, exactly as on the captain-upload path. An L3 member resolving a shortage has
      a CCTV still or a POD, and a resolution that cannot reference it is a resolution nobody can
      audit later.

   4. AN OUTCOME, because "resolved" is not the only thing that happens:
        · resolved   — closes the case
        · need_input — answers the captain and LEAVES THE CASE OPEN. They were missing a photo,
                       an AWB, a date. Collapsing this into "resolved" is how a captain ends up
                       waiting on a case nobody is working.
        · rejected   — closes it with a reason they can read

   ── AND A PREVIEW, WHICH IS THE POINT ───────────────────────────────────────────────────────
   The composer shows exactly what the captain will see, in their bubble, as it is typed. The
   fastest way to stop somebody sending "done" is to show them "done" in the place the captain
   reads it.

   Templates are per-disposition and deliberately INCOMPLETE — each leaves a bracketed blank
   ("[amount]", "[date]") so it cannot be sent as-is without reading it. A complete template is a
   one-click way to send something untrue. ── */

const OUTCOMES = [
  { key: "resolved",   label: "Resolved",        icon: CheckCircle2, tone: "good",
    hint: "Closes the case. The captain sees your message on their panel." },
  { key: "need_input", label: "Need info",       icon: CornerUpLeft, tone: "warn",
    hint: "Answers the captain but KEEPS the case open — use when something is missing." },
  { key: "rejected",   label: "Not upheld",      icon: Ban,          tone: "bad",
    hint: "Closes the case as not upheld. Say why — they will read it." },
];

/* Keyed on the engine's disposition. The bracketed blanks are load-bearing. */
const TEMPLATES = {
  hardstop_loss: [
    ["Reversal raised", "Checked the records for [AWB]. The debit of ₹[amount] was raised in error — reversal is with Finance and the credit lands in your [next/current] payment cycle."],
    ["Debit upheld", "Checked [AWB]. The shortage was confirmed at the destination facility on [date] and the debit stands. If you have CCTV from within 72 hours you can still contest it."],
  ],
  shortage_loss: [
    ["Evidence accepted", "Reviewed the CCTV you shared for [AWB]. The shortage was not at your DC — the debit of ₹[amount] is being reversed."],
    ["Evidence needed", "To take this further I need valid CCTV covering the outscan of [AWB] on [date], uploaded within 72 hours of the debit. Please share it via the captain portal."],
  ],
  load_planning: [
    ["Explained", "Your allocation for [date] was reduced because [RTO%/pendency] was above target. Bringing it under [target] restores the volume within [n] days."],
  ],
  cod_pendency: [
    ["Cleared", "Your COD pendency of ₹[amount] has been reconciled against the deposit dated [date]. Nothing further is outstanding."],
  ],
  _default: [
    ["Resolved", "Looked into this. [What was found.] [What happens next, and by when.]"],
  ],
};

const NON_ANSWERS = new Set(["done", "ok", "okay", "fixed", "resolved", "closed", "na", "n/a",
  "-", "yes", "no", "completed", "complete", "sorted", "handled", "actioned", "yep", "k"]);
const MIN = 25;

/** Mirrors l3.validate_reply. Duplicated deliberately — the server is the authority, but a
 *  round trip to be told your message is too short is a bad way to learn it. */
function validate(text) {
  const t = (text || "").trim();
  if (!t) return "A message for the captain is required.";
  if (NON_ANSWERS.has(t.toLowerCase().replace(/[.!]+$/, "")))
    return `“${t}” is not an answer. Say what was found and what happens next.`;
  if (t.length < MIN) return `Too short (${t.length}/${MIN}) — say what you did.`;
  return "";
}

export default function ResolutionComposer({ item, onSend, busy, error }) {
  const [outcome, setOutcome] = useState("resolved");
  const [reply, setReply] = useState("");
  const [internal, setInternal] = useState("");
  const [atts, setAtts] = useState([]);
  const [touched, setTouched] = useState(false);
  const fileRef = useRef(null);

  const templates = TEMPLATES[item?.disposition] || TEMPLATES._default;
  const problem = validate(reply);
  const blanks = useMemo(() => (reply.match(/\[[^\]]+\]/g) || []), [reply]);
  const canSend = !problem && !blanks.length && !busy;

  function addFiles(list) {
    // Metadata only. The file object is kept just long enough to read name/type/size and is
    // never uploaded — the same contract as the captain's own attachment path.
    setAtts((a) => [...a, ...Array.from(list).map((f) => ({
      filename: f.name, mime: f.type || "application/octet-stream", size: f.size }))]);
  }

  return (
    <div className="rc">
      {/* ── outcome ── */}
      <div className="rc-row">
        <span className="rc-label">Outcome</span>
        <div className="rc-outcomes">
          {OUTCOMES.map((o) => {
            const Icon = o.icon;
            return (
              <button key={o.key} type="button" title={o.hint}
                className={`rc-outcome ${o.tone} ${outcome === o.key ? "on" : ""}`}
                onClick={() => setOutcome(o.key)}>
                <Icon size={13} />{o.label}
              </button>
            );
          })}
        </div>
      </div>
      <div className="rc-hint">{OUTCOMES.find((o) => o.key === outcome).hint}</div>

      {/* ── templates ── */}
      <div className="rc-row" style={{ marginTop: 12 }}>
        <span className="rc-label">Start from</span>
        <div className="rc-templates">
          {templates.map(([name, body]) => (
            <button key={name} type="button" className="rc-tmpl"
              onClick={() => { setReply(body); setTouched(true); }}>{name}</button>
          ))}
          {reply && <button type="button" className="rc-tmpl clear"
            onClick={() => setReply("")}>clear</button>}
        </div>
      </div>

      {/* ── the partner-facing message ── */}
      <label className="rc-field">
        <span className="rc-field-head">
          <Eye size={12} /> Message to the captain
          <em>they read this — in Hindi/Hinglish if that is how they wrote to you</em>
        </span>
        <textarea rows={4} value={reply} placeholder="What you found, and what happens next."
          onChange={(e) => { setReply(e.target.value); setTouched(true); }} />
        <span className="rc-meta">
          <span className={reply.trim().length < MIN ? "warn" : ""}>
            {reply.trim().length} chars</span>
          {blanks.length > 0 && (
            <span className="bad"><AlertTriangle size={11} />
              fill in {blanks.join(" ")} before sending</span>
          )}
        </span>
      </label>

      {/* ── the internal note ── */}
      <label className="rc-field">
        <span className="rc-field-head">
          <Lock size={12} /> Internal note
          <em>never shown to the captain — for whoever picks this up next</em>
        </span>
        <textarea rows={2} value={internal} placeholder="Ticket refs, who you spoke to, what to watch for."
          onChange={(e) => setInternal(e.target.value)} />
      </label>

      {/* ── attachments ── */}
      <div className="rc-row" style={{ marginTop: 4 }}>
        <button type="button" className="rc-attach" onClick={() => fileRef.current?.click()}>
          <Paperclip size={12} /> Attach evidence
        </button>
        <input ref={fileRef} type="file" multiple hidden
          onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
        <span className="rc-hint" style={{ margin: 0 }}>
          filename, type and size are recorded — the file itself is not stored
        </span>
      </div>
      {atts.length > 0 && (
        <div className="rc-atts">
          {atts.map((a, i) => (
            <span key={i} className="rc-att">
              {a.filename} <b>{(a.size / 1024).toFixed(0)}KB</b>
              <button type="button" onClick={() => setAtts((x) => x.filter((_, j) => j !== i))}
                aria-label={`Remove ${a.filename}`}><X size={10} /></button>
            </span>
          ))}
        </div>
      )}

      {/* ── the preview: what the captain will actually see ── */}
      <div className="rc-preview">
        <div className="rc-preview-head">On the captain's panel</div>
        <div className="rc-bubble">
          {reply.trim() || <i>your message appears here</i>}
          <div className="rc-bubble-foot">
            {item?.concern_id} · {outcome === "need_input" ? "still open" : "resolved"}
            {atts.length > 0 && ` · ${atts.length} attachment${atts.length > 1 ? "s" : ""}`}
          </div>
        </div>
      </div>

      {(touched && problem) || error ? (
        <div className="rc-error"><AlertTriangle size={13} />{error || problem}</div>
      ) : null}

      <button className="rc-send" disabled={!canSend}
        onClick={() => onSend({ reply: reply.trim(), internal: internal.trim(), outcome,
                                attachments: atts })}>
        <Send size={14} />
        {busy ? "Sending…"
              : outcome === "need_input" ? "Send to captain · keep case open"
              : "Send to captain · close case"}
      </button>
      {/* Honest label: nothing is pushed. The captain's panel polls every 6s. */}
      <div className="rc-hint" style={{ textAlign: "center", marginTop: 6 }}>
        appears on their panel within ~6s · no notification is pushed
      </div>
    </div>
  );
}
