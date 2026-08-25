import { useState } from "react";
import { CheckCircle2, XCircle, AlertTriangle, Scale, ShieldCheck, ChevronRight } from "lucide-react";
import { CheckRows, ConfidenceDial, Evidence } from "./Pipeline.jsx";

/* ── TraceView — one record, three readers ─────────────────────────────────────────────────────

   THE PROBLEM THIS SOLVES is not visual, it is structural.

   There is exactly ONE record: the concern, and its persisted trace at /api/concern/{id}/trace.
   Three different people need the same four fields off it —

       · the CHECKS the engine ran, and whether each passed
       · the gate's CONFIDENCE against its THRESHOLD, and what blocked it
       · the escalation TEAM
       · the reference ID

   — and until now three separate renderers drew them: `Pipeline.jsx` for the internal console, a
   flat one-line `steps` list in the captain's widget that dropped all four, and a hand-rolled
   block in SupportCommand that read `c.name || c.label || c.check` and therefore printed the
   literal string "check: ✗" for every real check object.

   Three renderings of one record is exactly what "three apps sharing a sidebar" looks like from
   the outside. One component at three sizes is what makes it a system — and the audience sees the
   same three visual elements on the captain's screen, the L3 desk and the ledger, which is the
   claim doing the arguing.

   SCOPES
     widget  354px, in the captain's docked widget. Reasons and evidence collapse to counts; the
             whole region is capped and scrolls, because the sticky column it lives in has an
             overflow that is unreachable by construction (the thing that would scroll it is
             pinned). A full-fidelity trace here is ~1,540px against an ~854px budget.
     l3      full width, as "why this reached you". Everything open — an L3 reader is an expert
             and the checks are the content.
     log     inside a ledger row expander. Compact, no dial ring, evidence collapsed.

   WHAT IT DELIBERATELY DOES NOT RENDER: the reply. The chat bubble already shows it, and
   `Pipeline` prints it three times on one screen (the bubble, the `explain` node's data.reply, and
   the `reply` node's detail). Once is right. ── */

const SCOPES = {
  widget: { dial: 44, collapseReasons: true, collapseEvidence: true, font: 11 },
  l3: { dial: 56, collapseReasons: false, collapseEvidence: false, font: 12 },
  log: { dial: 0, collapseReasons: true, collapseEvidence: true, font: 11 },
};

/** Pull the four fields out of an event list. Tolerant of a partial or in-flight trace. */
export function readTrace(events = []) {
  const byNode = (n) => [...events].reverse().find((e) => e.node === n);
  const policy = byNode("policy");
  const gate = byNode("gate");
  const escalate = byNode("escalate");
  const reply = byNode("reply");
  const act = byNode("act");
  const cost = byNode("cost");
  const query = byNode("query");
  const pd = policy?.data || {};
  const gd = gate?.data || {};
  const ed = escalate?.data || {};
  return {
    checks: pd.checks_run || [],
    evidence: pd.evidence_trail || [],
    action: pd.action || reply?.data?.decision_action || "",
    confidence: gd.confidence,
    threshold: gd.threshold ?? 0.8,
    passed: gd.passed,
    blocks: gd.blocks || [],
    reasons: gd.reasons || [],
    moneyMoving: gd.money_moving,
    team: ed.team || "",
    // The id can arrive on either event. The backend now puts `reference_id` on the escalate
    // event itself (it used to appear only on `reply`, one event later, so the escalation row
    // rendered without the number the presenter needs to read aloud).
    ref: ed.reference_id || reply?.data?.concern_id || "",
    simulated: act?.data?.simulated,
    costUsd: cost?.data?.cost_usd,
    calls: cost?.data?.calls,
    queryName: query?.data?.query || "",
    // A turn that never reached the engine — no key, unknown captain — has nothing to show.
    engineError: !!reply?.data?.engine_error,
  };
}

function Section({ icon, title, children, font }) {
  return (
    <div style={{ marginTop: 9 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 5,
        fontSize: font - 1.5, fontWeight: 700, color: "var(--text-mute)" }}>
        {icon}{title}
      </div>
      {children}
    </div>
  );
}

function Collapsible({ label, count, children, font }) {
  const [open, setOpen] = useState(false);
  if (!count) return null;
  return (
    <div style={{ marginTop: 4 }}>
      <button onClick={() => setOpen((v) => !v)} className="mono"
        style={{ display: "inline-flex", alignItems: "center", gap: 4, background: "none",
          border: "none", cursor: "pointer", padding: 0, fontSize: font - 2,
          color: "var(--text-faint)" }}>
        <ChevronRight size={10} style={{ transform: open ? "rotate(90deg)" : "none",
          transition: "transform .15s" }} />
        {count} {label}
      </button>
      {open && <div style={{ marginTop: 5 }}>{children}</div>}
    </div>
  );
}

