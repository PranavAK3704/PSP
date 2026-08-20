import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Mic, Search, Database, GitBranch, ShieldCheck, Scale, Swords,
  Zap, MessageSquareHeart, Archive, AlertTriangle, CheckCircle2, XCircle,
  CircleDot, Sparkles, BookOpen, HelpCircle, Clock, ShieldAlert, Receipt,
} from "lucide-react";

const ICONS = {
  capture: Mic, intent: Search, ground: Database, disposition: GitBranch,
  policy: ShieldCheck, gate: Scale, verify: Swords, act: Zap, escalate: AlertTriangle,
  knowledge: BookOpen, explain: MessageSquareHeart, learn: Archive, resolved: CheckCircle2,
  gather: HelpCircle, need_input: Clock, friction: AlertTriangle, query: Database,
  stream: Database, firstpass: Search, compose: Sparkles, nudge: MessageSquareHeart, clear: CheckCircle2,
  // `guard` is its own node, NOT a second "ground" event: this list is collapsed by node id
  // below (latest wins), so two events sharing a name silently overwrite each other.
  guard: ShieldAlert, cost: Receipt,
};

export function ConfidenceDial({ value = 0, threshold = 0.8 }) {
  const r = 22, c = 2 * Math.PI * r;
  const pass = value >= threshold;
  const col = pass ? "var(--good)" : "var(--warn)";
  return (
    <div className="dial">
      <svg width="56" height="56">
        <circle cx="28" cy="28" r={r} fill="none" stroke="var(--ink-4)" strokeWidth="5" />
        <motion.circle
          cx="28" cy="28" r={r} fill="none" stroke={col} strokeWidth="5" strokeLinecap="round"
          strokeDasharray={c}
          initial={{ strokeDashoffset: c }}
          animate={{ strokeDashoffset: c * (1 - value) }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        />
      </svg>
      <div>
        <div className="dval" style={{ color: col }}>{Math.round(value * 100)}%</div>
        <div className="faint mono" style={{ fontSize: 9.5 }}>gate ≥ {Math.round(threshold * 100)}%</div>
      </div>
    </div>
  );
}

function CheckRows({ checks }) {
  return (checks || []).map((c, i) => (
    <div key={i} className={`check-row ${c.passed ? "pass" : "fail"}`}>
      <span className="ci">{c.passed ? <CheckCircle2 size={13} /> : <XCircle size={13} />}</span>
      <span className="cd"><b>{c.description}</b> — {c.result}</span>
    </div>
  ));
}

function Evidence({ trail }) {
  return (trail || []).map((e, i) => (
    <motion.div key={i} className="evidence-card"
      initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.08 }}>
      <div className="el">{e.label}</div>
      <div className="ev">{e.value}</div>
      <div className="er">◦ {e.ref} · via {e.source}</div>
    </motion.div>
  ));
}

