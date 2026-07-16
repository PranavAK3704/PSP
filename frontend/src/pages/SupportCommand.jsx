import React, { useEffect, useState } from "react";
import { getInsights, getAudit, getKt, submitKt, reviewKt, compileSopStream, getLedger,
  approveSop, saveSopDraft, extractSop, compileBlueprintStream, getBlueprints, saveBlueprint, approveBlueprint,
  getConcernTrace, exportLedger, getAuditRubric, saveAuditRubric, runAudit, runAuditBatch, getAuditScores,
  getFramework, saveFramework, uploadFramework, approveFramework } from "../lib/api.js";
import PolicyCompileAnimation from "../components/PolicyCompileAnimation.jsx";
import BlueprintCompileAnimation from "../components/BlueprintCompileAnimation.jsx";
import { useAuth } from "../lib/auth.jsx";

// Nav now lives in the app's left sidebar (App.jsx) — these views are exported as
// standalone top-level views and routed there. Command / AuthoringStudio /
// AuditingStudio / GovernanceFramework / Ledger each fetch their own data on mount.

/* ── Command / metrics ── */
// KPI tile (Stitch "Command Center"): label-caps header + corner icon, a large
// tabular-nums digit, and a small trend/sub label. `danger` red-accents the tile
// (used by Active Breaches when > 0).
function Kpi({ icon, label, value, sub, tone = "text-on-surface", danger = false }) {
  return (
    <div className={`glass-card p-md rounded-xl scan-line relative flex flex-col justify-between min-h-[116px] ${danger ? "border-error/40" : ""}`}
      style={danger ? { background: "rgba(255,180,171,0.06)" } : undefined}>
      <div className="flex items-start justify-between gap-sm">
        <span className={`text-[10px] font-bold uppercase tracking-[0.12em] ${danger ? "text-error" : "text-on-surface-variant"}`}
          style={{ fontFamily: "JetBrains Mono" }}>{label}</span>
        <span className={`material-symbols-outlined ${danger ? "text-error" : "text-secondary-container/70"}`} style={{ fontSize: 18 }}>{icon}</span>
      </div>
      <div className="mt-sm">
        <div className={`text-[34px] font-bold leading-none ${danger ? "text-error" : tone}`} style={{ fontVariantNumeric: "tabular-nums" }}>
          {value == null || value === "" ? "—" : value}</div>
        {sub && <div className="text-[11px] text-on-surface-variant mt-1.5" style={{ fontFamily: "JetBrains Mono" }}>{sub}</div>}
      </div>
    </div>
  );
}
export function Command() {
  const [d, setD] = useState(null);
  useEffect(() => { getInsights().then(setD); }, []);
  if (!d) return <div className="glass-card p-lg rounded-xl text-on-surface-variant">Loading…</div>;
  // Defensive: never assume the insights payload carries every top-level key — a missing
  // ledger/satisfaction/knowledge object must not crash the panel (it would blank it to the
  // error fallback). Default each to {} so the reads below degrade to "—"/0 gracefully.
  const L = d.ledger || {}, S = d.satisfaction || {}, K = d.knowledge || {};
  const total = L.total || 0;
  const automation = total ? Math.round((L.resolved_in_conversation || 0) / total * 1000) / 10 : null;
  const breaches = d.active_breaches || 0;
  // avg_resolution_time is a structured ops metric ({display, sample, via_l3, …}); tolerate
  // an older string/absent payload without crashing.
  const art = (d.avg_resolution_time && typeof d.avg_resolution_time === "object") ? d.avg_resolution_time : {};
  const artDisplay = art.display || (typeof d.avg_resolution_time === "string" ? d.avg_resolution_time : "—");
  const artSub = art.sample ? `${art.sample} resolved · ${art.via_l3 || 0} via L3` : "ops metric";
  const dispEntries = Object.entries(L.by_disposition || {});
  const dispMax = dispEntries.reduce((m, [, v]) => Math.max(m, v || 0), 0) || 1;

  return (
    <div className="space-y-gutter">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-gutter">
        <Kpi icon="receipt_long" label="Concerns Logged" value={total} sub="total logged" tone="text-secondary-container" />
        <Kpi icon="bolt" label="Resolved in Convo" value={L.resolved_in_conversation}
          sub={`of ${total}${automation != null ? ` · ${automation}% automation` : ""}`} tone="text-tertiary" />
        <Kpi icon="savings" label="Recovered for Partners"
          value={`₹${(L.money_recovered_for_partners_inr || 0).toLocaleString("en-IN")}`} sub="tier-1 partners" tone="text-tertiary" />
        <Kpi icon="sentiment_satisfied" label="CSAT" value={S.csat_pct == null ? "—" : `${S.csat_pct}%`}
          sub={`${S.responses || 0} rated · target 85%+`} tone="text-secondary-container" />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-gutter">
        <Kpi icon="north_east" label="Escalated" value={L.escalated} sub="to L3 desk" tone="text-warn" />
        {/* NEW — Active Breaches (red-accented when > 0) */}
        <Kpi icon="gpp_maybe" label="Active Breaches" value={breaches}
          sub={breaches > 0 ? "past SLA now" : "all within SLA"} tone="text-tertiary" danger={breaches > 0} />
        {/* NEW — Avg Resolution Time (best-effort ops metric) */}
        <Kpi icon="timer" label="Avg Resolution Time" value={artDisplay} sub={artSub} tone="text-secondary-container" />
        <Kpi icon="radar" label="Open CPD" value={d.cpd_open} sub="risk signals" tone="text-error" />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-gutter">
        <Kpi icon="category" label="Dispositions" value={d.dispositions} sub="with policy" />
        <Kpi icon="database" label="Knowledge Chunks" value={K.total} sub={`${K.by_kind?.sop || 0} SOP indexed`} />
      </div>

      {/* By-disposition breakdown — dense rows with a proportional bar + tabular-nums count */}
      <div>
        <div className="text-[11px] font-bold uppercase tracking-[0.12em] text-secondary-container border-l-2 border-secondary-container pl-md mb-md"
          style={{ fontFamily: "JetBrains Mono" }}>By disposition</div>
        <div className="glass-card rounded-xl overflow-hidden">
          {dispEntries.length === 0 && <div className="px-lg py-md text-sm text-on-surface-variant">No concerns logged yet.</div>}
          {dispEntries.map(([k, v]) => (
            <div key={k} className="grid grid-cols-[1fr_auto] gap-md items-center px-lg h-[44px] border-b border-on-primary-fixed-variant/10 last:border-0 hover:bg-surface-variant/20 transition-colors">
              <div className="flex items-center gap-md min-w-0">
                <span className="text-sm truncate w-40">{k}</span>
                <div className="flex-1 h-1.5 rounded-full bg-surface-variant/40 overflow-hidden min-w-[60px]">
                  <div className="h-full bg-secondary-container/70" style={{ width: `${Math.round(100 * (v || 0) / dispMax)}%` }} />
                </div>
              </div>
              <span className="text-tertiary text-sm font-bold" style={{ fontFamily: "JetBrains Mono", fontVariantNumeric: "tabular-nums" }}>{v}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
   AUTHORING STUDIO — where a domain owner authors the engine's brain.
   Mode "SOP" (plain SOP → Executable Policy) | "Domain Brain" (walkthrough →
   Blueprint). Left: raw text + Compile. Right: the streamed structuring, then an
   EDITABLE structured view with inline amber gap chips. Actions: Queue changes
   (draft) + Approve & go live (reload → the engine follows it). Below: existing
   Blueprints + SOPs with status pills; click to load into the editor.
   ══════════════════════════════════════════════════════════════════════════ */
const BP_DOMAINS = ["losses", "payments", "fe_id", "cod_cash", "consumables", "orders", "other"];
const SEV_TONE = { high: "text-error bg-error/10 border-error/30", warn: "text-warn bg-warn/10 border-warn/30" };

// A small amber (or red for high-severity) gap chip surfaced next to the problem area.
function GapChip({ gap }) {
  return (
    <span title={gap.message}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold border ${SEV_TONE[gap.severity] || SEV_TONE.warn}`}
      style={{ fontFamily: "JetBrains Mono" }}>
      <span className="material-symbols-outlined" style={{ fontSize: 12 }}>
        {gap.severity === "high" ? "priority_high" : "warning"}</span>
      {gap.message}
    </span>
  );
}
// Collect the gaps that point at a given path prefix (e.g. "signals", "decision[1]").
const gapsFor = (gaps, ...prefixes) =>
  (gaps || []).filter((g) => prefixes.some((p) => (g.where || "").startsWith(p)));

// Inline editable field / row primitives (kept minimal + consistent with the app).
function Field({ value, onChange, placeholder, mono = true, className = "" }) {
  return (
    <input value={value ?? ""} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
      className={`bg-surface-container-highest border border-on-primary-fixed-variant/20 rounded px-2 py-1 text-[12px]
        focus:outline-none focus:border-secondary-container placeholder:text-on-surface-variant/40 ${className}`}
      style={mono ? { fontFamily: "JetBrains Mono" } : undefined} />
  );
}
function RowCard({ children, onRemove }) {
  return (
    <div className="bg-surface-container-lowest border border-on-primary-fixed-variant/15 rounded-lg p-sm flex items-start gap-sm">
      <div className="flex-1 flex flex-wrap items-center gap-xs min-w-0">{children}</div>
      {onRemove && (
        <button onClick={onRemove} className="flex-none w-6 h-6 grid place-items-center rounded text-on-surface-variant hover:text-error">
          <span className="material-symbols-outlined" style={{ fontSize: 15 }}>close</span>
        </button>
      )}
    </div>
  );
}
function AddRow({ label, onClick }) {
  return (
    <button onClick={onClick} className="self-start flex items-center gap-1 text-[11px] text-on-surface-variant hover:text-secondary-container">
      <span className="material-symbols-outlined" style={{ fontSize: 14 }}>add</span>{label}
    </button>
  );
}
function SectionHead({ icon, title, gaps }) {
  return (
    <div className="flex items-center gap-2 flex-wrap mb-xs mt-md first:mt-0">
      <span className="material-symbols-outlined text-secondary-container" style={{ fontSize: 15 }}>{icon}</span>
      <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-on-surface-variant">{title}</span>
      {(gaps || []).map((g, i) => <GapChip key={i} gap={g} />)}
    </div>
  );
}

/* ── Editable Blueprint (Domain Brain) ── */
function BlueprintEditor({ bp, gaps, onChange }) {
  const set = (k, v) => onChange({ ...bp, [k]: v });
  const setArr = (k, i, patch) => set(k, (bp[k] || []).map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const del = (k, i) => set(k, (bp[k] || []).filter((_, j) => j !== i));
  const add = (k, row) => set(k, [...(bp[k] || []), row]);
  const g = (...p) => gapsFor(gaps, ...p);
  return (
    <div className="flex flex-col">
      <div className="flex items-center gap-sm flex-wrap mb-sm">
        <Field value={bp.label} onChange={(v) => set("label", v)} placeholder="Domain label" mono={false}
          className="text-sm font-semibold w-48" />
        {/* editable/confirmable domain — pre-filled from the compiled brain (inferred or explicit) */}
        <span className="text-[10px] text-on-surface-variant flex items-center gap-1">
          <span className="material-symbols-outlined" style={{ fontSize: 13 }}>fingerprint</span>domain</span>
        <select value={bp.domain || ""} onChange={(e) => set("domain", e.target.value)}
          className="bg-secondary-container/15 text-secondary-container border border-secondary-container/30 rounded px-2 py-0.5 text-[11px] focus:outline-none focus:border-secondary-container"
          style={{ fontFamily: "JetBrains Mono" }}>
          {(BP_DOMAINS.includes(bp.domain) ? BP_DOMAINS : [bp.domain, ...BP_DOMAINS]).map((d) => (
            <option key={d} value={d}>{d}</option>))}
        </select>
      </div>

      <SectionHead icon="sensors" title="Signals" gaps={g("signals")} />
      <div className="flex flex-col gap-xs">
        {(bp.signals || []).map((s, i) => (
          <RowCard key={i} onRemove={() => del("signals", i)}>
            <Field value={s.key} onChange={(v) => setArr("signals", i, { key: v })} placeholder="key" className="w-32" />
            <Field value={s.desc} onChange={(v) => setArr("signals", i, { desc: v })} placeholder="description" mono={false} className="flex-1 min-w-[140px]" />
            <select value={s.source || ""} onChange={(e) => setArr("signals", i, { source: e.target.value })}
              className="bg-surface-container-highest border border-on-primary-fixed-variant/20 rounded px-1 py-1 text-[11px]" style={{ fontFamily: "JetBrains Mono" }}>
              {["", "message", "profile", "image", "either"].map((o) => <option key={o} value={o}>{o || "source?"}</option>)}
            </select>
          </RowCard>
        ))}
        <AddRow label="signal" onClick={() => add("signals", { key: "", desc: "", source: "message" })} />
      </div>

      <SectionHead icon="account_tree" title="Derivations · resolve clues → one key" gaps={g("derivations")} />
      <div className="flex flex-col gap-xs">
        {(bp.derivations || []).map((r, i) => (
          <RowCard key={i} onRemove={() => del("derivations", i)}>
            <Field value={(r.from || []).join(", ")} onChange={(v) => setArr("derivations", i, { from: v.split(",").map((x) => x.trim()).filter(Boolean) })} placeholder="from (comma-sep keys)" className="w-40" />
            <span className="text-on-surface-variant">→</span>
            <Field value={r.to} onChange={(v) => setArr("derivations", i, { to: v })} placeholder="to key" className="w-28" />
            <Field value={r.how} onChange={(v) => setArr("derivations", i, { how: v })} placeholder="how it resolves" mono={false} className="flex-1 min-w-[140px]" />
          </RowCard>
        ))}
        <AddRow label="derivation" onClick={() => add("derivations", { from: [], to: "", how: "" })} />
      </div>

      <SectionHead icon="database" title="Lookups" gaps={g("lookups")} />
      <div className="flex flex-col gap-xs">
        {(bp.lookups || []).map((l, i) => (
          <RowCard key={i} onRemove={() => del("lookups", i)}>
            <span className="text-[10px] text-on-surface-variant">when have</span>
            <Field value={l.when_have} onChange={(v) => setArr("lookups", i, { when_have: v })} placeholder="key" className="w-28" />
            <span className="text-[10px] text-on-surface-variant">fetch</span>
            <Field value={(l.fetch || []).join(", ")} onChange={(v) => setArr("lookups", i, { fetch: v.split(",").map((x) => x.trim()).filter(Boolean) })} placeholder="fields (comma-sep)" className="flex-1 min-w-[120px]" />
            <Field value={l.from} onChange={(v) => setArr("lookups", i, { from: v })} placeholder="from" className="w-24" />
          </RowCard>
        ))}
        <AddRow label="lookup" onClick={() => add("lookups", { when_have: "", fetch: [], from: "" })} />
      </div>

      <SectionHead icon="rule" title="Decision branches" gaps={g("decision")} />
      <div className="flex flex-col gap-xs">
        {(bp.decision || []).map((b, i) => (
          <RowCard key={i} onRemove={() => del("decision", i)}>
            <Field value={b.condition} onChange={(v) => setArr("decision", i, { condition: v })} placeholder="condition (yes/no test)" mono={false} className="flex-1 min-w-[160px]" />
            <select value={b.action || ""} onChange={(e) => setArr("decision", i, { action: e.target.value })}
              className="bg-surface-container-highest border border-on-primary-fixed-variant/20 rounded px-1 py-1 text-[11px]" style={{ fontFamily: "JetBrains Mono" }}>
              {["", "reverse", "inform_educate", "escalate", "respond"].map((o) => <option key={o} value={o}>{o || "action?"}</option>)}
            </select>
            <Field value={b.note} onChange={(v) => setArr("decision", i, { note: v })} placeholder="note" mono={false} className="flex-1 min-w-[140px]" />
            {gapsFor(gaps, `decision[${i}]`).map((gg, k) => <GapChip key={k} gap={gg} />)}
          </RowCard>
        ))}
        <AddRow label="branch" onClick={() => add("decision", { condition: "", action: "escalate", note: "" })} />
      </div>

      <SectionHead icon="help" title="Ask if missing · in the captain's language" gaps={g("ask_if_missing")} />
      <div className="flex flex-col gap-xs">
        {(bp.ask_if_missing || []).map((a, i) => (
          <RowCard key={i} onRemove={() => del("ask_if_missing", i)}>
            <Field value={a.need} onChange={(v) => setArr("ask_if_missing", i, { need: v })} placeholder="need (key)" className="w-28" />
            <Field value={a.prompt} onChange={(v) => setArr("ask_if_missing", i, { prompt: v })} placeholder="prompt to the captain" mono={false} className="flex-1 min-w-[200px]" />
          </RowCard>
        ))}
        <AddRow label="prompt" onClick={() => add("ask_if_missing", { need: "", satisfied_by: [], prompt: "" })} />
      </div>

      <SectionHead icon="diversity_3" title="Escalation + proactive" />
      <div className="flex flex-col gap-xs">
        <Field value={bp.escalation_team} onChange={(v) => set("escalation_team", v)} placeholder="escalation team" mono={false} />
        <Field value={bp.proactive} onChange={(v) => set("proactive", v)} placeholder="proactive: how to warn the captain before it posts" mono={false} />
      </div>
    </div>
  );
}

/* ── Editable SOP (Executable Policy) ── */
function SopEditor({ policy, gaps, onChange }) {
  const set = (k, v) => onChange({ ...policy, [k]: v });
  const setNested = (k, sub, v) => set(k, { ...(policy[k] || {}), [sub]: v });
  const trig = policy.trigger || {}, res = policy.resolution || {}, esc = policy.escalation || {};
  const g = (...p) => gapsFor(gaps, ...p);
  return (
    <div className="flex flex-col">
      <div className="flex items-center gap-sm flex-wrap mb-sm">
        <Field value={policy.disposition} onChange={(v) => set("disposition", v)} placeholder="disposition" className="w-48" />
        <span className="text-[10px] px-2 py-0.5 rounded bg-secondary-container/15 text-secondary-container" style={{ fontFamily: "JetBrains Mono" }}>{policy.id || "pol_…"}</span>
      </div>

      <SectionHead icon="bolt" title="Trigger keywords" gaps={g("trigger")} />
      <Field value={(trig.keywords || []).join(", ")} onChange={(v) => setNested("trigger", "keywords", v.split(",").map((x) => x.trim()).filter(Boolean))} placeholder="keywords (comma-sep)" />

      <SectionHead icon="fact_check" title="Required evidence · what to gather / ask" gaps={g("required_evidence")} />
      <Field value={(policy.required_evidence || []).join(", ")} onChange={(v) => set("required_evidence", v.split(",").map((x) => x.trim()).filter(Boolean))} placeholder="evidence rows (comma-sep)" />

      <SectionHead icon="rule" title="Checks" gaps={g("checks")} />
      <div className="flex flex-col gap-xs">
        {(policy.checks || []).map((c, i) => (
          <RowCard key={i} onRemove={() => set("checks", (policy.checks || []).filter((_, j) => j !== i))}>
            <Field value={c.description} onChange={(v) => set("checks", (policy.checks || []).map((x, j) => (j === i ? { ...x, description: v } : x)))} placeholder="the yes/no test" mono={false} className="flex-1 min-w-[180px]" />
            <Field value={(c.reads || []).join(", ")} onChange={(v) => set("checks", (policy.checks || []).map((x, j) => (j === i ? { ...x, reads: v.split(",").map((y) => y.trim()).filter(Boolean) } : x)))} placeholder="reads (evidence)" className="w-40" />
            {gapsFor(gaps, `checks[${i}]`).map((gg, k) => <GapChip key={k} gap={gg} />)}
          </RowCard>
        ))}
        <AddRow label="check" onClick={() => set("checks", [...(policy.checks || []), { id: "", description: "", reads: [], expect: "" }])} />
      </div>

      <SectionHead icon="payments" title="Resolution" gaps={g("resolution")} />
      <div className="flex flex-wrap gap-xs items-center">
        <Field value={res.action} onChange={(v) => setNested("resolution", "action", v)} placeholder="action" className="w-40" />
        <Field value={res.cap_inr ?? ""} onChange={(v) => setNested("resolution", "cap_inr", v === "" ? null : Number(v))} placeholder="cap ₹" className="w-24" />
      </div>

      <SectionHead icon="diversity_3" title="Escalation" gaps={g("escalation")} />
      <div className="flex flex-wrap gap-xs items-center">
        <Field value={esc.team} onChange={(v) => setNested("escalation", "team", v)} placeholder="team" className="w-48" mono={false} />
        <Field value={esc.handover} onChange={(v) => setNested("escalation", "handover", v)} placeholder="handover" mono={false} className="flex-1 min-w-[160px]" />
      </div>
    </div>
  );
}

export function AuthoringStudio() {
  const { isApprover } = useAuth();             // only approvers can make content go live
  const [mode, setMode] = useState("brain");    // "sop" | "brain"
  const [raw, setRaw] = useState("");
  const [domain, setDomain] = useState("");     // "" = auto-detect (the machine infers it)
  const [stageData, setStageData] = useState({});
  const [current, setCurrent] = useState(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);   // edited Blueprint or Policy
  const [gaps, setGaps] = useState([]);
  const [existingBrain, setExistingBrain] = useState(null);  // dedup: a brain already exists for this domain
  const [similarSops, setSimilarSops] = useState([]);        // dedup: compiled SOPs this one resembles
  const [helpOpen, setHelpOpen] = useState(false);           // "How this works" modal
  const [toast, setToast] = useState(null);
  const [srcFile, setSrcFile] = useState(null);              // filename the text was extracted from
  const [uploading, setUploading] = useState(false);
  const fileRef = React.useRef(null);

  const [blueprints, setBlueprints] = useState([]);
  const [kt, setKt] = useState({ all: [] });
  const loadLibrary = () => { getBlueprints().then((d) => setBlueprints(d.blueprints || [])); getKt().then(setKt); };
  useEffect(loadLibrary, []);
  const flash = (msg) => { setToast(msg); setTimeout(() => setToast(null), 4200); };

  function compile() {
    if (!raw.trim() || busy) return;
    setBusy(true); setResult(null); setGaps([]); setStageData({}); setCurrent("understand");
    setExistingBrain(null); setSimilarSops([]);
    if (mode === "brain") {
      compileBlueprintStream(raw, domain,           // domain may be "" → the machine infers it
        (ev) => {
          const st = ev.stage; if (!st) return; setCurrent(st);
          if (st === "done") {
            setResult(ev.blueprint); setGaps(ev.gaps || []);
            // pre-fill the up-front picker with the inferred/confirmed domain (author can override)
            if (ev.blueprint?.domain) setDomain(ev.blueprint.domain);
            setExistingBrain(ev.existing_brain || null);
            setBusy(false);
          } else if (st !== "understand") setStageData((d) => ({ ...d, [st]: ev.data }));
        }, () => setBusy(false));
    } else {
      compileSopStream(raw,
        (ev) => {
          const st = ev.stage; if (!st) return; setCurrent(st);
          if (st === "done") {
            setResult(ev.policy); setGaps(ev.gaps || []);
            setSimilarSops(ev.similar_sops || []);
            setBusy(false);
          } else if (st !== "understand") setStageData((d) => ({ ...d, [st]: ev.data }));
        }, () => setBusy(false));
    }
  }

  // Clear the whole editor back to a blank slate (used after go-live so the studio is ready
  // for the next SOP — the just-approved one now lives in the Authored library below).
  function resetEditor() {
    setRaw(""); setResult(null); setGaps([]); setStageData({}); setCurrent(null);
    setSimilarSops([]); setExistingBrain(null); setSrcFile(null);
  }

  // Upload a real ops artifact → extract its content → prefill the editor. The author then
  // reviews/edits the text and Compiles it through the normal pipeline.
  async function onUpload(e) {
    const file = e.target.files?.[0];
    if (fileRef.current) fileRef.current.value = "";   // allow re-uploading the same file
    if (!file) return;
    setUploading(true);
    try {
      const r = await extractSop(file);
      setRaw(r.text || "");
      setSrcFile(r.source_name || file.name);
      setResult(null); setGaps([]); setStageData({}); setCurrent(null);
      flash(`Loaded "${r.source_name || file.name}" — review the text, then Compile.`);
    } catch (err) {
      flash(`Couldn't read that file: ${err?.message || "unsupported or empty"}`);
    } finally {
      setUploading(false);
    }
  }

  async function queueChanges() {
    if (!result) return;
    if (mode === "brain") {
      const r = await saveBlueprint(result, "domain-owner");
      setGaps(r.gaps || []); flash("Queued as draft. Approve to make the engine follow it.");
    } else {
      const r = await saveSopDraft(result);
      if (r?.ok) flash("Saved as a draft — it's in the Authored library below. Approve & go live when ready.");
    }
    loadLibrary();
  }
  async function approve() {
    if (!result) return;
    if (mode === "brain") {
      await saveBlueprint(result, "domain-owner");
      const r = await approveBlueprint(result.domain);
      if (r.ok) flash(`Live. The resolution engine now follows the ${result.label || result.domain} brain — it's in the library below.`);
    } else {
      const r = await approveSop(result);
      if (r.ok) flash("Live. This SOP is in the retrieval corpus and the library below — the engine follows it now.");
    }
    resetEditor();      // editor goes blank; the approved item now shows in the Authored library
    loadLibrary();
  }

  function loadBlueprint(bp) {
    setMode("brain"); setDomain(bp.domain); setResult(bp); setGaps(bp.gaps || []);
    setStageData({}); setCurrent("done"); setExistingBrain(null); setSimilarSops([]);
  }
  function loadSop(k) {
    setMode("sop"); setResult(k.policy || {}); setGaps([]); setStageData({}); setCurrent("done");
    setExistingBrain(null); setSimilarSops([]);
  }
  // dedup banner → open the existing brain (full object from the loaded library) into the editor
  function openExistingBrain(dom) {
    const full = blueprints.find((b) => b.domain === dom);
    if (full) loadBlueprint(full);
  }
  function openSimilarSop(id) {
    const full = sops.find((k) => k.id === id);
    if (full) loadSop(full);
  }

  const highGaps = gaps.filter((g) => g.severity === "high").length;
  const sops = (kt.all || []).filter((k) => k.compiled_sop);

  return (
    <div className="flex flex-col gap-gutter">
      {toast && (
        <div className="glass-card rounded-lg px-md py-sm flex items-center gap-2 text-sm border border-tertiary/40 text-tertiary">
          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>check_circle</span>{toast}
        </div>
      )}

      {/* Mode toggle + intro */}
      <div className="flex items-center justify-between flex-wrap gap-sm">
        <div>
          <div className="text-[15px] font-bold text-on-surface flex items-center gap-2">
            <span className="material-symbols-outlined text-secondary-container" style={{ fontSize: 20 }}>neurology</span>
            Authoring Studio</div>
          <p className="text-xs text-on-surface-variant mt-0.5 max-w-[560px]">
            Author the engine's brain in plain language. Understand once (LLM) → the engine follows it every time.
            Gaps that mean "the machine doesn't know what to ask" surface right here, as you author.</p>
        </div>
        <div className="flex items-center gap-sm">
          <button onClick={() => setHelpOpen(true)} title="How this works"
            className="flex items-center gap-1.5 px-md py-sm rounded-lg text-[13px] font-semibold text-on-surface-variant hover:text-secondary-container border border-on-primary-fixed-variant/20 hover:border-secondary-container/40 transition-all">
            <span className="material-symbols-outlined" style={{ fontSize: 17 }}>help</span>
            <span className="hidden sm:inline">How this works</span></button>
          <div className="flex gap-1 glass-card rounded-lg p-1">
            {[["brain", "Domain Brain", "neurology"], ["sop", "SOP", "description"]].map(([k, label, icon]) => (
              <button key={k} onClick={() => { setMode(k); setResult(null); setGaps([]); setCurrent(null); setStageData({}); setExistingBrain(null); setSimilarSops([]); }}
                className={`flex items-center gap-1.5 px-md py-sm rounded-md text-sm font-semibold transition-all ${
                  mode === k ? "bg-secondary-container text-on-secondary" : "text-on-surface-variant hover:text-secondary-container"}`}>
                <span className="material-symbols-outlined" style={{ fontSize: 17 }}>{icon}</span>{label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {helpOpen && <HowThisWorks onClose={() => setHelpOpen(false)} />}

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-gutter items-start">
        {/* LEFT: raw text + domain + compile */}
        <div className="glass-card rounded-xl p-lg flex flex-col">
          <div className="flex items-center justify-between gap-sm mb-1">
            <div className="text-[11px] font-bold uppercase tracking-[0.1em] text-secondary-container">
              {mode === "brain" ? "Domain walkthrough" : "Plain-language SOP"}</div>
            <input ref={fileRef} type="file" className="hidden"
              accept=".xlsx,.xlsm,.docx,.pdf,.csv,.txt,.md,text/csv,text/plain,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              onChange={onUpload} />
            <button onClick={() => fileRef.current?.click()} disabled={uploading}
              title="Upload an Excel / Word / PDF / CSV file — the content is extracted into the box below"
              className="flex items-center gap-1.5 px-md py-1.5 rounded-lg text-[12px] font-semibold text-on-surface-variant hover:text-secondary-container border border-on-primary-fixed-variant/20 hover:border-secondary-container/40 disabled:opacity-50 transition-all">
              <span className="material-symbols-outlined" style={{ fontSize: 16 }}>{uploading ? "hourglass_top" : "upload_file"}</span>
              {uploading ? "Reading…" : "Upload file"}</button>
          </div>
          <p className="text-xs text-on-surface-variant mb-md">
            {mode === "brain"
              ? "Describe how you work this domain — what clues you read, how they resolve to one identifier, what you look up, how you decide, and what you'd ask the captain. Or upload a walkthrough doc."
              : "Paste a plain-language SOP — when it applies, evidence required, the checks, the resolution + cap, and the escalation owner. Or upload an Excel / Word / PDF and it'll be extracted here."}</p>
          {srcFile && (
            <div className="flex items-center gap-1.5 mb-sm text-[11px] text-tertiary bg-tertiary/10 border border-tertiary/30 rounded px-2 py-1 self-start">
              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>description</span>
              Extracted from <b>{srcFile}</b> — review below, then Compile.
              <button onClick={() => setSrcFile(null)} className="ml-1 opacity-70 hover:opacity-100">
                <span className="material-symbols-outlined" style={{ fontSize: 13 }}>close</span></button>
            </div>
          )}
          {mode === "brain" && (
            <div className="flex items-center gap-sm mb-sm flex-wrap">
              <span className="text-[11px] text-on-surface-variant">Domain</span>
              <select value={domain} onChange={(e) => setDomain(e.target.value)}
                className="bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded px-sm py-1 text-[12px]" style={{ fontFamily: "JetBrains Mono" }}>
                <option value="">auto-detect</option>
                {BP_DOMAINS.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
              <span className="text-[10px] text-on-surface-variant/70">
                {domain ? "Locked — the machine will use this." : "Optional — the machine infers it from your walkthrough; you confirm after compiling."}</span>
            </div>
          )}
          <textarea value={raw} onChange={(e) => setRaw(e.target.value)}
            placeholder={mode === "brain"
              ? "e.g. When a captain says a debit is wrong, I look at the amount and payment cycle, or the hub code + debit note number, to find the AWB. Then I check the loss record — if there's a facility inscan or the attribution changed, I reverse it; if it's genuinely their fault I explain; if I can't find the AWB I escalate to Losses & Debits."
              : "e.g. When a shortage-loss debit is disputed, gather the AWB and loss record. If a facility inscan exists, reverse the debit up to ₹5000; otherwise escalate to Losses & Debits."}
            className="w-full min-h-[260px] bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded-lg p-md text-[13px] leading-relaxed focus:outline-none focus:border-secondary-container resize-y placeholder:text-on-surface-variant/40" />
          <button onClick={compile} disabled={busy}
            className="mt-md self-start bg-secondary-container text-on-secondary px-lg py-sm rounded-lg font-bold text-sm flex items-center gap-2 hover:brightness-110 disabled:opacity-50 transition-all">
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>bolt</span>
            {busy ? "Compiling…" : mode === "brain" ? "Compile to Domain Brain" : "Compile to Executable Policy"}</button>
        </div>

        {/* RIGHT: streamed structuring → editable structured view + gaps + actions */}
        <div className="glass-card rounded-xl p-lg flex flex-col min-h-[320px]">
          {!current && !result && (
            <div className="flex-1 grid place-items-center text-center text-on-surface-variant text-sm">
              <div>
                <span className="material-symbols-outlined" style={{ fontSize: 34, color: "var(--text-faint)" }}>graphic_eq</span>
                <div className="mt-2">The structured {mode === "brain" ? "brain" : "policy"} will assemble here as it streams.</div>
              </div>
            </div>
          )}

          {(busy || (current && current !== "done")) && (
            <div>
              {mode === "brain"
                ? <BlueprintCompileAnimation data={stageData} current={current} blueprint={result} busy={busy} />
                : <PolicyCompileAnimation data={stageData} current={current} policy={result} busy={busy} />}
            </div>
          )}

          {result && !busy && (
            <div className="flex flex-col">
              {/* DEDUP guardrails (non-blocking) — a brain already exists for this domain… */}
              {existingBrain && (
                <div className="flex items-start gap-2 mb-md text-xs bg-warn/10 border border-warn/40 rounded-lg px-md py-sm text-warn">
                  <span className="material-symbols-outlined" style={{ fontSize: 16 }}>merge_type</span>
                  <div className="flex-1 leading-relaxed">
                    A Brain for <b>{existingBrain.domain}</b> already exists ({existingBrain.status}). You're editing it — or open the existing one.
                  </div>
                  <button onClick={() => openExistingBrain(existingBrain.domain)}
                    className="flex-none text-[11px] font-bold px-2 py-1 rounded border border-warn/50 hover:bg-warn/20 transition-all">Open existing</button>
                </div>
              )}
              {/* …or this SOP resembles ones already compiled */}
              {similarSops.length > 0 && (
                <div className="flex items-start gap-2 mb-md text-xs bg-warn/10 border border-warn/40 rounded-lg px-md py-sm text-warn">
                  <span className="material-symbols-outlined" style={{ fontSize: 16 }}>content_copy</span>
                  <div className="flex-1 leading-relaxed">
                    This looks similar to existing SOP{similarSops.length > 1 ? "s" : ""}:{" "}
                    {similarSops.map((s, i) => (
                      <React.Fragment key={s.id}>
                        {i > 0 && ", "}
                        <button onClick={() => openSimilarSop(s.id)}
                          className="underline decoration-dotted hover:text-warn/80 font-semibold">
                          {s.title} ({Math.round(s.score * 100)}%)</button>
                      </React.Fragment>
                    ))}{" "}— open it instead?
                  </div>
                </div>
              )}
              {/* gap summary banner */}
              {gaps.length > 0 ? (
                <div className="flex items-center gap-2 mb-md text-xs text-warn">
                  <span className="material-symbols-outlined" style={{ fontSize: 16 }}>error</span>
                  {gaps.length} gap{gaps.length > 1 ? "s" : ""} to resolve
                  {highGaps > 0 && <span className="text-error font-bold">· {highGaps} blocking</span>}
                  <span className="text-on-surface-variant">— the engine won't know what to ask / how to decide until these are filled.</span>
                </div>
              ) : (
                <div className="flex items-center gap-2 mb-md text-xs text-tertiary">
                  <span className="material-symbols-outlined" style={{ fontSize: 16 }}>verified</span>
                  No gaps — the machine knows what to gather, how to decide, and has a safe fallback.
                </div>
              )}

              <div className="max-h-[440px] overflow-y-auto custom-scrollbar pr-1">
                {mode === "brain"
                  ? <BlueprintEditor bp={result} gaps={gaps} onChange={setResult} />
                  : <SopEditor policy={result} gaps={gaps} onChange={setResult} />}
              </div>

              <div className="flex items-center gap-sm flex-wrap mt-lg pt-md border-t border-on-primary-fixed-variant/15">
                <button onClick={queueChanges}
                  className="border border-secondary-container text-secondary-container px-lg py-sm rounded-lg font-bold text-sm flex items-center gap-2 hover:bg-secondary-container/10 transition-all">
                  <span className="material-symbols-outlined" style={{ fontSize: 18 }}>save</span>
                  {isApprover ? "Queue changes" : "Queue for approval"}</button>
                {isApprover ? (
                  <>
                    <button onClick={approve} disabled={highGaps > 0}
                      title={highGaps > 0 ? "Resolve the blocking gaps first" : ""}
                      className="bg-tertiary text-on-tertiary px-lg py-sm rounded-lg font-bold text-sm flex items-center gap-2 hover:brightness-110 disabled:opacity-40 transition-all">
                      <span className="material-symbols-outlined" style={{ fontSize: 18 }}>rocket_launch</span>Approve &amp; go live</button>
                    <span className="text-[11px] text-on-surface-variant">Approving reloads the corpus — the resolution engine follows it immediately.</span>
                  </>
                ) : (
                  <span className="flex items-center gap-1.5 text-[11px] text-on-surface-variant">
                    <span className="material-symbols-outlined text-warn" style={{ fontSize: 15 }}>lock</span>
                    Needs an approver to go live — your draft is queued for review.</span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* LIBRARY: existing Blueprints + SOPs with status pills; click to load */}
      <div className="glass-card rounded-xl p-lg">
        <div className="text-[11px] font-bold uppercase tracking-[0.1em] text-secondary-container mb-md">Authored library · click to load into the editor</div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-gutter">
          {/* Blueprints */}
          <div>
            <div className="text-[10px] uppercase tracking-wide text-on-surface-variant mb-sm flex items-center gap-1">
              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>neurology</span>Domain Brains · {blueprints.length}</div>
            <div className="space-y-sm">
              {blueprints.length === 0 && <div className="text-on-surface-variant text-sm">No blueprints yet — author one above.</div>}
              {blueprints.map((b) => (
                <div key={b.domain} onClick={() => loadBlueprint(b)}
                  className="bg-surface-container-lowest border border-on-primary-fixed-variant/15 rounded-lg p-md cursor-pointer hover:border-secondary-container/40 transition-all">
                  <div className="flex justify-between items-start gap-md">
                    <div className="min-w-0">
                      <div className="text-sm font-semibold flex items-center gap-2 flex-wrap">
                        {b.label || b.domain}
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-secondary-container/10 text-secondary-container" style={{ fontFamily: "JetBrains Mono" }}>{b.domain}</span>
                      </div>
                      <div className="text-[10px] text-on-surface-variant mt-1" style={{ fontFamily: "JetBrains Mono" }}>
                        {(b.signals || []).length} signals · {(b.decision || []).length} branches · {b.escalation_team || "no team"}</div>
                    </div>
                    <div className="flex flex-col items-end gap-1 flex-none">
                      <StatusPill status={b.status} />
                      {(b.gaps || []).length > 0 && <span className="text-[9px] text-warn font-bold">{b.gaps.length} gap{b.gaps.length > 1 ? "s" : ""}</span>}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
          {/* SOPs */}
          <div>
            <div className="text-[10px] uppercase tracking-wide text-on-surface-variant mb-sm flex items-center gap-1">
              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>description</span>Compiled SOPs · {sops.length}</div>
            <div className="space-y-sm">
              {sops.length === 0 && <div className="text-on-surface-variant text-sm">No compiled SOPs yet — compile one in SOP mode and approve it.</div>}
              {sops.map((k) => (
                <div key={k.id} onClick={() => loadSop(k)}
                  className="bg-surface-container-lowest border border-on-primary-fixed-variant/15 rounded-lg p-md cursor-pointer hover:border-secondary-container/40 transition-all">
                  <div className="flex justify-between items-start gap-md">
                    <div className="min-w-0">
                      <div className="text-sm font-semibold">{k.structured?.title || k.policy?.disposition || k.id}</div>
                      <div className="text-[10px] text-on-surface-variant mt-1" style={{ fontFamily: "JetBrains Mono" }}>{k.id} · {(k.policy?.checks || []).length} checks</div>
                    </div>
                    <StatusPill status={k.status} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

    </div>
  );
}

function StatusPill({ status }) {
  const ok = status === "approved";
  return (
    <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${ok ? "text-tertiary bg-tertiary/10" : "text-warn bg-warn/10"}`}>
      {ok ? "approved · live" : "draft"}</span>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
   "HOW THIS WORKS" — in-product help for the Authoring Studio. Condenses
   docs/AUTHORING_GUIDE.md: Brain vs SOP, how the bot uses both at runtime, the
   author flow, and the two rules of the road. Matches the Team-admin modal style.
   ══════════════════════════════════════════════════════════════════════════ */
function HelpLabel({ icon, children }) {
  return (
    <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-secondary-container flex items-center gap-1.5 mb-sm">
      <span className="material-symbols-outlined" style={{ fontSize: 15 }}>{icon}</span>{children}</div>
  );
}
function HowThisWorks({ onClose }) {
  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-black/60 backdrop-blur-sm p-md" onClick={onClose}>
      <div className="glass-card rounded-2xl w-[92%] max-w-[620px] max-h-[86vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-lg py-md border-b border-on-primary-fixed-variant/20">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-secondary-container" style={{ fontSize: 20 }}>neurology</span>
            <span className="text-sm font-bold">How the Authoring Studio works</span>
          </div>
          <button onClick={onClose} className="w-8 h-8 grid place-items-center rounded-lg text-on-surface-variant hover:text-error hover:bg-error/10">
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>close</span>
          </button>
        </div>

        <div className="overflow-y-auto custom-scrollbar p-lg flex flex-col gap-lg text-sm leading-relaxed">
          {/* Brain vs SOP */}
          <div>
            <HelpLabel icon="compare_arrows">Brain vs SOP — two things, two jobs</HelpLabel>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-sm">
              <div className="bg-surface-container-lowest border border-on-primary-fixed-variant/15 rounded-lg p-md">
                <div className="font-semibold flex items-center gap-1.5 mb-1">
                  <span className="material-symbols-outlined text-secondary-container" style={{ fontSize: 16 }}>neurology</span>Domain Brain</div>
                <p className="text-xs text-on-surface-variant">How to think about a <b>whole domain</b> (losses, payments, FE-ID…): which
                  signals to read → how they derive to one identifier → how to decide (reverse / inform / escalate) → what to
                  ask the captain when a key is missing.</p>
              </div>
              <div className="bg-surface-container-lowest border border-on-primary-fixed-variant/15 rounded-lg p-md">
                <div className="font-semibold flex items-center gap-1.5 mb-1">
                  <span className="material-symbols-outlined text-secondary-container" style={{ fontSize: 16 }}>description</span>SOP</div>
                <p className="text-xs text-on-surface-variant">One specific <b>rule / policy</b>: "in situation X, the rule is Y, up to ₹Z,
                  else escalate to team T." SOPs aren't domain-keyed.</p>
              </div>
            </div>
            <p className="text-xs text-on-surface-variant mt-sm">
              <b className="text-on-surface">Brain = the reasoning. SOP = the rulebook it reasons with.</b> You need both.</p>
          </div>

          {/* Runtime */}
          <div>
            <HelpLabel icon="smart_toy">How the bot uses both at runtime</HelpLabel>
            <ol className="text-xs text-on-surface-variant space-y-1.5 list-decimal pl-5">
              <li>Identifies the <b>domain</b> of the captain's message.</li>
              <li>Applies that domain's approved <b>Brain</b> — gathers the signals, asks <i>only</i> the one detail it genuinely can't work out, decides partner-first.</li>
              <li>Pulls the relevant <b>SOPs</b> for that case's specifics.</li>
              <li>Replies — grounded, partner-first, in the captain's language.</li>
            </ol>
          </div>

          {/* The flow */}
          <div>
            <HelpLabel icon="conveyor_belt">The flow</HelpLabel>
            <div className="flex items-center gap-xs flex-wrap text-[11px]" style={{ fontFamily: "JetBrains Mono" }}>
              {["write plain language", "Compile", "machine structures it", "clear the red (blocking) gaps", "Queue", "a senior (approver) approves → go live"].map((s, i, a) => (
                <React.Fragment key={i}>
                  <span className="px-2 py-1 rounded bg-secondary-container/10 text-secondary-container border border-secondary-container/25">{s}</span>
                  {i < a.length - 1 && <span className="material-symbols-outlined text-on-surface-variant" style={{ fontSize: 14 }}>chevron_right</span>}
                </React.Fragment>
              ))}
            </div>
            <p className="text-xs text-on-surface-variant mt-sm">
              Gap chips surface as you author: <span className="text-warn font-semibold">amber</span> = should fix,
              <span className="text-error font-semibold"> red</span> = blocking. "Approve &amp; go live" stays disabled until the red ones are cleared.</p>
          </div>

          {/* Two rules */}
          <div>
            <HelpLabel icon="gavel">Two rules of the road</HelpLabel>
            <div className="space-y-sm">
              <div className="flex items-start gap-2 text-xs text-on-surface-variant">
                <span className="material-symbols-outlined text-tertiary flex-none" style={{ fontSize: 16 }}>record_voice_over</span>
                <div><b className="text-on-surface">Always say what to ask the captain.</b> The most common gap is knowing the rule but never
                  stating the detail to request. That's what turns "please raise a ticket" into a real resolution.</div>
              </div>
              <div className="flex items-start gap-2 text-xs text-on-surface-variant">
                <span className="material-symbols-outlined text-tertiary flex-none" style={{ fontSize: 16 }}>library_books</span>
                <div><b className="text-on-surface">Check the library / heed the duplicate warning</b> before creating a second one. One Brain per
                  domain — if the studio warns that a Brain/SOP already exists, open and edit it instead.</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Learning Queue — approve pending nuances / auto-gaps captured from live escalations.
   (Gap DETECTION now lives inline in the Authoring Studio; this is pure review/approval.) ── */
function LearningQueue({ onChanged }) {
  const { isApprover } = useAuth();             // only approvers can approve/reject KT
  const [ktText, setKtText] = useState("");
  const [kt, setKt] = useState({ all: [] });
  const loadKt = () => getKt().then(setKt);
  useEffect(() => { loadKt(); }, []);
  async function submit() { if (!ktText.trim()) return; await submitKt(ktText, "support-ops"); setKtText(""); loadKt(); onChanged && onChanged(); }
  async function review(id, ok) { await reviewKt(id, ok, "you"); loadKt(); onChanged && onChanged(); }

  return (
    <div className="flex flex-col gap-gutter">
      {/* KT engine + approval — the plain-language contribution → approve loop */}
      <div className="glass-card rounded-xl p-lg flex flex-col">
        <div className="text-[11px] font-bold uppercase tracking-[0.1em] text-tertiary mb-1">KT Engine · contribute + approve</div>
        <p className="text-xs text-on-surface-variant mb-md">Speak/type a learning → the engine structures it into a policy/procedure → a human approves before it enters the knowledge base.</p>
        <textarea value={ktText} onChange={(e) => setKtText(e.target.value)}
          placeholder="e.g. 'If RVP consumable pickup done but payment not received in 15 days, captain can dispute — need UTR + pickup date.'"
          className="w-full min-h-[80px] bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded-lg p-md text-[13px] focus:outline-none focus:border-tertiary resize-y" />
        <button onClick={submit}
          className="mt-sm self-start border border-tertiary text-tertiary px-lg py-sm rounded-lg font-bold text-sm flex items-center gap-2 hover:bg-tertiary/10 transition-all">
          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>send</span>Structure & queue for approval</button>

        <div className="text-[10px] uppercase tracking-wide text-on-surface-variant mt-lg mb-sm">Approval queue</div>
        <div className="max-h-[280px] overflow-y-auto custom-scrollbar space-y-sm">
          {(kt.all || []).length === 0 && <div className="text-on-surface-variant text-sm">No KT yet — contribute one above.</div>}
          {(kt.all || []).map((k) => (
            <div key={k.id} className="bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded-lg p-md">
              <div className="flex justify-between items-start gap-md">
                <div className="min-w-0">
                  <div className="text-sm font-semibold flex items-center gap-2 flex-wrap">
                    {k.structured?.title || k.raw_text?.slice(0, 40)}
                    <span className={`px-2 py-0.5 rounded text-[9px] font-bold ${k.type === "policy" ? "bg-secondary-container/15 text-secondary-container" : "bg-surface-variant text-on-surface-variant"}`}>{(k.type || "procedure").toUpperCase()}</span>
                    {k.auto_gap && <span className="px-2 py-0.5 rounded text-[9px] font-bold bg-warn/10 text-warn">AUTO-GAP · needs SOP{k.hit_count > 1 ? ` · ${k.hit_count}× hit` : ""}</span>}
                  </div>
                  <div className="text-[10px] text-on-surface-variant mt-1" style={{ fontFamily: "JetBrains Mono" }}>{k.id} · {k.contributor} · {k.structured?.queue}</div>
                  {k.structured?.knowledge && <div className="text-xs text-on-surface-variant mt-1 line-clamp-2">{k.structured.knowledge}</div>}
                </div>
                {k.status === "pending" ? (
                  isApprover ? (
                    <div className="flex gap-1 flex-none">
                      <button onClick={() => review(k.id, true)} className="w-8 h-8 grid place-items-center rounded-lg border border-tertiary/40 text-tertiary hover:bg-tertiary/10"><span className="material-symbols-outlined" style={{ fontSize: 16 }}>check</span></button>
                      <button onClick={() => review(k.id, false)} className="w-8 h-8 grid place-items-center rounded-lg border border-error/40 text-error hover:bg-error/10"><span className="material-symbols-outlined" style={{ fontSize: 16 }}>close</span></button>
                    </div>
                  ) : (
                    <span className="flex-none self-start flex items-center gap-1 text-[10px] font-bold px-2 py-1 rounded bg-warn/10 text-warn">
                      <span className="material-symbols-outlined" style={{ fontSize: 13 }}>lock</span>needs an approver</span>
                  )
                ) : <span className={`text-[10px] font-bold px-2 py-1 rounded self-start ${k.status === "approved" ? "text-tertiary bg-tertiary/10" : "text-error bg-error/10"}`}>{k.status}</span>}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ── Audit + CPD ── */
function Audit() {
  const [d, setD] = useState({ trail: [], cpd: [], satisfaction: {} });
  useEffect(() => { getAudit().then(setD); }, []);
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-gutter">
      <div className="lg:col-span-2 glass-card rounded-xl overflow-hidden">
        <div className="px-lg py-md border-b border-on-primary-fixed-variant/20 text-[11px] font-bold uppercase tracking-[0.1em] text-secondary-container">Immutable audit trail</div>
        <div className="max-h-[560px] overflow-y-auto custom-scrollbar">
          {(d.trail || []).map((a) => (
            <div key={a.concern_id} className="px-lg py-md border-b border-on-primary-fixed-variant/10 last:border-0">
              <div className="flex justify-between items-start">
                <span className="text-[11px] text-on-surface-variant" style={{ fontFamily: "JetBrains Mono" }}>{a.concern_id}</span>
                <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${a.outcome === "escalated" ? "bg-warn/10 text-warn" : "bg-tertiary/10 text-tertiary"}`}>{a.action_taken}</span>
              </div>
              <div className="text-sm mt-1">{a.intent}</div>
              <div className="text-[10px] text-on-surface-variant mt-1" style={{ fontFamily: "JetBrains Mono" }}>
                {a.captain_id} · {a.channel} · {a.disposition} · data: {(a.data_used || []).join(", ") || "—"} · {a.turns || 1} turn/s</div>
            </div>
          ))}
        </div>
      </div>
      <div className="glass-card rounded-xl overflow-hidden">
        <div className="px-lg py-md border-b border-on-primary-fixed-variant/20 text-[11px] font-bold uppercase tracking-[0.1em] text-error">Continuous Problem Discovery</div>
        <div className="max-h-[560px] overflow-y-auto custom-scrollbar">
          {(d.cpd || []).length === 0 && <div className="px-lg py-md text-on-surface-variant text-sm">No CPD signals yet.</div>}
          {(d.cpd || []).map((c) => (
            <div key={c.id} className="px-lg py-md border-b border-on-primary-fixed-variant/10 last:border-0">
              <div className="flex justify-between"><span className="text-[11px] text-on-surface-variant" style={{ fontFamily: "JetBrains Mono" }}>{c.id}</span>
                <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-warn/10 text-warn">{c.status}</span></div>
              <div className="text-sm mt-1">{c.note || "(no note)"}</div>
              <div className="text-[10px] text-on-surface-variant mt-1" style={{ fontFamily: "JetBrains Mono" }}>{c.captain_id} · {c.concern_id}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ── Concern Log ── */
// Outcome pills (Stitch language): RESOLVED = green border + 10% fill; escalated = amber; nudge = blue.
const BADGE = {
  resolved_in_conversation: { label: "resolved", cls: "bg-tertiary/10 text-tertiary border border-tertiary/40" },
  l3_resolved:              { label: "resolved by L3", cls: "bg-tertiary/10 text-tertiary border border-tertiary/40" },
  proactive_nudge:          { label: "nudge sent", cls: "bg-secondary-container/10 text-secondary-container border border-secondary-container/40" },
  escalated:                { label: "escalated", cls: "bg-warn/10 text-warn border border-warn/40" },
};
export function Ledger() {
  const [d, setD] = useState({ concerns: [], stats: {} });
  const [open, setOpen] = useState(null);    // expanded concern id
  useEffect(() => { getLedger().then(setD); }, []);
  return (
    <div className="glass-card rounded-xl overflow-hidden">
      <div className="px-lg h-[46px] border-b border-on-primary-fixed-variant/20 flex items-center justify-between gap-md flex-wrap">
        <span className="text-[11px] font-bold uppercase tracking-[0.12em] text-secondary-container flex items-center gap-sm" style={{ fontFamily: "JetBrains Mono" }}>
          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>receipt_long</span>
          Append-only Concern Log · <span style={{ fontVariantNumeric: "tabular-nums" }}>{d.concerns?.length || 0}</span> events</span>
        <div className="flex items-center gap-sm">
          <span className="text-[10px] text-on-surface-variant uppercase tracking-wide" style={{ fontFamily: "JetBrains Mono" }}>Export</span>
          <button onClick={() => exportLedger("csv")}
            className="flex items-center gap-1.5 text-[11px] font-bold px-md py-1.5 rounded-lg border border-secondary-container/40 text-secondary-container hover:bg-secondary-container/10 transition-all">
            <span className="material-symbols-outlined" style={{ fontSize: 15 }}>table_view</span>CSV</button>
          <button onClick={() => exportLedger("json")}
            className="flex items-center gap-1.5 text-[11px] font-bold px-md py-1.5 rounded-lg border border-secondary-container/40 text-secondary-container hover:bg-secondary-container/10 transition-all">
            <span className="material-symbols-outlined" style={{ fontSize: 15 }}>data_object</span>JSON</button>
        </div>
      </div>
      <div className="max-h-[600px] overflow-y-auto custom-scrollbar">
        {(d.concerns || []).map((c) => (
          <div key={c.id + c.seq} className="border-b border-on-primary-fixed-variant/10 last:border-0">
            <button onClick={() => setOpen(open === c.id ? null : c.id)}
              className={`w-full text-left grid grid-cols-[auto_auto_1fr_auto] gap-md items-center px-lg min-h-[52px] py-sm border-l-2 transition-all ${open === c.id ? "border-l-secondary-container bg-secondary-container/[0.06]" : "border-l-transparent hover:bg-surface-variant/20"}`}>
              <span className="material-symbols-outlined text-on-surface-variant transition-transform" style={{ fontSize: 18, transform: open === c.id ? "rotate(90deg)" : "none" }}>chevron_right</span>
              <span className="text-[11px] text-on-surface-variant" style={{ fontFamily: "JetBrains Mono", fontVariantNumeric: "tabular-nums" }}>{c.id}</span>
              <div className="min-w-0">
                <div className="text-sm truncate">{c.intent}</div>
                <div className="text-[10px] text-on-surface-variant mt-0.5" style={{ fontFamily: "JetBrains Mono", fontVariantNumeric: "tabular-nums" }}>
                  {c.captain_id} · {c.disposition}{c.policy_version ? ` · ${c.policy_version}` : ""}{c.amount_inr ? ` · ₹${c.amount_inr}` : ""}{c.confidence != null ? ` · ${Math.round(c.confidence * 100)}%` : ""}</div>
              </div>
              <span className={`text-[10px] font-bold px-2.5 py-1 rounded-full whitespace-nowrap ${BADGE[c.outcome]?.cls || "bg-warn/10 text-warn border border-warn/40"}`}>
                {BADGE[c.outcome]?.label || c.action_taken || "escalated"}</span>
            </button>
            {open === c.id && <TraceTimeline concernId={c.id} concern={c} />}
          </div>
        ))}
      </div>
    </div>
  );
}

/* ── Resolution trace timeline (expanded row) ── */
const TRACE_DOT = {
  done:    "bg-tertiary",
  blocked: "bg-error",
  running: "bg-warn animate-pulse",
};
function TraceTimeline({ concernId, concern }) {
  const [trace, setTrace] = useState(null);   // {events}
  const [raw, setRaw] = useState(false);
  useEffect(() => { getConcernTrace(concernId).then(setTrace); }, [concernId]);

  if (!trace) return <div className="px-lg pb-md pl-[52px] text-[11px] text-on-surface-variant">Loading trace…</div>;
  const events = trace.events || [];

  return (
    <div className="bg-surface-container-lowest/60 border-t border-on-primary-fixed-variant/10 px-lg py-md">
      <div className="flex items-center justify-between mb-sm pl-[38px]">
        <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-secondary-container flex items-center gap-1.5" style={{ fontFamily: "JetBrains Mono" }}>
          <span className="material-symbols-outlined" style={{ fontSize: 13 }}>timeline</span>
          Resolution trace · <span style={{ fontVariantNumeric: "tabular-nums" }}>{events.length}</span> stage{events.length === 1 ? "" : "s"}</span>
        <button onClick={() => setRaw(!raw)}
          className="text-[10px] text-on-surface-variant hover:text-secondary-container flex items-center gap-1">
          <span className="material-symbols-outlined" style={{ fontSize: 13 }}>{raw ? "view_timeline" : "code"}</span>
          {raw ? "timeline" : "raw"}</button>
      </div>

      {events.length === 0 && !raw && (
        <div className="pl-[38px] text-[11px] text-on-surface-variant">
          No trace stored for this concern (it may pre-date trace persistence).
          {concern?.reply && <div className="mt-1 text-on-surface/80 italic max-w-[720px]">"{concern.reply}"</div>}
        </div>
      )}

      {raw ? (
        <pre className="pl-[38px] text-[10px] text-on-surface-variant overflow-x-auto custom-scrollbar max-h-[360px]" style={{ fontFamily: "JetBrains Mono" }}>
          {JSON.stringify(events, null, 1)}</pre>
      ) : (
        <div className="relative pl-[38px]">
          {events.map((e, i) => (
            <div key={i} className="relative flex gap-sm pb-md last:pb-0">
              {/* connector line */}
              {i < events.length - 1 && <div className="absolute left-[5px] top-[14px] bottom-0 w-px bg-on-primary-fixed-variant/20" />}
              <div className={`mt-[5px] w-[11px] h-[11px] rounded-full flex-none ${TRACE_DOT[e.status] || "bg-on-surface-variant/50"}`} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[12px] font-semibold text-on-surface">{e.label || e.node}</span>
                  {e.node && <span className="text-[9px] px-1.5 py-0.5 rounded bg-secondary-container/10 text-secondary-container" style={{ fontFamily: "JetBrains Mono" }}>{e.node}</span>}
                  {e.tier && <span className="text-[9px] text-on-surface-variant" style={{ fontFamily: "JetBrains Mono" }}>{e.tier}</span>}
                  {e.status && e.status !== "done" && <span className={`text-[9px] font-bold ${e.status === "blocked" ? "text-error" : "text-warn"}`}>{e.status}</span>}
                </div>
                {e.detail && <div className="text-[11px] text-on-surface-variant mt-0.5">{e.detail}</div>}
                <TraceData data={e.data} />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* Compact, readable view of an event's key data — not a JSON dump. */
function TraceData({ data }) {
  if (!data || typeof data !== "object") return null;
  const chips = [];
  const push = (label, val) => chips.push({ label, val });

  if (data.entities && Object.keys(data.entities).length)
    push("entities", Object.entries(data.entities).map(([k, v]) => `${k}=${v}`).join(", "));
  if (Array.isArray(data.checks_run) && data.checks_run.length)
    push("checks", data.checks_run.map((c) => `${c.name || c.label || c.check || "check"}: ${c.passed === false || c.pass === false ? "✗" : "✓"}`).join("  "));
  if (Array.isArray(data.evidence_trail) && data.evidence_trail.length)
    push("evidence", data.evidence_trail.map((e) => `${e.label || e.source}${e.value ? `=${e.value}` : ""}`).join(" · "));
  if (data.decision_action) push("action", data.decision_action);
  if (Array.isArray(data.sop_refs) && data.sop_refs.length) push("SOPs", data.sop_refs.join(", "));
  if (Array.isArray(data.sources) && data.sources.length)
    push("sources", data.sources.map((s) => (typeof s === "string" ? s : s.title || s.id || "src")).join(", "));

  return (
    <div className="mt-1 flex flex-col gap-1">
      {chips.map((c, i) => (
        <div key={i} className="text-[10px] text-on-surface-variant" style={{ fontFamily: "JetBrains Mono" }}>
          <span className="text-secondary-container/80">{c.label}:</span> {c.val}</div>
      ))}
      {data.reply && (
        <div className="text-[11px] text-on-surface/85 italic mt-0.5 max-w-[720px] bg-surface-variant/20 rounded px-2 py-1">
          "{data.reply}"</div>
      )}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
   AUDITING STUDIO — one surface, internal tabs:
     Scores & Rubric (the LLM-judge rubric + dashboard) · Audit Trail & CPD ·
     Learning Queue (KT approval + corpus-wide SOP required-data gaps).
   ══════════════════════════════════════════════════════════════════════════ */
export function AuditingStudio() {
  const [sub, setSub] = useState("scores");
  // Governance is now a standalone top-level view (App.jsx sidebar) — Auditing keeps
  // scores / trail / learning only.
  const SUBS = [
    ["scores", "Scores & Rubric", "insights"],
    ["trail", "Audit Trail & CPD", "policy"],
    ["learning", "Learning Queue", "school"],
  ];
  return (
    <div className="flex flex-col gap-gutter">
      <div className="flex gap-xs flex-wrap border-b border-on-primary-fixed-variant/15 pb-sm">
        {SUBS.map(([k, label, icon]) => (
          <button key={k} onClick={() => setSub(k)}
            className={`flex items-center gap-1.5 px-md py-1.5 rounded-lg text-[13px] font-semibold transition-all border ${
              sub === k ? "bg-secondary-container/20 text-secondary-container border-secondary-container/50"
                        : "text-on-surface-variant hover:text-secondary-container border-transparent"}`}>
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>{icon}</span>{label}
          </button>
        ))}
      </div>
      {sub === "scores" && <AuditScores />}
      {sub === "trail" && <Audit />}
      {sub === "learning" && <LearningQueue />}
    </div>
  );
}

function AuditScores() {
  const [rubric, setRubric] = useState(null);      // {version, dimensions}
  const [dims, setDims] = useState([]);            // editable working copy
  const [scores, setScores] = useState(null);
  const [busy, setBusy] = useState(false);
  const [runId, setRunId] = useState("");
  const [openAudit, setOpenAudit] = useState(null);
  const [toast, setToast] = useState(null);
  const flash = (m) => { setToast(m); setTimeout(() => setToast(null), 4200); };

  const loadRubric = () => getAuditRubric().then((r) => { setRubric(r); setDims((r.dimensions || []).map((d) => ({ ...d }))); });
  const loadScores = () => getAuditScores().then(setScores);
  useEffect(() => { loadRubric(); loadScores(); }, []);

  const totalWeight = dims.reduce((s, d) => s + (Number(d.weight) || 0), 0) || 1;
  const setDim = (i, patch) => setDims(dims.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  const addDim = () => setDims([...dims, { key: "", label: "", description: "", weight: 0.1 }]);
  const removeDim = (i) => setDims(dims.filter((_, j) => j !== i));

  async function saveRubric() {
    const r = await saveAuditRubric(dims);
    setRubric(r); setDims((r.dimensions || []).map((d) => ({ ...d })));
    flash(`Rubric saved — now version ${r.version}. New audits score against it.`);
  }
  async function auditBatch() {
    setBusy(true);
    try { const r = await runAuditBatch(10); flash(`Audited ${r.audited} concern${r.audited === 1 ? "" : "s"}${r.avg_composite != null ? ` · avg ${r.avg_composite}` : ""}.`); await loadScores(); }
    finally { setBusy(false); }
  }
  async function auditOne() {
    if (!runId.trim() || busy) return;
    setBusy(true);
    try {
      const r = await runAudit(runId.trim());
      if (r.error) flash(r.error); else { flash(`Audited ${r.concern_id} · composite ${r.composite}.`); setRunId(""); }
      await loadScores();
    } finally { setBusy(false); }
  }

  const perDimAvg = scores?.per_dimension_avg || {};
  const dimLabel = (k) => (rubric?.dimensions || []).find((d) => d.key === k)?.label || k;

  return (
    <div className="flex flex-col gap-gutter">
      {toast && (
        <div className="glass-card rounded-lg px-md py-sm flex items-center gap-2 text-sm border border-tertiary/40 text-tertiary">
          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>check_circle</span>{toast}
        </div>
      )}

      <div className="flex items-center justify-between flex-wrap gap-sm">
        <div>
          <div className="text-[15px] font-bold text-on-surface flex items-center gap-2">
            <span className="material-symbols-outlined text-secondary-container" style={{ fontSize: 20 }}>fact_check</span>
            Auditing Studio</div>
          <p className="text-xs text-on-surface-variant mt-0.5 max-w-[620px]">
            Author how a resolution should be judged, then let the LLM judge score sampled concerns
            against that rubric — partner-first advocacy, grounding, in-policy decisions, honesty.</p>
        </div>
        {rubric && <span className="text-[11px] font-bold px-2.5 py-1 rounded bg-secondary-container/10 text-secondary-container" style={{ fontFamily: "JetBrains Mono" }}>rubric v{rubric.version}</span>}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-gutter items-start">
        {/* ── RUBRIC EDITOR ── */}
        <div className="glass-card rounded-xl p-lg flex flex-col">
          <div className="flex items-center justify-between mb-sm">
            <div className="text-[11px] font-bold uppercase tracking-[0.1em] text-secondary-container">Rubric · weighted dimensions</div>
            <span className="text-[10px] text-on-surface-variant" style={{ fontFamily: "JetBrains Mono" }}>Σ weight {totalWeight.toFixed(2)} → normalized</span>
          </div>
          <p className="text-xs text-on-surface-variant mb-md">Weights need not sum to 1 — they're normalized when scoring. Save bumps the rubric version.</p>
          <div className="space-y-sm max-h-[520px] overflow-y-auto custom-scrollbar pr-1">
            {dims.map((d, i) => (
              <div key={i} className="bg-surface-container-lowest border border-on-primary-fixed-variant/15 rounded-lg p-md">
                <div className="flex items-center gap-sm mb-1.5">
                  <input value={d.label} onChange={(e) => setDim(i, { label: e.target.value })} placeholder="Dimension label"
                    className="flex-1 bg-surface-container-highest border border-on-primary-fixed-variant/20 rounded px-sm py-1 text-[13px] font-semibold focus:outline-none focus:border-secondary-container" />
                  {d.key && <span className="text-[9px] px-1.5 py-0.5 rounded bg-secondary-container/10 text-secondary-container" style={{ fontFamily: "JetBrains Mono" }}>{d.key}</span>}
                  <button onClick={() => removeDim(i)} className="w-7 h-7 grid place-items-center rounded-lg border border-error/40 text-error hover:bg-error/10 flex-none">
                    <span className="material-symbols-outlined" style={{ fontSize: 15 }}>close</span></button>
                </div>
                <textarea value={d.description} onChange={(e) => setDim(i, { description: e.target.value })} placeholder="What this dimension rewards"
                  className="w-full bg-surface-container-highest border border-on-primary-fixed-variant/20 rounded px-sm py-1 text-[12px] focus:outline-none focus:border-secondary-container resize-y min-h-[44px] placeholder:text-on-surface-variant/40" />
                <div className="flex items-center gap-sm mt-1.5">
                  <span className="text-[10px] text-on-surface-variant">weight</span>
                  <input type="number" step="0.05" min="0" value={d.weight} onChange={(e) => setDim(i, { weight: e.target.value })}
                    className="w-20 bg-surface-container-highest border border-on-primary-fixed-variant/20 rounded px-sm py-1 text-[12px] focus:outline-none focus:border-secondary-container" style={{ fontFamily: "JetBrains Mono" }} />
                  <div className="flex-1 h-1.5 rounded-full bg-surface-variant/40 overflow-hidden">
                    <div className="h-full bg-secondary-container" style={{ width: `${Math.round(100 * (Number(d.weight) || 0) / totalWeight)}%` }} />
                  </div>
                  <span className="text-[10px] text-on-surface-variant w-9 text-right" style={{ fontFamily: "JetBrains Mono" }}>{Math.round(100 * (Number(d.weight) || 0) / totalWeight)}%</span>
                </div>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-sm mt-md pt-md border-t border-on-primary-fixed-variant/15">
            <button onClick={addDim} className="flex items-center gap-1.5 text-[12px] font-bold text-secondary-container hover:brightness-110">
              <span className="material-symbols-outlined" style={{ fontSize: 17 }}>add</span>Add dimension</button>
            <button onClick={saveRubric}
              className="ml-auto bg-secondary-container text-on-secondary px-lg py-sm rounded-lg font-bold text-sm flex items-center gap-2 hover:brightness-110 transition-all">
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>save</span>Save rubric</button>
          </div>
        </div>

        {/* ── SCORE DASHBOARD ── */}
        <div className="glass-card rounded-xl p-lg flex flex-col">
          <div className="text-[11px] font-bold uppercase tracking-[0.12em] text-secondary-container mb-md" style={{ fontFamily: "JetBrains Mono" }}>Score dashboard</div>

          {/* composite — big KPI tile, tone by score band */}
          {(() => {
            const comp = scores?.avg_composite;
            const compColor = comp == null ? "text-on-surface-variant" : comp >= 80 ? "text-tertiary" : comp >= 55 ? "text-secondary-container" : "text-error";
            return (
              <div className="rounded-xl border border-on-primary-fixed-variant/15 bg-surface-container-lowest/60 scan-line p-md mb-lg">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-bold uppercase tracking-[0.12em] text-on-surface-variant" style={{ fontFamily: "JetBrains Mono" }}>Avg composite</span>
                  <span className="material-symbols-outlined text-secondary-container/70" style={{ fontSize: 18 }}>speed</span>
                </div>
                <div className="flex items-baseline gap-2 mt-sm">
                  <span className={`text-[52px] font-bold leading-none ${compColor}`} style={{ fontVariantNumeric: "tabular-nums" }}>{comp ?? "—"}</span>
                  <span className="text-lg text-on-surface-variant">/100</span>
                </div>
                <div className="text-[11px] text-on-surface-variant mt-1.5" style={{ fontFamily: "JetBrains Mono", fontVariantNumeric: "tabular-nums" }}>
                  {scores?.count || 0} audit{scores?.count === 1 ? "" : "s"} scored{rubric ? ` · rubric v${rubric.version}` : ""}</div>
              </div>
            );
          })()}

          {/* per-dimension bars */}
          <div className="text-[10px] font-bold uppercase tracking-[0.1em] text-on-surface-variant mb-sm" style={{ fontFamily: "JetBrains Mono" }}>Per-dimension average</div>
          <div className="space-y-sm mb-lg">
            {Object.keys(perDimAvg).length === 0 && <div className="text-[11px] text-on-surface-variant">No audits yet — run a batch below.</div>}
            {Object.entries(perDimAvg).map(([k, v]) => {
              const prom = k === "partner_supportedness";
              return (
                <div key={k}>
                  <div className="flex justify-between text-[11px] mb-0.5">
                    <span className={prom ? "text-tertiary font-bold flex items-center gap-1" : "text-on-surface-variant"}>
                      {prom && <span className="material-symbols-outlined" style={{ fontSize: 13 }}>favorite</span>}{dimLabel(k)}</span>
                    <span style={{ fontFamily: "JetBrains Mono", fontVariantNumeric: "tabular-nums" }}>{Math.round(v * 100)}</span>
                  </div>
                  <div className="h-2 rounded-full bg-surface-variant/40 overflow-hidden">
                    <div className={`h-full ${prom ? "bg-tertiary" : "bg-secondary-container"}`} style={{ width: `${Math.round(v * 100)}%` }} />
                  </div>
                </div>
              );
            })}
          </div>

          {/* trend */}
          <div className="text-[10px] uppercase tracking-wide text-on-surface-variant mb-sm">Composite over time</div>
          <TrendSparkline series={scores?.time_series || []} />

          {/* by disposition */}
          {scores?.by_disposition && Object.keys(scores.by_disposition).length > 0 && (
            <div className="mt-md">
              <div className="text-[10px] uppercase tracking-wide text-on-surface-variant mb-sm">By disposition</div>
              <div className="flex flex-wrap gap-xs">
                {Object.entries(scores.by_disposition).map(([disp, s]) => (
                  <span key={disp} className="text-[10px] px-2.5 py-0.5 rounded-full bg-surface-variant text-on-surface-variant border border-on-primary-fixed-variant/20" style={{ fontFamily: "JetBrains Mono", fontVariantNumeric: "tabular-nums" }}>
                    {disp}: {s.avg_composite} ({s.count})</span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── RUN CONTROLS + audited concern list ── */}
      <div className="glass-card rounded-xl p-lg">
        <div className="flex items-center justify-between flex-wrap gap-sm mb-md">
          <div className="text-[11px] font-bold uppercase tracking-[0.1em] text-secondary-container">Run the judge · sampled concerns</div>
          <div className="flex items-center gap-sm flex-wrap">
            <input value={runId} onChange={(e) => setRunId(e.target.value)} placeholder="CNC-… (audit one on demand)"
              className="bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded-lg px-sm py-1.5 text-[12px] focus:outline-none focus:border-secondary-container placeholder:text-on-surface-variant/40" style={{ fontFamily: "JetBrains Mono" }} />
            <button onClick={auditOne} disabled={busy || !runId.trim()}
              className="border border-secondary-container text-secondary-container px-md py-1.5 rounded-lg font-bold text-[12px] flex items-center gap-1.5 hover:bg-secondary-container/10 disabled:opacity-40 transition-all">
              <span className="material-symbols-outlined" style={{ fontSize: 16 }}>play_arrow</span>Audit</button>
            <button onClick={auditBatch} disabled={busy}
              className="bg-tertiary text-on-tertiary px-lg py-1.5 rounded-lg font-bold text-[12px] flex items-center gap-2 hover:brightness-110 disabled:opacity-40 transition-all">
              <span className="material-symbols-outlined" style={{ fontSize: 16 }}>{busy ? "hourglass_top" : "fact_check"}</span>
              {busy ? "Judging…" : "Audit recent 10"}</button>
          </div>
        </div>

        <div className="space-y-sm max-h-[520px] overflow-y-auto custom-scrollbar">
          {(scores?.history || []).length === 0 && <div className="text-on-surface-variant text-sm">No audits yet — run a batch to score recent resolutions.</div>}
          {(scores?.history || []).map((a, i) => (
            <div key={a.concern_id + i} className="bg-surface-container-lowest border border-on-primary-fixed-variant/15 rounded-lg overflow-hidden">
              <button onClick={() => setOpenAudit(openAudit === a.concern_id + i ? null : a.concern_id + i)}
                className="w-full text-left grid grid-cols-[auto_1fr_auto_auto] gap-md items-center px-md py-sm hover:bg-surface-variant/20 transition-all">
                <span className="material-symbols-outlined text-on-surface-variant transition-transform" style={{ fontSize: 16, transform: openAudit === a.concern_id + i ? "rotate(90deg)" : "none" }}>chevron_right</span>
                <div className="min-w-0">
                  <div className="text-[12px] font-semibold" style={{ fontFamily: "JetBrains Mono" }}>{a.concern_id}</div>
                  <div className="text-[10px] text-on-surface-variant" style={{ fontFamily: "JetBrains Mono" }}>{a.disposition || "—"} · {a.action_taken || "—"} · rubric v{a.rubric_version}</div>
                </div>
                <span className={`text-[10px] font-bold px-2.5 py-0.5 rounded-full ${compTone(a.composite)}`} style={{ fontVariantNumeric: "tabular-nums" }}>{a.composite}</span>
                <span className="text-[9px] text-on-surface-variant" style={{ fontFamily: "JetBrains Mono", fontVariantNumeric: "tabular-nums" }}>{(a.audited_at || "").slice(0, 10)}</span>
              </button>
              {openAudit === a.concern_id + i && (
                <div className="px-md pb-md border-t border-on-primary-fixed-variant/10 pt-sm">
                  {a.overall_rationale && <div className="text-[11px] text-on-surface/85 italic mb-sm bg-surface-variant/20 rounded px-2 py-1">{a.overall_rationale}</div>}
                  <div className="space-y-1.5">
                    {Object.entries(a.per_dimension || {}).map(([k, v]) => (
                      <div key={k} className="grid grid-cols-[150px_auto_1fr] gap-sm items-center">
                        <span className={`text-[11px] ${k === "partner_supportedness" ? "text-tertiary font-bold" : "text-on-surface-variant"}`}>{dimLabel(k)}</span>
                        <span className="text-[11px] font-bold w-8" style={{ fontFamily: "JetBrains Mono" }}>{Math.round((v.score || 0) * 100)}</span>
                        <span className="text-[10px] text-on-surface-variant truncate" title={v.rationale}>{v.rationale}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
   GOVERNANCE FRAMEWORK — a dynamic, fully editable governance model.
   Upload a framework doc (the machine structures it into a draft) OR edit every
   part by hand: objective, signal sources, weighted dimensions (+ sub-factors +
   an optional ladder), combine rule + formula, bands, band-movement, metrics,
   accountability, and the prioritised items catalogue. Save draft (author+) /
   Approve & publish (approver-only). Reuses the Field/RowCard/AddRow/SectionHead
   primitives + StatusPill + glass-card aesthetic.
   ══════════════════════════════════════════════════════════════════════════ */
const numOrStr = (v) => (v === "" || v == null ? "" : (isNaN(Number(v)) ? v : Number(v)));

export function GovernanceFramework() {
  const { isApprover } = useAuth();          // only approvers can publish (make it live)
  const [fw, setFw] = useState(null);        // the working (editable) framework
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [source, setSource] = useState("");  // uploaded doc name (after structuring)
  const [toast, setToast] = useState(null);
  const flash = (m) => { setToast(m); setTimeout(() => setToast(null), 4200); };

  const load = () => getFramework().then(setFw);
  useEffect(() => { load(); }, []);

  async function onUpload(e) {
    const file = e.target.files?.[0];
    e.target.value = "";                     // allow re-selecting the same file
    if (!file || uploading) return;
    setUploading(true); setSource("");
    try {
      const r = await uploadFramework(file);
      if (r?.framework) {
        setFw(r.framework); setSource(r.source_name || file.name);
        flash("Structured into a draft — review every part, then Save / Approve.");
      } else {
        flash(r?.detail || "Could not structure that document.");
      }
    } catch (err) {
      flash("Upload failed: " + (err?.message || "error"));
    } finally { setUploading(false); }
  }

  async function onSave() {
    if (!fw || busy) return;
    setBusy(true);
    try { const r = await saveFramework(fw); if (r?.framework) { setFw(r.framework); flash("Saved as draft."); } }
    finally { setBusy(false); }
  }
  async function onApprove() {
    if (!fw || busy) return;
    setBusy(true);
    try {
      await saveFramework(fw);               // persist current edits, then publish
      const r = await approveFramework();
      if (r?.framework) { setFw(r.framework); flash(`Published — framework is now v${r.framework.version} (approved).`); }
    } finally { setBusy(false); }
  }

  if (!fw) return <div className="glass-card p-lg rounded-xl text-on-surface-variant">Loading framework…</div>;

  return (
    <div className="flex flex-col gap-gutter">
      {toast && (
        <div className="glass-card rounded-lg px-md py-sm flex items-center gap-2 text-sm border border-tertiary/40 text-tertiary">
          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>check_circle</span>{toast}
        </div>
      )}

      {/* header: title + version/status */}
      <div className="flex items-center justify-between flex-wrap gap-sm">
        <div>
          <div className="text-[15px] font-bold text-on-surface flex items-center gap-2">
            <span className="material-symbols-outlined text-secondary-container" style={{ fontSize: 20 }}>account_tree</span>
            Governance Framework</div>
          <p className="text-xs text-on-surface-variant mt-0.5 max-w-[640px]">
            A dynamic, fully editable governance model — signal sources, scoring dimensions, bands,
            metrics, accountability and the prioritised problems catalogue. Everything here is data;
            edit it (or upload a doc) with zero code change.</p>
        </div>
        <div className="flex items-center gap-sm">
          <StatusPill status={fw.status} />
          <span className="text-[11px] font-bold px-2.5 py-1 rounded bg-secondary-container/10 text-secondary-container" style={{ fontFamily: "JetBrains Mono" }}>v{fw.version}</span>
        </div>
      </div>

      {/* placeholder note */}
      {fw.note && (
        <div className="flex items-start gap-2 text-xs bg-warn/10 border border-warn/40 rounded-lg px-md py-sm text-warn">
          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>lightbulb</span>
          <span className="flex-1 leading-relaxed">{fw.note}</span>
        </div>
      )}

      {/* AT-A-GLANCE OVERVIEW — highlighted formula, band pills, dimension cards (live from the model below) */}
      <GovernanceOverview fw={fw} />

      {/* UPLOAD control */}
      <div className="glass-card rounded-xl p-lg flex flex-col gap-sm">
        <div className="text-[11px] font-bold uppercase tracking-[0.1em] text-secondary-container">Upload a framework doc</div>
        <p className="text-xs text-on-surface-variant">
          Drop in a governance document (.pdf, .csv, .md, .txt). The machine structures it into the model
          below — <b>review before approving</b>.</p>
        <div className="flex items-center gap-md flex-wrap">
          <label className={`self-start flex items-center gap-2 px-lg py-sm rounded-lg font-bold text-sm cursor-pointer transition-all ${
            uploading ? "opacity-50 pointer-events-none" : ""} bg-secondary-container text-on-secondary hover:brightness-110`}>
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>{uploading ? "hourglass_top" : "upload_file"}</span>
            {uploading ? "Structuring…" : "Upload & structure"}
            <input type="file" accept=".pdf,.csv,.md,.txt" onChange={onUpload} className="hidden" />
          </label>
          {uploading && (
            <span className="flex items-center gap-1.5 text-[11px] text-on-surface-variant">
              <span className="material-symbols-outlined animate-spin" style={{ fontSize: 15 }}>progress_activity</span>
              The machine structures it; review before approving.</span>
          )}
          {source && !uploading && (
            <span className="text-[11px] text-on-surface-variant flex items-center gap-1" style={{ fontFamily: "JetBrains Mono" }}>
              <span className="material-symbols-outlined text-tertiary" style={{ fontSize: 14 }}>description</span>{source}</span>
          )}
        </div>
      </div>

      {/* EDITABLE view */}
      <div className="glass-card rounded-xl p-lg">
        <FrameworkEditor fw={fw} onChange={setFw} />
      </div>

      {/* ACTIONS */}
      <div className="glass-card rounded-xl p-lg flex items-center gap-sm flex-wrap">
        <button onClick={onSave} disabled={busy}
          className="border border-secondary-container text-secondary-container px-lg py-sm rounded-lg font-bold text-sm flex items-center gap-2 hover:bg-secondary-container/10 disabled:opacity-40 transition-all">
          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>save</span>Save draft</button>
        {isApprover ? (
          <>
            <button onClick={onApprove} disabled={busy}
              className="bg-tertiary text-on-tertiary px-lg py-sm rounded-lg font-bold text-sm flex items-center gap-2 hover:brightness-110 disabled:opacity-40 transition-all">
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>rocket_launch</span>Approve &amp; publish</button>
            <span className="text-[11px] text-on-surface-variant">Publishing bumps the version and marks it approved.</span>
          </>
        ) : (
          <span className="flex items-center gap-1.5 text-[11px] text-on-surface-variant">
            <span className="material-symbols-outlined text-warn" style={{ fontSize: 15 }}>lock</span>
            Needs an approver to publish — your draft is saved for review.</span>
        )}
      </div>
    </div>
  );
}

/* Band pill tone — P0/critical red → high amber → mid blue → low/watch neutral. */
function bandTone(label) {
  const l = (label || "").toUpperCase();
  if (/\bP0\b|CRIT|SEV0|SEV 0|BLOCK/.test(l)) return "bg-error/15 text-error border border-error/50";
  if (/\bP1\b|HIGH|SEV1/.test(l)) return "bg-warn/15 text-warn border border-warn/50";
  if (/\bP2\b|MED/.test(l)) return "bg-secondary-container/15 text-secondary-container border border-secondary-container/50";
  return "bg-surface-variant text-on-surface-variant border border-on-primary-fixed-variant/30";  // low / watch / monitor / default
}

/* Read-oriented Stitch overview of the framework: a highlighted formula card, priority
   bands as pills, and each scoring dimension as a card. Purely presentational — reflects
   the editable model live. */
function GovernanceOverview({ fw }) {
  const mono = { fontFamily: "JetBrains Mono" };
  const bands = fw.bands || [];
  const dims = fw.dimensions || [];
  return (
    <div className="flex flex-col gap-gutter">
      {/* formula — highlighted card */}
      <div className="glass-card rounded-xl p-lg scan-line">
        <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-secondary-container mb-sm" style={mono}>Scoring formula</div>
        <div className="rounded-lg border border-secondary-container/40 bg-secondary-container/[0.08] px-lg py-md flex items-center gap-md flex-wrap">
          <span className="material-symbols-outlined text-secondary-container" style={{ fontSize: 22 }}>functions</span>
          <span className="text-[17px] font-bold text-on-surface" style={{ ...mono, fontVariantNumeric: "tabular-nums" }}>{fw.formula || "—"}</span>
          {fw.combine && <span className="ml-auto text-[10px] px-2.5 py-0.5 rounded-full bg-surface-variant text-on-surface-variant border border-on-primary-fixed-variant/25" style={mono}>combine · {fw.combine}</span>}
        </div>
      </div>

      {/* bands — pills */}
      {bands.length > 0 && (
        <div className="glass-card rounded-xl p-lg">
          <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-secondary-container mb-md" style={mono}>Priority bands</div>
          <div className="flex flex-wrap gap-sm">
            {bands.map((b, i) => (
              <div key={i} className="flex items-center gap-sm bg-surface-container-lowest/50 border border-on-primary-fixed-variant/15 rounded-lg pl-sm pr-md py-sm">
                <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-bold whitespace-nowrap ${bandTone(b.label)}`}>{b.label || "—"}</span>
                <div className="min-w-0">
                  {b.meaning && <div className="text-[12px] text-on-surface leading-tight">{b.meaning}</div>}
                  {b.action && <div className="text-[10px] text-on-surface-variant leading-tight mt-0.5" style={mono}>{b.action}</div>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* dimensions — cards */}
      {dims.length > 0 && (
        <div className="glass-card rounded-xl p-lg">
          <div className="text-[10px] font-bold uppercase tracking-[0.12em] text-secondary-container mb-md" style={mono}>Scoring dimensions</div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-md">
            {dims.map((d, i) => (
              <div key={i} className="rounded-lg border border-on-primary-fixed-variant/15 bg-surface-container-lowest/50 p-md flex flex-col gap-1.5">
                <div className="flex items-center justify-between gap-sm">
                  <span className="text-[13px] font-semibold text-on-surface truncate">{d.label || d.key || `dimension ${i + 1}`}</span>
                  {(d.scale_min != null || d.scale_max != null) && (
                    <span className="text-[10px] text-on-surface-variant flex-none" style={{ ...mono, fontVariantNumeric: "tabular-nums" }}>{d.scale_min ?? "?"}–{d.scale_max ?? "?"}</span>
                  )}
                </div>
                {d.description && <p className="text-[11px] text-on-surface-variant leading-relaxed line-clamp-3">{d.description}</p>}
                {(d.sub_factors || []).length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-0.5">
                    {d.sub_factors.map((s, j) => (
                      <span key={j} className="text-[9px] px-2 py-0.5 rounded-full bg-surface-variant text-on-surface-variant border border-on-primary-fixed-variant/20">{s.label || `sub ${j + 1}`}</span>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Editable Governance Framework ── */
const SIGNAL_MODES = ["reactive", "proactive"];
const METRIC_KINDS = ["behavioural", "functional", "impact"];
const COMBINE_MODES = ["multiply", "weighted_sum"];

function FrameworkEditor({ fw, onChange }) {
  const set = (k, v) => onChange({ ...fw, [k]: v });
  const setArr = (k, i, patch) => set(k, (fw[k] || []).map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const del = (k, i) => set(k, (fw[k] || []).filter((_, j) => j !== i));
  const add = (k, row) => set(k, [...(fw[k] || []), row]);
  const acc = fw.accountability || {};
  const setAcc = (sub, v) => set("accountability", { ...acc, [sub]: v });

  return (
    <div className="flex flex-col">
      {/* name + objective */}
      <div className="flex flex-col gap-xs mb-sm">
        <Field value={fw.name} onChange={(v) => set("name", v)} placeholder="Framework name" mono={false}
          className="text-sm font-semibold" />
        <Field value={fw.objective} onChange={(v) => set("objective", v)} placeholder="Objective — what this framework is for" mono={false} />
      </div>

      {/* signal sources */}
      <SectionHead icon="sensors" title="Signal sources · where a problem shows up" />
      <div className="flex flex-col gap-xs">
        {(fw.signal_sources || []).map((s, i) => (
          <RowCard key={i} onRemove={() => del("signal_sources", i)}>
            <Field value={s.label} onChange={(v) => setArr("signal_sources", i, { label: v })} placeholder="label" mono={false} className="w-52" />
            <Field value={(s.examples || []).join(", ")} onChange={(v) => setArr("signal_sources", i, { examples: v.split(",").map((x) => x.trim()).filter(Boolean) })} placeholder="examples (comma-sep)" mono={false} className="flex-1 min-w-[180px]" />
            <select value={s.mode || ""} onChange={(e) => setArr("signal_sources", i, { mode: e.target.value })}
              className="bg-surface-container-highest border border-on-primary-fixed-variant/20 rounded px-1 py-1 text-[11px]" style={{ fontFamily: "JetBrains Mono" }}>
              {["", ...SIGNAL_MODES].map((o) => <option key={o} value={o}>{o || "mode?"}</option>)}
            </select>
          </RowCard>
        ))}
        <AddRow label="signal source" onClick={() => add("signal_sources", { label: "", examples: [], mode: "reactive" })} />
      </div>

      {/* dimensions */}
      <SectionHead icon="straighten" title="Dimensions · weighted scoring axes" />
      <div className="flex flex-col gap-sm">
        {(fw.dimensions || []).map((d, i) => (
          <DimensionCard key={i} dim={d}
            onChange={(patch) => setArr("dimensions", i, patch)}
            onRemove={() => del("dimensions", i)} />
        ))}
        <AddRow label="dimension" onClick={() => add("dimensions", { key: "", label: "", description: "", scale_min: 1, scale_max: 10, sub_factors: [], ladder: [] })} />
      </div>

      {/* combine + formula */}
      <SectionHead icon="functions" title="Combine + formula" />
      <div className="flex flex-wrap gap-xs items-center">
        <span className="text-[10px] text-on-surface-variant">combine</span>
        <select value={fw.combine || ""} onChange={(e) => set("combine", e.target.value)}
          className="bg-surface-container-highest border border-on-primary-fixed-variant/20 rounded px-1 py-1 text-[11px]" style={{ fontFamily: "JetBrains Mono" }}>
          {COMBINE_MODES.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
        <Field value={fw.formula} onChange={(v) => set("formula", v)} placeholder="formula (e.g. Priority Index = Scale × Severity × Recoverability)" mono={false} className="flex-1 min-w-[240px]" />
      </div>

      {/* bands */}
      <SectionHead icon="stacked_bar_chart" title="Bands" />
      <div className="flex flex-col gap-xs">
        {(fw.bands || []).map((b, i) => (
          <RowCard key={i} onRemove={() => del("bands", i)}>
            <Field value={b.label} onChange={(v) => setArr("bands", i, { label: v })} placeholder="band" className="w-20" />
            <Field value={b.meaning} onChange={(v) => setArr("bands", i, { meaning: v })} placeholder="meaning" mono={false} className="flex-1 min-w-[160px]" />
            <Field value={b.action} onChange={(v) => setArr("bands", i, { action: v })} placeholder="action" mono={false} className="flex-1 min-w-[140px]" />
          </RowCard>
        ))}
        <AddRow label="band" onClick={() => add("bands", { label: "", meaning: "", action: "" })} />
      </div>

      {/* band movement */}
      <SectionHead icon="swap_horiz" title="Band movement" />
      <div className="flex flex-col gap-xs">
        {(fw.band_movement || []).map((m, i) => (
          <RowCard key={i} onRemove={() => del("band_movement", i)}>
            <Field value={m.from} onChange={(v) => setArr("band_movement", i, { from: v })} placeholder="from" className="w-20" />
            <span className="text-on-surface-variant">→</span>
            <Field value={m.to} onChange={(v) => setArr("band_movement", i, { to: v })} placeholder="to" className="w-20" />
            <Field value={m.condition} onChange={(v) => setArr("band_movement", i, { condition: v })} placeholder="condition" mono={false} className="flex-1 min-w-[200px]" />
          </RowCard>
        ))}
        <AddRow label="movement rule" onClick={() => add("band_movement", { from: "", to: "", condition: "" })} />
      </div>

      {/* metrics */}
      <SectionHead icon="query_stats" title="Metrics" />
      <div className="flex flex-col gap-xs">
        {(fw.metrics || []).map((m, i) => (
          <RowCard key={i} onRemove={() => del("metrics", i)}>
            <Field value={m.name} onChange={(v) => setArr("metrics", i, { name: v })} placeholder="name" mono={false} className="w-52" />
            <Field value={m.definition} onChange={(v) => setArr("metrics", i, { definition: v })} placeholder="definition" mono={false} className="flex-1 min-w-[180px]" />
            <select value={m.kind || ""} onChange={(e) => setArr("metrics", i, { kind: e.target.value })}
              className="bg-surface-container-highest border border-on-primary-fixed-variant/20 rounded px-1 py-1 text-[11px]" style={{ fontFamily: "JetBrains Mono" }}>
              {["", ...METRIC_KINDS].map((o) => <option key={o} value={o}>{o || "kind?"}</option>)}
            </select>
          </RowCard>
        ))}
        <AddRow label="metric" onClick={() => add("metrics", { name: "", definition: "", kind: "behavioural" })} />
      </div>

      {/* accountability */}
      <SectionHead icon="diversity_3" title="Accountability" />
      <div className="flex flex-col gap-xs">
        <div className="flex flex-wrap gap-xs items-center">
          <span className="text-[10px] text-on-surface-variant">owners by</span>
          <Field value={acc.owners_by} onChange={(v) => setAcc("owners_by", v)} placeholder="how owners are assigned" mono={false} className="flex-1 min-w-[220px]" />
          <span className="text-[10px] text-on-surface-variant">SLA hrs</span>
          <Field value={acc.sla_hours ?? ""} onChange={(v) => setAcc("sla_hours", v === "" ? "" : Number(v))} placeholder="hours" className="w-20" />
        </div>
        <Field value={acc.score_def} onChange={(v) => setAcc("score_def", v)} placeholder="governance score definition" mono={false} />
      </div>

      {/* items catalogue */}
      <SectionHead icon="format_list_bulleted" title="Items · prioritised problems catalogue" />
      <div className="flex flex-col gap-xs">
        {(fw.items || []).map((it, i) => (
          <RowCard key={i} onRemove={() => del("items", i)}>
            <Field value={it.name} onChange={(v) => setArr("items", i, { name: v })} placeholder="problem / pain-point" mono={false} className="w-full mb-1" />
            <Field value={it.journey_stage} onChange={(v) => setArr("items", i, { journey_stage: v })} placeholder="stage" mono={false} className="w-40" />
            <Field value={it.priority} onChange={(v) => setArr("items", i, { priority: v })} placeholder="P?" className="w-14" />
            <Field value={it.impact_type} onChange={(v) => setArr("items", i, { impact_type: v })} placeholder="impact" className="w-20" />
            <span className="text-[10px] text-on-surface-variant">S</span>
            <Field value={it.scale} onChange={(v) => setArr("items", i, { scale: numOrStr(v) })} placeholder="scale" className="w-14" />
            <span className="text-[10px] text-on-surface-variant">×</span>
            <Field value={it.severity} onChange={(v) => setArr("items", i, { severity: numOrStr(v) })} placeholder="sev" className="w-14" />
            <span className="text-[10px] text-on-surface-variant">×</span>
            <Field value={it.recoverability} onChange={(v) => setArr("items", i, { recoverability: v })} placeholder="recov" className="w-14" />
            <span className="text-[10px] text-on-surface-variant">=</span>
            <Field value={it.index} onChange={(v) => setArr("items", i, { index: numOrStr(v) })} placeholder="index" className="w-16" />
            <Field value={it.owner} onChange={(v) => setArr("items", i, { owner: v })} placeholder="owner" mono={false} className="flex-1 min-w-[120px]" />
          </RowCard>
        ))}
        <AddRow label="item" onClick={() => add("items", { name: "", journey_stage: "", priority: "", impact_type: "", scale: "", severity: "", recoverability: "", index: "", metrics: {}, root_cause: "", policy_proposed: "", owner: "", timeline: "", nps_impact: "", recovery_path: "" })} />
      </div>
    </div>
  );
}

/* One dimension card: label/key/description/scale range + sub-factor rows + optional ladder. */
function DimensionCard({ dim, onChange, onRemove }) {
  const sub = dim.sub_factors || [];
  const ladder = dim.ladder || [];
  const setSub = (i, patch) => onChange({ sub_factors: sub.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const delSub = (i) => onChange({ sub_factors: sub.filter((_, j) => j !== i) });
  const addSub = () => onChange({ sub_factors: [...sub, { label: "", scale_min: 1, scale_max: 5, normalization: "", note: "" }] });
  const setLad = (i, patch) => onChange({ ladder: ladder.map((l, j) => (j === i ? { ...l, ...patch } : l)) });
  const delLad = (i) => onChange({ ladder: ladder.filter((_, j) => j !== i) });
  const addLad = () => onChange({ ladder: [...ladder, { level: ladder.length + 1, label: "" }] });

  return (
    <div className="bg-surface-container-lowest border border-on-primary-fixed-variant/15 rounded-lg p-md">
      <div className="flex items-start gap-sm">
        <div className="flex-1 flex flex-col gap-xs min-w-0">
          <div className="flex items-center gap-xs flex-wrap">
            <Field value={dim.label} onChange={(v) => onChange({ label: v })} placeholder="dimension label" mono={false} className="w-44 font-semibold" />
            <Field value={dim.key} onChange={(v) => onChange({ key: v })} placeholder="key" className="w-28" />
            <span className="text-[10px] text-on-surface-variant">range</span>
            <Field value={dim.scale_min ?? ""} onChange={(v) => onChange({ scale_min: numOrStr(v) })} placeholder="min" className="w-12" />
            <span className="text-on-surface-variant">–</span>
            <Field value={dim.scale_max ?? ""} onChange={(v) => onChange({ scale_max: numOrStr(v) })} placeholder="max" className="w-12" />
          </div>
          <Field value={dim.description} onChange={(v) => onChange({ description: v })} placeholder="what this dimension measures" mono={false} />
        </div>
        <button onClick={onRemove} className="flex-none w-6 h-6 grid place-items-center rounded text-on-surface-variant hover:text-error">
          <span className="material-symbols-outlined" style={{ fontSize: 15 }}>close</span>
        </button>
      </div>

      {/* sub-factors */}
      <div className="mt-sm pl-md border-l border-on-primary-fixed-variant/15 flex flex-col gap-xs">
        <div className="text-[9px] uppercase tracking-wide text-on-surface-variant">sub-factors</div>
        {sub.map((s, i) => (
          <RowCard key={i} onRemove={() => delSub(i)}>
            <Field value={s.label} onChange={(v) => setSub(i, { label: v })} placeholder="sub-factor" mono={false} className="w-44" />
            <Field value={s.scale_min ?? ""} onChange={(v) => setSub(i, { scale_min: numOrStr(v) })} placeholder="min" className="w-12" />
            <span className="text-on-surface-variant">–</span>
            <Field value={s.scale_max ?? ""} onChange={(v) => setSub(i, { scale_max: numOrStr(v) })} placeholder="max" className="w-12" />
            <Field value={s.normalization} onChange={(v) => setSub(i, { normalization: v })} placeholder="normalization" className="w-32" />
            <Field value={s.note} onChange={(v) => setSub(i, { note: v })} placeholder="note" mono={false} className="flex-1 min-w-[120px]" />
          </RowCard>
        ))}
        <AddRow label="sub-factor" onClick={addSub} />
      </div>

      {/* optional ladder */}
      <div className="mt-sm pl-md border-l border-on-primary-fixed-variant/15 flex flex-col gap-xs">
        <div className="text-[9px] uppercase tracking-wide text-on-surface-variant">ladder (optional ordinal)</div>
        {ladder.map((l, i) => (
          <RowCard key={i} onRemove={() => delLad(i)}>
            <span className="text-[10px] text-on-surface-variant">level</span>
            <Field value={l.level ?? ""} onChange={(v) => setLad(i, { level: numOrStr(v) })} placeholder="n" className="w-12" />
            <Field value={l.label} onChange={(v) => setLad(i, { label: v })} placeholder="label" mono={false} className="flex-1 min-w-[160px]" />
          </RowCard>
        ))}
        <AddRow label="ladder level" onClick={addLad} />
      </div>
    </div>
  );
}

function compTone(c) {
  if (c == null) return "bg-warn/10 text-warn border border-warn/40";
  if (c >= 80) return "bg-tertiary/10 text-tertiary border border-tertiary/40";
  if (c >= 55) return "bg-secondary-container/10 text-secondary-container border border-secondary-container/40";
  return "bg-error/10 text-error border border-error/40";
}

/* Tiny inline SVG sparkline for the composite trend (self-contained, no deps). */
function TrendSparkline({ series }) {
  if (!series || series.length === 0)
    return <div className="text-[11px] text-on-surface-variant">No trend yet.</div>;
  if (series.length === 1) {
    const p = series[0];
    return <div className="text-[11px] text-on-surface-variant" style={{ fontFamily: "JetBrains Mono" }}>{p.day}: {p.avg_composite} ({p.count})</div>;
  }
  const W = 320, H = 54, pad = 4;
  const xs = series.map((_, i) => pad + (i * (W - 2 * pad)) / (series.length - 1));
  const ys = series.map((p) => H - pad - ((p.avg_composite / 100) * (H - 2 * pad)));
  const path = xs.map((x, i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ height: H }}>
        <polyline points={`${xs[0]},${H} ${xs.map((x, i) => `${x},${ys[i]}`).join(" ")} ${xs[xs.length - 1]},${H}`}
          fill="var(--good, rgba(74,222,128,0.12))" opacity="0.15" />
        <path d={path} fill="none" stroke="var(--good, #4ade80)" strokeWidth="1.5" />
        {xs.map((x, i) => <circle key={i} cx={x} cy={ys[i]} r="2" fill="var(--good, #4ade80)" />)}
      </svg>
      <div className="flex justify-between text-[9px] text-on-surface-variant" style={{ fontFamily: "JetBrains Mono" }}>
        <span>{series[0].day}</span><span>{series[series.length - 1].day}</span>
      </div>
    </div>
  );
}

/* ── Support Command — ONE state, tabs inside (Command · Authoring · Auditing · Governance · Concern Log) ── */
const SC_TABS = [
  ["command", "Command Deck", "space_dashboard"],
  ["authoring", "Authoring Studio", "edit_note"],
  ["auditing", "Auditing Studio", "fact_check"],
  ["governance", "Governance", "gavel"],
  ["concernlog", "Concern Log", "receipt_long"],
];
const SC_COMP = { command: Command, authoring: AuthoringStudio, auditing: AuditingStudio, governance: GovernanceFramework, concernlog: Ledger };
export default function SupportCommand() {
  const [tab, setTab] = useState("command");
  const C = SC_COMP[tab];
  return (
    <div className="h-full flex flex-col gap-gutter">
      <div className="flex gap-sm flex-wrap">
        {SC_TABS.map(([k, label, icon]) => (
          <button key={k} onClick={() => setTab(k)}
            className={`flex items-center gap-2 px-md py-sm rounded-lg text-sm font-semibold transition-all border ${
              tab === k ? "bg-secondary-container text-on-secondary border-secondary-container"
                        : "glass-card text-on-surface-variant hover:text-secondary-container border-transparent"}`}>
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>{icon}</span>{label}
          </button>
        ))}
      </div>
      <div className="flex-1 overflow-y-auto custom-scrollbar min-h-0"><C /></div>
    </div>
  );
}
