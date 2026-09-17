import { useEffect, useState } from "react";
import { Users } from "lucide-react";
import { getCalibration } from "../lib/api.js";

/* ── Cohen's κ — the caveat on every audit score, moved next to the scores ────────────────────

   This lived on the Calibration page, which is not where anyone reads an audit score. Its own
   text said so: "This κ is the caveat on every audit score in the platform, so it belongs beside
   those scores in the Auditing Studio rather than on a page nobody opens."

   ── WHY THE MOVE IS THE POINT, NOT THE TIDYING ───────────────────────────────────────────────
   `grep kappa` across the repo hit exactly one file: the calibration page. So the Auditing
   Studio — which scores every concern with an LLM judge — contained no human verdict anywhere,
   which is a machine grading itself and reporting the grade. The number that says how far to
   trust that grade was one nav click away, on a page the user said they did not know the purpose
   of. A caveat nobody reaches is not a caveat.

   Fetches its own data rather than taking a prop: the Auditing Studio's other panels each fetch
   their own, and threading calibration through AuditingStudio -> AuditScores would couple two
   panels that are otherwise independent. Renders nothing at all when the pairs are unavailable —
   an empty κ box beside real scores would read as "κ = 0", which is the opposite of unknown. ── */

export default function JudgeAgreement() {
  const [k, setK] = useState(null);

  useEffect(() => {
    let alive = true;
    getCalibration()
      .then((d) => { if (alive) setK(d?.kapture || null); })
      .catch(() => { /* a caveat panel must never take the studio down */ });
    return () => { alive = false; };
  }, []);

  if (!k?.available) return null;
  const pct = (n) => (n == null ? "—" : `${n}%`);

  return (
    <section className="glass-card rounded-xl p-lg border-l-4 border-l-warn/70">
      <div className="flex items-center gap-sm text-[10px] font-bold uppercase tracking-[0.13em] text-warn"
        style={{ fontFamily: "JetBrains Mono" }}>
        <Users size={13} />How far to trust the scores above
      </div>
      <h2 className="text-[17px] font-semibold text-on-surface mt-sm leading-snug">
        {`Cohen's κ = ${k.cohen_kappa} on n=${(k.n || 0).toLocaleString("en-IN")} paired verdicts.`}
      </h2>
      <div className="text-[12.5px] text-on-surface-variant mt-sm leading-relaxed space-y-sm">
        <div className="grid grid-cols-3 gap-md my-sm">
          {[["raw agreement", pct(k.agreement_pct), "text-tertiary"],
            ["Cohen's κ", String(k.cohen_kappa), "text-warn"],
            ["engine fail rate", pct(k.engine_fail_rate), "text-on-surface"]].map(([l, v, c]) => (
            <div key={l} className="rounded-lg bg-surface-variant/25 p-md">
              <div className="text-[9.5px] uppercase tracking-[0.1em] text-on-surface-variant"
                style={{ fontFamily: "JetBrains Mono" }}>{l}</div>
              <div className={`text-xl font-bold mt-1 ${c}`}
                style={{ fontVariantNumeric: "tabular-nums" }}>{v}</div>
            </div>
          ))}
        </div>
        <p>
          κ corrects for the agreement you would get by chance. When the overwhelming majority of
          cases are a pass, agreeing on the passes is free — so {pct(k.agreement_pct)} raw
          agreement and κ = {k.cohen_kappa} together mean the judge and the human are agreeing{" "}
          <b className="text-on-surface">about as much as two people flipping coins with the same
          bias</b>. Where it matters — the cases that fail — they do not agree.
        </p>
        <p className="text-on-surface-variant/85">
          <b className="text-on-surface">And what it does NOT measure:</b> {k.not_measures}. It
          scores {k.measures}. Two different numbers, and they are easy to read as one.
        </p>
        <p className="text-on-surface-variant/85">
          <b className="text-on-surface">Read this before the scores above, not after.</b> Those
          are produced by an LLM judge; this is the only measurement in the platform of whether
          that judge agrees with a person. It comes from a different task — audit-quality verdicts
          on Kapture tickets rather than gate decisions — so it qualifies the judge, not the gate.
        </p>
      </div>
    </section>
  );
}
