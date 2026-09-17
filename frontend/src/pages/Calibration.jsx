import { useEffect, useState } from "react";
import { Gauge, TriangleAlert, ChevronRight, Scale, Users } from "lucide-react";
import { getCalibration } from "../lib/api.js";

/* ── "What is calibration for?" — answered at the top, before any numbers ─────────────────────

   You said you did not know what this page was for. That is the finding, not a gap in the
   explanation: the page opened with bins, counts and a Brier score and never stated its purpose,
   so the only readers who could use it were the ones who already knew.

   ── SO IT IS NOW BUILT BACKWARDS FROM THE QUESTION IT ANSWERS ────────────────────────────────
   Two questions, and they are unrelated to each other, which is the other reason the old page
   was confusing — it presented them as one subject:

   1. **The trust gate blocks an action when confidence < 0.80. Does that 0.80 mean anything?**
      A number written as a probability implies that decisions at 0.9 are right about 90% of the
      time. If that is true the threshold is a dial you can tune against a cost. If it is not, it
      is a label wearing probability notation, and moving it is guesswork.

      MEASURED ANSWER: it is a label. The engine emits exactly FOUR distinct confidence values
      across every decision it has ever made — 0.2, 0.4, 0.9, 0.92 — against a 0.80 threshold.
      Two of them are below it and two above, so in practice it is a two-way switch. Worth
      knowing, cheap to state, and it does not need a page.

   2. **When our LLM judge scores a resolution, does it agree with a human?** THIS is what you
      thought calibration meant, and you were right that it is the more interesting question.
      Cohen's κ = 0.052 on n=1,089 paired verdicts. Raw agreement is 90.6%, which sounds
      excellent and is not: κ corrects for agreement you would get by chance, and when 94% of
      cases are "pass", agreeing on the passes is free. κ near zero means the judge and the human
      are agreeing about as much as two people flipping coins with the same bias.

      That is the number that qualifies every audit score in this platform, and it belongs next
      to those scores rather than on a page of its own — which is where it is going.

   The four "we cannot measure this yet" blocks the old page carried are collapsed into one line
   at the bottom. They were honest and they were also 60% of the page. ── */

const pct = (n) => (n == null ? "—" : `${n}%`);

function Verdict({ tone, kicker, headline, children }) {
  const c = tone === "bad" ? "border-l-warn/70" : tone === "good" ? "border-l-tertiary/70"
                                                : "border-l-secondary-container/70";
  const t = tone === "bad" ? "text-warn" : tone === "good" ? "text-tertiary"
                                         : "text-secondary-container";
  return (
    <section className={`glass-card rounded-xl p-lg border-l-4 ${c}`}>
      <div className={`flex items-center gap-sm text-[10px] font-bold uppercase tracking-[0.13em] ${t}`}
        style={{ fontFamily: "JetBrains Mono" }}>{kicker}</div>
      <h2 className="text-[17px] font-semibold text-on-surface mt-sm leading-snug">{headline}</h2>
      <div className="text-[12.5px] text-on-surface-variant mt-sm leading-relaxed space-y-sm">
        {children}
      </div>
    </section>
  );
}

