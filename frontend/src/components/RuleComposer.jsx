import { useState } from "react";
import { Wand2, X, Plus } from "lucide-react";

/* ── "This should have been resolved" — capture a rule, in plain language ─────────────────────

   Two more `window.prompt`s, back to back: one for the rule, one for a comma-separated list of
   required inputs. Chaining two dialogs is worse than one — cancel the second and the first is
   already gone, and a comma-separated list typed into a browser dialog is a data-entry format
   from 1997.

   What this feeds is not a ticket. It goes to the KT approval queue, gets compiled into a policy,
   and the engine follows it next time — so the text is closer to a spec than a comment, and the
   `required_inputs` are what the engine will ASK THE CAPTAIN FOR before it tries. Getting those
   wrong means the engine asks for the wrong thing, so they are chips you can see and remove
   rather than a string you have to punctuate correctly.

   Deliberately NOT free-text-only: the example is on screen, because "add a rule" with an empty
   box produces "reverse it faster". ── */

const EXAMPLES = [
  "If a shortage debit has valid CCTV from within 72 hours showing the outscan, reverse it without escalating.",
  "If COD pendency is under ₹500 and older than 7 days, write it off and tell the captain it is cleared.",
];

export default function RuleComposer({ item, onSubmit, onCancel }) {
  const [text, setText] = useState("");
  const [inputs, setInputs] = useState([]);
  const [draft, setDraft] = useState("");

  const addInput = () => {
    const v = draft.trim();
    if (v && !inputs.includes(v)) setInputs((a) => [...a, v]);
    setDraft("");
  };

  return (
    <div className="rc">
      <div className="rc-field-head" style={{ marginBottom: 2 }}>
        <Wand2 size={13} /> Rule for next time
        <em>plain language — no code. Goes to the KT approval queue for {item?.disposition}.</em>
      </div>
      <textarea rows={3} value={text} placeholder="If <situation>, then <what the engine should do>."
        onChange={(e) => setText(e.target.value)} className="rc-ta" />
      <div className="rc-hint" style={{ marginLeft: 0 }}>
        For example: <i>{EXAMPLES[0]}</i>
      </div>

      <div className="rc-field" style={{ marginTop: 6 }}>
        <span className="rc-field-head">
          Required from the captain
          <em>what the engine must ASK FOR before it applies this — get these wrong and it asks
              for the wrong thing</em>
        </span>
        <div className="rc-row" style={{ gap: 6 }}>
          <input value={draft} placeholder="e.g. AWB, CCTV within 72h, deposit date"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addInput(); } }}
            className="rc-in" />
          <button type="button" className="rc-tmpl" onClick={addInput}><Plus size={11} /> add</button>
        </div>
        {inputs.length > 0 && (
          <div className="rc-atts" style={{ marginLeft: 0 }}>
            {inputs.map((v) => (
              <span key={v} className="rc-att">{v}
                <button type="button" onClick={() => setInputs((a) => a.filter((x) => x !== v))}
                  aria-label={`Remove ${v}`}><X size={10} /></button>
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="rc-row" style={{ marginTop: 8, gap: 8 }}>
        <button className="rc-send" style={{ flex: 1, marginTop: 0 }}
          disabled={text.trim().length < 20}
          onClick={() => onSubmit({ text: text.trim(), required: inputs })}>
          <Wand2 size={14} /> Send to KT approval
        </button>
        <button className="rc-tmpl clear" style={{ padding: "11px 16px" }}
          onClick={onCancel}>cancel</button>
      </div>
      {text.trim().length > 0 && text.trim().length < 20 && (
        <div className="rc-hint" style={{ marginLeft: 0 }}>
          {text.trim().length}/20 characters — a rule needs a condition and an action.
        </div>
      )}
    </div>
  );
}