export default function TraceView({ events = [], scope = "widget", maxHeight }) {
  const s = SCOPES[scope] || SCOPES.widget;
  const t = readTrace(events);
  const hasEngine = t.checks.length || t.confidence != null || t.team;
  if (!hasEngine) return null;

  const failing = t.checks.filter((c) => !c.passed);
  const passing = t.checks.filter((c) => c.passed);

  return (
    <div style={{ maxHeight, overflowY: maxHeight ? "auto" : undefined,
      fontSize: s.font, borderTop: "1px solid var(--line-soft)", paddingTop: 8 }}
      className={maxHeight ? "custom-scrollbar" : undefined}>

      {/* ── the checks ─────────────────────────────────────────────────────────
          Failing checks FIRST and always expanded — that is the answer to "why".
          Passing ones collapse to a count in the narrow scopes, because a reader
          who wants to know why something was declined does not need four greens
          above the one red. */}
      {t.checks.length > 0 && (
        <Section icon={<ShieldCheck size={s.font} />} title="What it checked" font={s.font}>
          <CheckRows checks={failing} />
          {s.collapseReasons
            ? <Collapsible label="checks passed" count={passing.length} font={s.font}>
                <CheckRows checks={passing} />
              </Collapsible>
            : <CheckRows checks={passing} />}
          {t.evidence.length > 0 && (s.collapseEvidence
            ? <Collapsible label="evidence rows read" count={t.evidence.length} font={s.font}>
                <Evidence trail={t.evidence} />
              </Collapsible>
            : <Section icon={null} title="Evidence assembled" font={s.font}>
                <Evidence trail={t.evidence} />
              </Section>)}
        </Section>
      )}

      {/* ── the gate ───────────────────────────────────────────────────────────
          The lozenge and the dial together are the argument. "gate BLOCK · conf 0.4"
          as a mono line is a claim; a ring stopping at 40% under a marked 80% is a
          reason, and it reads on a projector. */}
      {t.confidence != null && (
        <Section icon={<Scale size={s.font} />} title="Trust gate" font={s.font}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <span className={`gate-lozenge ${t.passed ? "pass" : "block"}`}>
              {t.passed ? "GATE PASSED" : "BLOCKED"}
            </span>
            {s.dial > 0 && <ConfidenceDial value={t.confidence} threshold={t.threshold} />}
            {s.dial === 0 && (
              <span className="mono" style={{ fontSize: s.font,
                color: t.passed ? "var(--good)" : "var(--warn)" }}>
                {Math.round(t.confidence * 100)}% / gate {Math.round(t.threshold * 100)}%
              </span>
            )}
          </div>
          {/* Every block, verbatim. This is the sentence the engine used to refuse itself. */}
          {t.blocks.map((b, i) => (
            <div key={i} className="check-row fail" style={{ marginTop: 4 }}>
              <span className="ci"><XCircle size={13} /></span>
              <span className="cd">{b}</span>
            </div>
          ))}
          {s.collapseReasons
            ? <Collapsible label="constitution clauses upheld" count={t.reasons.length}
                font={s.font}>
                {t.reasons.map((r, i) => (
                  <div key={i} className="check-row pass">
                    <span className="ci"><CheckCircle2 size={13} /></span>
                    <span className="cd">{r}</span>
                  </div>
                ))}
              </Collapsible>
            : t.reasons.map((r, i) => (
                <div key={i} className="check-row pass">
                  <span className="ci"><CheckCircle2 size={13} /></span>
                  <span className="cd">{r}</span>
                </div>
              ))}
        </Section>
      )}

      {/* ── the escalation, with its reference on the same row ─────────────────
          The id is what the presenter reads aloud and the next two screens carry.
          It renders here because the backend now puts reference_id on this event —
          it used to arrive one event later, so this row appeared without a number. */}
      {t.team && (
        <Section icon={<AlertTriangle size={s.font} />} title="Handed over" font={s.font}>
          <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
            <span className="tag mono" style={{ color: "var(--warn)",
              borderColor: "rgba(255,185,95,.4)" }}>→ {t.team}</span>
            {t.ref && <span className="mono" style={{ fontSize: s.font, fontWeight: 700 }}>
              {t.ref}</span>}
          </div>
          <div className="mono" style={{ fontSize: s.font - 2, color: "var(--text-faint)",
            marginTop: 4 }}>
            worked case assembled · nothing was written
          </div>
        </Section>
      )}

      {/* The footer earns its line: it names the ONE query that ran and what the turn cost. */}
      {(t.queryName || t.costUsd != null) && (
        <div className="mono" style={{ fontSize: s.font - 2, color: "var(--text-faint)",
          marginTop: 9, display: "flex", gap: 10, flexWrap: "wrap" }}>
          {t.queryName && <span>named query: {t.queryName}</span>}
          {t.costUsd != null && <span>${Number(t.costUsd).toFixed(4)} · {t.calls} call(s)</span>}
          {t.simulated && <span style={{ color: "var(--warn)" }}>simulated — nothing written</span>}
        </div>
      )}
    </div>
  );
}
