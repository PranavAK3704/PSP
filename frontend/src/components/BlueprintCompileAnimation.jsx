import React from "react";
import { motion, AnimatePresence } from "framer-motion";

// Domain-Brain compiler visualization — the same assembly/pipeline aesthetic as
// PolicyCompileAnimation (stages light up cyan→green, each real section snaps in),
// but tuned to the Blueprint shape: signals → derivations → lookups → decision →
// ask_if_missing. Reuses the identical Chip/Section/stage-rail language.

const STAGES = [
  ["understand", "Understand", "psychology"],
  ["signals", "Signals", "sensors"],
  ["derivations", "Derivations", "account_tree"],
  ["lookups", "Lookups", "database"],
  ["decision", "Decision", "rule"],
  ["ask_if_missing", "Ask if missing", "help"],
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

const ACTION_TONE = { reverse: "green", inform_educate: "cyan", respond: "cyan", escalate: "amber" };

export default function BlueprintCompileAnimation({ data, current, blueprint, busy }) {
  const d = data || {};
  const has = (k) => d[k] !== undefined;
  const stageState = (key) => {
    if (has(key) || (key === "understand" && (busy || current))) {
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

      {/* Assembling brain */}
      <div style={{ marginTop: 6 }}>
        {blueprint?.domain && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
            style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 14 }}>
            <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--good)" }}>neurology</span>
            <span style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--good)" }}>
              {blueprint.label || blueprint.domain}</span>
            <span style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--text-faint)" }}>{blueprint.domain}</span>
          </motion.div>
        )}

        <Section show={has("signals")} icon="sensors" title="Signals it reads">
          {(d.signals?.signals || []).map((s, i) => <Chip key={i}>{s.key} · {s.source}</Chip>)}
        </Section>

        {has("derivations") && (
          <Section show icon="account_tree" title="Clues → one canonical key">
            <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: 6 }}>
              {(d.derivations?.derivations || []).map((r, i) => (
                <motion.div key={i} initial={{ opacity: 0, x: -12 }} animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.12 }}
                  style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--text)", display: "flex", gap: 7 }}>
                  <span style={{ color: "var(--signal)" }}>{(r.from || []).join(" + ")}</span>
                  <span style={{ color: "var(--text-faint)" }}>→</span>
                  <span style={{ color: "var(--good)" }}>{r.to}</span>
                </motion.div>
              ))}
            </div>
          </Section>
        )}

        <Section show={has("lookups")} icon="database" title="Data lookups">
          {(d.lookups?.lookups || []).map((l, i) => (
            <Chip key={i}>{l.when_have} → {(l.fetch || []).join(", ")}</Chip>
          ))}
        </Section>

        {has("decision") && (
          <Section show icon="rule" title={`Decision branches · ${(d.decision?.decision || []).length}`}>
            <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: 6 }}>
              {(d.decision?.decision || []).map((c, i) => (
                <motion.div key={i} initial={{ opacity: 0, x: -12 }} animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.12 }}
                  style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--text)", display: "flex", gap: 7 }}>
                  <span style={{ color: "var(--signal)" }}>{String(i + 1).padStart(2, "0")}</span>
                  <span style={{ flex: 1 }}>{c.condition}</span>
                  <Chip tone={ACTION_TONE[c.action] || "cyan"}>{c.action}</Chip>
                </motion.div>
              ))}
            </div>
          </Section>
        )}

        {has("ask_if_missing") && (
          <Section show icon="help" title="Ask only the true gap">
            <div style={{ width: "100%", display: "flex", flexDirection: "column", gap: 6 }}>
              {(d.ask_if_missing?.ask_if_missing || []).map((a, i) => (
                <motion.div key={i} initial={{ opacity: 0, x: -12 }} animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: i * 0.12 }}
                  style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--text-mute)" }}>
                  <span style={{ color: "var(--signal)" }}>{a.need}: </span>&ldquo;{a.prompt}&rdquo;
                </motion.div>
              ))}
              {d.ask_if_missing?.escalation_team && <Chip tone="amber">↳ {d.ask_if_missing.escalation_team}</Chip>}
            </div>
          </Section>
        )}
      </div>
    </div>
  );
}
