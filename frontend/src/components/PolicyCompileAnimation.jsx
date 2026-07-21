import React from "react";
import { motion, AnimatePresence } from "framer-motion";

// Signature visualization of the SOP Compiler (BRD §4.3): a plain-language SOP is
// STRUCTURED and TIERED into an Executable Policy, section by section, as the backend
// streams real stages. Deliberately NOT the revolving DecisionCore orb — this is an
// assembly/pipeline reveal: stages light up cyan→green and each real policy section
// snaps into place.

const STAGES = [
  ["understand", "Understand", "psychology"],
  ["trigger", "Triggers", "bolt"],
  ["required_evidence", "Evidence", "fact_check"],
  ["checks", "Checks", "rule"],
  ["resolution", "Resolution", "payments"],
  ["escalation", "Escalation", "diversity_3"],
  ["partner_rights", "Rights", "gavel"],
  ["conformance", "Governance", "verified_user"],
];

const Chip = ({ children, tone = "cyan" }) => {
  const c = tone === "amber" ? "var(--warn)" : tone === "green" ? "var(--good)" : "var(--signal)";
  return (
    <motion.span initial={{ opacity: 0, y: 6, scale: 0.9 }} animate={{ opacity: 1, y: 0, scale: 1 }}
      style={{ fontFamily: "var(--mono)", fontSize: 11, padding: "3px 9px", borderRadius: 20,
        color: c, background: `color-mix(in srgb, ${c} 12%, transparent)`,
        border: `1px solid color-mix(in srgb, ${c} 40%, transparent)` }}>
      {children}
    </motion.span>
  );
};

function Section({ show, icon, title, children }) {
  return (
    <AnimatePresence>
      {show && (
        <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
          transition={{ type: "spring", stiffness: 260, damping: 22 }}
          style={{ borderLeft: "2px solid var(--signal-line)", paddingLeft: 12, marginTop: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 7, marginBottom: 7 }}>
            <span className="material-symbols-outlined" style={{ fontSize: 15, color: "var(--signal)" }}>{icon}</span>
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, textTransform: "uppercase",
              letterSpacing: "0.12em", color: "var(--text-mute)" }}>{title}</span>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 7 }}>{children}</div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export default function PolicyCompileAnimation({ data, current, policy, busy }) {
  const d = data || {};
  const has = (k) => d[k] !== undefined;
  const stageState = (key) => {
    if (has(key) || (key === "understand" && (busy || current))) {
      // done if a later stage has arrived or compile finished
      const idx = STAGES.findIndex((s) => s[0] === key);
      const curIdx = current === "done" ? STAGES.length : STAGES.findIndex((s) => s[0] === current);
      if (current === "done" || idx < curIdx) return "done";
      return "active";
    }
    return "pending";
  };

  return (
    <div>
      {/* Stage rail */}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        {STAGES.map(([key, label, icon], i) => {
          const st = stageState(key);
          const color = st === "done" ? "var(--good)" : st === "active" ? "var(--signal)" : "var(--text-faint)";
          return (
            <React.Fragment key={key}>
              {i > 0 && <span style={{ width: 10, height: 1, background: "var(--line)" }} />}
              <motion.div animate={st === "active" ? { scale: [1, 1.06, 1] } : { scale: 1 }}
                transition={st === "active" ? { repeat: Infinity, duration: 1.1 } : {}}
                style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "4px 9px", borderRadius: 20,
                  fontFamily: "var(--mono)", fontSize: 10, color,
                  border: `1px solid color-mix(in srgb, ${color} 40%, transparent)`,
                  background: st !== "pending" ? `color-mix(in srgb, ${color} 10%, transparent)` : "transparent" }}>
                <span className="material-symbols-outlined" style={{ fontSize: 13 }}>
                  {st === "done" ? "check" : icon}</span>
                {label}
              </motion.div>
            </React.Fragment>
          );
        })}
      </div>

      {/* Assembling policy */}
      <div style={{ marginTop: 6 }}>
        {policy?.id && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
            style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 14 }}>
            <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--good)" }}>verified</span>
            <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--good)" }}>{policy.id}</span>
            {policy.version && <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--text-faint)" }}>{policy.version}</span>}
          </motion.div>
        )}

        <Section show={has("trigger")} icon="bolt" title="Triggers">
          {(d.trigger?.keywords || []).map((k, i) => <Chip key={"k" + i}>{k}</Chip>)}
          {(d.trigger?.preconditions || []).map((p, i) => <Chip key={"p" + i} tone="green">{p}</Chip>)}
        </Section>

        <Section show={has("required_evidence")} icon="fact_check" title="Required evidence">
          {(d.required_evidence?.required_evidence || []).map((e, i) => <Chip key={i}>{e}</Chip>)}
        </Section>

        {has("checks") && (
          <Section show icon="rule" title={`Deterministic checks · ${(d.checks?.checks || []).length}`}>
            <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: 6 }}>
              {(d.checks?.checks || []).map((c, i) => (
                <motion.div key={i} initial={{ opacity: 0, x: -12 }} animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.12 }}
                  style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--text)", display: "flex", gap: 7 }}>
                  <span style={{ color: "var(--signal)" }}>{String(i + 1).padStart(2, "0")}</span>
                  <span>{c.description || c.id}</span>
                </motion.div>
              ))}
            </div>
          </Section>
        )}

        <Section show={has("resolution")} icon="payments" title="Resolution + money cap">
          {d.resolution?.action && <Chip tone="green">{d.resolution.action}</Chip>}
          {d.resolution?.cap_inr != null && <Chip tone="amber">cap ₹{d.resolution.cap_inr}</Chip>}
          {d.resolution?.params?.idempotent && <Chip>idempotent</Chip>}
        </Section>

        <Section show={has("escalation")} icon="diversity_3" title="Escalation route">
          {d.escalation?.team && <Chip tone="amber">{d.escalation.team}</Chip>}
        </Section>

        <Section show={has("partner_rights")} icon="gavel" title="Partner-Constitution rights">
          {(d.partner_rights?.partner_rights || []).map((r, i) => <Chip key={i} tone="green">{r}</Chip>)}
        </Section>

        {/* Governance conformance — scored against the org's approved Framework before approval. */}
        <Section show={has("conformance")} icon="verified_user" title="Governance conformance">
          <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: 7 }}>
            <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
              {d.conformance?.band && <Chip tone={d.conformance?.conformant ? "green" : "amber"}>band · {d.conformance.band}</Chip>}
              <Chip tone={d.conformance?.conformant ? "green" : "amber"}>
                {d.conformance?.conformant ? "conforms to framework" : "resolve before going live"}
              </Chip>
            </div>
            {(d.conformance?.findings || []).map((f, i) => {
              const col = f.severity === "high" ? "var(--bad)" : f.severity === "warn" ? "var(--warn)" : "var(--signal)";
              return (
                <motion.div key={i} initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.1 }}
                  style={{ display: "flex", gap: 7, fontSize: 11, color: "var(--text)", lineHeight: 1.45 }}>
                  <span className="material-symbols-outlined" style={{ fontSize: 13, color: col, flex: "none" }}>
                    {f.severity === "high" ? "error" : f.severity === "warn" ? "warning" : "info"}</span>
                  <span><b style={{ fontFamily: "var(--mono)", color: col }}>{f.rule}</b> — {f.message}</span>
                </motion.div>
              );
            })}
            {(d.conformance?.findings || []).length === 0 && (
              <span style={{ fontSize: 11, color: "var(--good)" }}>All governance mandates met.</span>
            )}
          </div>
        </Section>
      </div>
    </div>
  );
}