function NodeBody({ ev }) {
  const d = ev.data || {};
  return (
    <>
      <div className="ptitle">
        {ev.label}
        {ev.tier && <span className={`tier-badge tier-${ev.tier}`}>{ev.tier === "deep" ? "Opus-tier" : "Haiku-tier"}</span>}
        {d.model && <span className="mono faint" style={{ fontSize: 9.5 }}>{d.model}</span>}
      </div>
      {ev.detail && <div className="pdetail">{ev.detail}</div>}

      {ev.node === "intent" && (
        <div className="kv">
          {d.entities?.amount_inr && <span className="tag">₹{d.entities.amount_inr}</span>}
          {d.entities?.awb && <span className="tag">AWB {d.entities.awb}</span>}
          {d.language && <span className="tag">{d.language}</span>}
          {(d.keywords || []).slice(0, 4).map((k, i) => <span key={i} className="tag">{k}</span>)}
        </div>
      )}

      {/* Grounding shows what the MODEL received — the aggregate — not the rows. The rows are in
          this event's `data.rows` for anyone replaying the concern, but they never left the box,
          which is the whole point of the projection (see backend engine/dataplane.py). Under the
          real-data provider there is no captain name at all: the provider refuses to invent one. */}
      {ev.node === "ground" && (d.aggregate || d.profile) && (
        <div className="kv">
          {d.aggregate ? (
            <>
              <span className="tag">{d.aggregate.debits_on_record} debits</span>
              <span className="tag">{d.aggregate.open_debits} open</span>
              {d.aggregate.total_debited_inr > 0 &&
                <span className="tag">₹{Number(d.aggregate.total_debited_inr).toLocaleString("en-IN")} debited</span>}
              {d.aggregate.reversals > 0 && <span className="tag">{d.aggregate.reversals} reversed</span>}
              {d.aggregate.hub && <span className="tag">hub {d.aggregate.hub}</span>}
              {d.aggregate.cod_data_available === false &&
                <span className="tag" style={{ color: "var(--warn)", borderColor: "var(--warn)" }}>no COD data</span>}
              {(d.rows?.losses || []).length > 0 &&
                <span className="tag" style={{ color: "var(--text-faint)" }}>
                  {(d.rows.losses || []).length} row(s) held locally
                </span>}
            </>
          ) : null}
          {d.profile?.name && <span className="tag">{d.profile.name}</span>}
          {d.ledger_lines != null && <span className="tag">{d.ledger_lines} ledger lines</span>}
          {/* `source` may be a string OR an object ({account, shipments}) — never render the raw
              object (React throws "Objects are not valid as a React child"). */}
          {d.source && (typeof d.source === "object"
            ? Object.entries(d.source).map(([k, v]) => <span key={k} className="tag">{k}: {String(v)}</span>)
            : <span className="tag">source: {d.source}</span>)}
        </div>
      )}

      {ev.node === "disposition" && (
        <div className="kv">
          {d.novel
            ? <span className="tag" style={{ color: "var(--warn)", borderColor: "var(--warn)" }}>NOVEL → CPD</span>
            : <><span className="tag">{d.disposition}</span>
                {d.policy_id && <span className="tag">{d.policy_id}</span>}
                <span className="tag">score {d.score}</span></>}
        </div>
      )}

      {ev.node === "policy" && (
        <div>
          <CheckRows checks={d.checks_run} />
          <Evidence trail={d.evidence_trail} />
        </div>
      )}

      {ev.node === "gate" && (
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 6 }}>
            <span className={`gate-lozenge ${d.passed ? "pass" : "block"}`}>
              {d.passed ? <CheckCircle2 size={13} /> : <XCircle size={13} />} {d.passed ? "GATE PASSED" : "BLOCKED"}
            </span>
            <ConfidenceDial value={d.confidence || 0} threshold={d.threshold || 0.8} />
          </div>
          <div style={{ marginTop: 8 }}>
            {(d.reasons || []).map((r, i) => (
              <div key={i} className="check-row pass"><span className="ci"><CheckCircle2 size={12} /></span>
                <span className="cd">{r}</span></div>
            ))}
            {(d.blocks || []).map((r, i) => (
              <div key={i} className="check-row fail"><span className="ci"><XCircle size={12} /></span>
                <span className="cd">{r}</span></div>
            ))}
          </div>
        </div>
      )}

      {ev.node === "verify" && ev.status === "done" && (
        <div className={`verdict ${d.agrees ? "agree" : "refute"}`}>
          {d.agrees ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
          <div>
            <div className="vt">{d.agrees ? "Verifier AGREES" : "Verifier REFUTES"}
              {d.confidence != null && <span className="mono faint"> · {Math.round(d.confidence * 100)}%</span>}</div>
            <div className="vr">{d.reason}</div>
          </div>
        </div>
      )}

      {ev.node === "escalate" && (
        <div className="kv">
          <span className="tag" style={{ color: "var(--warn)", borderColor: "var(--warn)" }}>→ {d.team}</span>
          <span className="tag">handover assembled</span>
        </div>
      )}

      {/* The composed answer is what the model got; the rows stayed here. */}
      {ev.node === "query" && (
        <>
          <div className="kv">
            <span className="tag" style={{ color: "var(--info)" }}>query: {d.query}</span>
            <span className="tag">{(d.rows || []).length} row(s)</span>
            <span className="tag" style={{ color: "var(--text-faint)" }}>composed in code</span>
          </div>
          {d.answer && (
            <div className="evidence-card" style={{ borderLeftColor: "var(--info)" }}>
              <div className="er">{d.answer}</div>
            </div>
          )}
        </>
      )}

      {/* What the turn actually cost, priced at list rate from the real token counts. */}
      {ev.node === "cost" && (
        <div className="kv">
          <span className="tag" style={{ color: "var(--good)" }}>${Number(d.cost_usd || 0).toFixed(4)}</span>
          <span className="tag">{d.calls} model call{d.calls === 1 ? "" : "s"}</span>
          <span className="tag">{Number(d.tokens_in || 0).toLocaleString()} in / {Number(d.tokens_out || 0).toLocaleString()} out</span>
          {d.budget_remaining_usd != null &&
            <span className="tag" style={{ color: "var(--text-faint)" }}>
              ${Number(d.budget_remaining_usd).toFixed(2)} budget left
            </span>}
        </div>
      )}

      {/* The data-plane guard only ever renders when something tried to cross the boundary. */}
      {ev.node === "guard" && (
        <div className="kv">
          {(d.leaks || []).slice(0, 4).map((l, i) => (
            <span key={i} className="tag" style={{ color: "var(--warn)", borderColor: "var(--warn)" }}>
              {l.kind} redacted
            </span>
          ))}
          {d.tool && <span className="tag">from {d.tool}</span>}
        </div>
      )}

      {ev.node === "knowledge" && (d.sources || []).length > 0 && (
        <div>
          {(d.sources || []).map((s, i) => (
            <div key={i} className="evidence-card" style={{ borderLeftColor: "var(--violet)" }}>
              <div className="el" style={{ color: "var(--violet)" }}>{s.title || s.kind}</div>
              <div className="er">◦ {s.kind} · {s.source_repo} · score {s.score}</div>
            </div>
          ))}
        </div>
      )}

      {ev.node === "explain" && d.reply && (
        <div className="evidence-card" style={{ borderLeftColor: "var(--signal)" }}>
          <div className="ev" style={{ color: "var(--text)" }}>{d.reply}</div>
        </div>
      )}

      {ev.node === "nudge" && (
        <div className="evidence-card" style={{ borderLeftColor: "var(--warn)" }}>
          <div className="el" style={{ color: "var(--warn)" }}>Shadow-first nudge · {d.risk?.awb}</div>
          <div className="ev" style={{ color: "var(--text)" }}>{d.nudge}</div>
        </div>
      )}
    </>
  );
}

