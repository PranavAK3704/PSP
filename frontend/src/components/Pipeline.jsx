import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import DecisionCore from "./DecisionCore.jsx";
import {
  Mic, Search, Database, GitBranch, ShieldCheck, Scale, Swords,
  Zap, MessageSquareHeart, Archive, AlertTriangle, CheckCircle2, XCircle,
  CircleDot, Sparkles, Cpu, BookOpen, HelpCircle, Clock,
} from "lucide-react";

const ICONS = {
  capture: Mic, intent: Search, ground: Database, disposition: GitBranch,
  policy: ShieldCheck, gate: Scale, verify: Swords, act: Zap, escalate: AlertTriangle,
  knowledge: BookOpen, explain: MessageSquareHeart, learn: Archive, resolved: CheckCircle2,
  gather: HelpCircle, need_input: Clock, friction: AlertTriangle, query: Database,
  stream: Database, firstpass: Search, compose: Sparkles, nudge: MessageSquareHeart, clear: CheckCircle2,
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

      {ev.node === "ground" && d.profile && (
        <div className="kv">
          <span className="tag">{d.profile.name}</span>
          <span className="tag">{d.profile.hub_name}</span>
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

      {ev.node === "query" && (
        <div className="kv">
          <span className="tag" style={{ color: "var(--info)" }}>query: {d.query}</span>
          <span className="tag">{(d.rows || []).length} row(s)</span>
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

  if (!nodes.length) {
    return (
      <div className="empty">
        <div>
          <DecisionCore size={220} />
          <div className="big" style={{ marginTop: 4 }}>Decision core · idle</div>
          <div style={{ fontSize: 13 }}>Send a captain message — watch every stage resolve, live.</div>
        </div>
      </div>
    );
  }

  return (
    <div className="pipeline">
      <AnimatePresence>
        {nodes.map((ev, i) => {
          const Icon = ICONS[ev.node] || CircleDot;
          const state = ev.status === "running" ? "running"
            : ev.node === "gate" && ev.data && !ev.data.passed ? "blocked"
            : ev.node === "escalate" ? "blocked" : "done";
          return (
            <motion.div key={ev.node} className={`pnode ${state}`}
              initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
              <div className="rail">
                <div className="bead"><Icon size={15} /></div>
                {i < nodes.length - 1 && <div className="wire" />}
              </div>
              <div className="pbody"><NodeBody ev={ev} /></div>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