export default function Calibration() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  const [limits, setLimits] = useState(false);

  useEffect(() => {
    getCalibration().then(setD).catch(() => setErr("Could not read the calibration report."));
  }, []);

  if (err) return <div className="glass-card rounded-xl p-lg text-warn">{err}</div>;
  if (!d) return <div className="glass-card rounded-xl p-lg text-on-surface-variant">Measuring…</div>;

  const r = d.reliability || {};
  const k = d.kapture || {};
  const vals = r.distinct_confidence_values || [];
  const emptyBins = (r.bins || []).filter((b) => !b.n).length;

  return (
    <div className="space-y-lg">
      {/* ── what this page is for, first ── */}
      <section className="glass-card rounded-xl p-lg">
        <div className="flex items-center gap-sm text-[10px] font-bold uppercase tracking-[0.13em] text-on-surface-variant"
          style={{ fontFamily: "JetBrains Mono" }}>
          <Gauge size={13} />What this page is for
        </div>
        <p className="text-[13px] text-on-surface mt-sm leading-relaxed">
          Two of this platform's numbers claim more than they can prove. This page checks both and
          reports what they actually mean.
        </p>
        <div className="grid md:grid-cols-2 gap-md mt-md text-[12px] leading-relaxed">
          <div className="rounded-lg bg-surface-variant/25 p-md">
            <div className="flex items-center gap-sm text-secondary-container font-semibold">
              <Scale size={13} />The gate's confidence
            </div>
            <p className="text-on-surface-variant mt-1.5">
              The trust gate blocks an action below <b className="text-on-surface">{r.threshold ?? 0.8}</b>.
              If that reads as a probability, decisions at 0.9 should be right ~90% of the time —
              and the threshold becomes a dial you can tune. Is it?
            </p>
          </div>
          <div className="rounded-lg bg-surface-variant/25 p-md">
            <div className="flex items-center gap-sm text-secondary-container font-semibold">
              <Users size={13} />The audit judge
            </div>
            <p className="text-on-surface-variant mt-1.5">
              Every resolution is scored by an LLM judge. When a human scores the same case, do
              they agree — beyond the agreement you would get by chance?
            </p>
          </div>
        </div>
      </section>

      {/* ── ANSWER 1 ── */}
      <Verdict tone="bad" kicker={<><TriangleAlert size={13} />Answer 1 — it is a label, not a probability</>}
        headline={`The engine emits ${vals.length} distinct confidence values, ever.`}>
        <p>
          Across <b className="text-on-surface">{(r.concerns ?? 0).toLocaleString("en-IN")}</b> concerns,
          {" "}<b className="text-on-surface">{r.with_confidence ?? 0}</b> carry a confidence, and every
          one of them is one of these:
        </p>
        <div className="flex gap-sm flex-wrap my-sm">
          {vals.map((v) => (
            <span key={v} className={`rounded-lg px-3 py-1.5 text-[13px] font-bold border
              ${v >= (r.threshold ?? 0.8)
                ? "bg-tertiary/10 text-tertiary border-tertiary/40"
                : "bg-warn/10 text-warn border-warn/40"}`}
              style={{ fontFamily: "JetBrains Mono", fontVariantNumeric: "tabular-nums" }}>
              {v.toFixed(2)}{v >= (r.threshold ?? 0.8) ? " ✓ passes" : " ✗ blocks"}
            </span>
          ))}
        </div>
        <p>
          Two below the threshold, two above. So it is a <b className="text-on-surface">two-way
          switch written in probability notation</b> — and{" "}
          <b className="text-on-surface">{emptyBins} of {(r.bins || []).length}</b> reliability bins
          are empty because nothing ever lands in them.
        </p>
        <p className="text-on-surface-variant/85">
          <b className="text-on-surface">What follows from that:</b> the threshold cannot be tuned
          against a cost, because there is nothing between 0.4 and 0.9 to move it through. Raising
          it to 0.95 would block everything; lowering it to 0.5 would pass everything. It is a
          policy choice about which of two buckets escalates — which is a perfectly reasonable
          design, just not a calibrated one. The honest fix is fewer digits, not more measurement:
          the gate is doing pass/block, so it should say so.
        </p>
      </Verdict>

      {/* ── ANSWER 2 — MOVED, and this is the pointer, not a copy ─────────────────────────
          The κ block now renders in the Auditing Studio, above the audit scores it qualifies.
          Deliberately not duplicated here: two copies of a caveat drift, and the one that drifts
          is always the one on the page nobody opens. */}
      {k.available && (
        <Verdict tone="bad" kicker={<><Users size={13} />Answer 2 — moved to where it is read</>}
          headline={`Cohen's κ = ${k.cohen_kappa} on n=${(k.n || 0).toLocaleString("en-IN")} paired verdicts — now shown in the Auditing Studio.`}>
          <p>
            Our LLM judge and a human agree {pct(k.agreement_pct)} of the time, and κ ={" "}
            {k.cohen_kappa} — near zero once chance agreement is removed. That number qualifies{" "}
            <b className="text-on-surface">every audit score in the platform</b>, so it now sits
            directly above those scores in{" "}
            <b className="text-on-surface">Support Command → Auditing Studio → Scores &amp; Rubric</b>{" "}
            instead of here.
          </p>
          <p className="text-on-surface-variant/85">
            Why it mattered: <code className="text-on-surface">grep kappa</code> used to hit this
            file and nothing else — so the studio that scores every concern with an LLM judge held
            no human verdict at all, and the one measurement of whether to believe it was a nav
            click away on a page whose purpose you told us was unclear.
          </p>
        </Verdict>
      )}

      {/* ── known limits, collapsed ── */}
      <section className="glass-card rounded-xl overflow-hidden">
        <button onClick={() => setLimits((v) => !v)}
          className="w-full px-lg h-[44px] flex items-center gap-sm text-left hover:bg-surface-variant/20 transition-colors">
          <ChevronRight size={13} style={{ transform: limits ? "rotate(90deg)" : "none",
            transition: "transform .15s" }} />
          <span className="text-[12.5px] text-on-surface-variant">
            Known limits — what these two numbers still cannot tell you
          </span>
        </button>
        {limits && (
          <div className="px-lg pb-lg pt-sm border-t border-on-primary-fixed-variant/20
                          text-[12px] text-on-surface-variant leading-relaxed space-y-sm">
            <p>
              <b className="text-on-surface">Only {r.labelled ?? 0} decisions have a ground-truth
              label.</b> A reliability curve needs outcomes — was the reversal actually correct? —
              and those arrive from L3 closures, of which there are almost none. Until they exist,
              the bins can only be counted, not scored.
            </p>
            <p>
              <b className="text-on-surface">The κ pairs come from a different task.</b> They are
              audit-quality verdicts on Kapture tickets, not gate decisions, so they qualify the
              judge and not the gate. Nothing here measures the gate's accuracy, and nothing can
              until labelled outcomes exist.
            </p>
            <p>
              <b className="text-on-surface">Selection on the outcome makes precision undefined.</b>
              {" "}The hindsight cohort is assembled from cases that already went a certain way, so
              it has no negative class. Precision and recall are not unmeasured there — they are
              undefined, which is a different and more important statement.
            </p>
          </div>
        )}
      </section>
    </div>
  );
}