export default function Pipeline({ events = [] }) {
  // collapse by node id, preserve first-seen order, keep latest state
  const order = [];
  const map = new Map();
  for (const e of (events || [])) {
    if (!map.has(e.node)) order.push(e.node);
    map.set(e.node, e);
  }
  const nodes = order.map((n) => map.get(n));

  // Reveal one stage at a time. The backend streams events, but several can land in a single
  // render — the pipeline then popped up fully formed, which reads as a canned screenshot
  // rather than a system working. A release queue decouples *arrival* from *appearance*: nodes
  // are held and let through on a timer, so a burst still unfolds step by step and the viewer
  // can follow what the engine is doing. Later stages reveal a little quicker so a long trace
  // does not drag.
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (!nodes.length) { setShown(0); return; }          // new turn — replay from the top
    if (shown >= nodes.length) return;
    const gap = shown === 0 ? 90 : shown < 4 ? 300 : 190;
    const t = setTimeout(() => setShown((n) => n + 1), gap);
    return () => clearTimeout(t);
  }, [shown, nodes.length]);

  const visible = nodes.slice(0, shown);

  // Nothing running: render NOTHING. The panel now starts collapsed and only opens when a turn
  // begins, so an "Engine idle / send a message" placeholder could only ever be seen by someone
  // who had opened the panel deliberately — telling them what they just did. An empty state that
  // states the obvious is worse than no empty state, so this returns null and the panel is bare.
  if (!nodes.length) return null;

  return (
    <div className="pipeline">
      <AnimatePresence>
        {visible.map((ev, i) => {
          const Icon = ICONS[ev.node] || CircleDot;
          const state = ev.status === "running" ? "running"
            : ev.node === "gate" && ev.data && !ev.data.passed ? "blocked"
            : ev.node === "escalate" ? "blocked" : "done";
          return (
            <motion.div key={ev.node} className={`pnode ${state}`}
              initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
              <div className="rail">
                <div className="bead"><Icon size={15} /></div>
                {i < visible.length - 1 && <div className="wire" />}
              </div>
              <div className="pbody"><NodeBody ev={ev} /></div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
