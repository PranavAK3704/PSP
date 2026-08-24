import { useEffect, useMemo, useRef, useState } from "react";
import { BarChart3, Send, Sparkles, TriangleAlert, ExternalLink, TrendingUp,
         TrendingDown, ChevronRight, Database, ThumbsUp, PartyPopper,
         ArrowDown, Star, Scissors } from "lucide-react";
import { getGrowthIndex, getGrowth, stream } from "../lib/api.js";
import { n0, inr, num } from "../lib/format.js";
import { S, RP, LEVERS, WHY } from "../growth/strings.js";
import AtRiskPanel, { SeverityBanner } from "../components/AtRiskPanel.jsx";

/* ── Growth Dashboard + docked support widget ─────────────────────────────────────────────
   THE POINT OF THIS SCREEN, in one sentence: the captain is looking at their own dashboard,
   does not understand it, types "why is my load low" into the widget, and gets an answer
   computed from THE SAME PAYLOAD the dashboard is drawing. Same data, two presentations.

   Deliberately NOT a pixel copy of valmo-partner-webview. @meesho/merlin-ui is a private
   package and cannot be installed, so this is built from PSP's own tokens and the df-* classes
   from DataFoundation.jsx. What IS copied verbatim is the label vocabulary
   (GROWTH_DASHBOARD_STRINGS) and the metric titles, because a captain recognises the words —
   an approximation of the layout with invented labels would read as a mock-up of something
   else.

   The widget DOCKS; it does not replace. Both have to be visible at once or the demo beat
   ("same data, two presentations") is a claim rather than a demonstration. ── */

/* Strings, levers and tooltips live in ../growth/strings.js — a mirror of the upstream
   constants.ts, kept in one diffable block. Formatters live in ../lib/format.js. */

// Mirrors the engine's Tri: null when unreadable, so an unparseable value is never styled as
// passing. The panel's own parseNumeric returns 0 here, which for a lower-is-better metric
// renders as good — see backend adapters/growth/contract.py for why we diverge.
const statusOf = (cur, tgt, higherIsBetter) => {
  const c = num(cur), t = num(tgt);
  if (c === null || t === null) return null;
  return (higherIsBetter ? c >= t : c <= t) ? "good" : "not_good";
};

/* ── The waterfall ────────────────────────────────────────────────────────────────────────
   A REAL waterfall, not five columns on a shared baseline. Three things make it one:

   1. **The scale uses only the GROUNDED bars** — max_potential, current_eligible,
      final_manifested. Scaling to include the floating bars would shrink the very quantities
      the reader is comparing.
   2. **Bars 1 and 3 FLOAT**, on `marginBottom`. Bar 1 (allocation miss) sits on top of Current
      Eligible; bar 3 sits on top of Current Eligible when it is extra orders, and on top of
      Final Manifested when it is a capacity cut. That is what makes the arithmetic visible:
      the floating bar plus the bar under it equals the bar beside it.
   3. **Labels live in a SEPARATE ROW.** They have to: `white-space: pre-line` renders the `\n`
      in each label as two lines, and if the labels were inside the floating bar groups they
      would float with them. Separating the rows is what lets bars move while labels stay
      baseline-aligned.

   THE BUG THIS FIXES: bar 4's sign test was `b4 >= 0`, so a `bar4_value` of exactly 0 rendered
   as "Extra Orders" in the win colour. Upstream uses `> 0` strictly and has an explicit unit
   test named "should handle bar4_value of 0 as capacity cut". Zero is a cut, not a win.

   THE PALETTE, and why not violet. Bars 0/2/4 are the SAME MEASURE AT THREE STAGES, so they
   take one hue — the rule already written in DataFoundation.jsx. The old code painted 0 and 2
   teal but 4 violet, which implies three different identities; and `--c-violet` is a RESERVED
   status hue ("status: reversed" in styles.css), which DataFoundation is explicit must never be
   reused as "series 4". So: grounded = --c-teal (magnitude), loss = --c-rose (already "failed"),
   extra = --c-amber. Three hues, each mapping 1:1 onto an upstream hue, and the CVD-validated
   set is untouched.

   Upstream's own hexes (#C5D7FF for the grounded bars) are NOT used: at L≈0.86 they are far
   outside the documented L 0.48–0.67 fill band for this dark surface and would flatten against
   each other. ── */
const BAR_H = 200;      // upstream is 260 on a taller page; scaled to PSP's card

function Waterfall({ os }) {
  const { bars, maxValue, isExtra } = useMemo(() => {
    const b4 = Number(os.bar4_value || 0);
    const extra = b4 > 0;            // STRICTLY greater — 0 is a capacity cut
    const v = [Number(os.max_potential) || 0, Number(os.missed_in_allocation) || 0,
               Number(os.current_eligible) || 0, Math.abs(b4),
               Number(os.final_manifested) || 0];
    return {
      isExtra: extra,
      maxValue: Math.max(v[0], v[2], v[4], 1),   // GROUNDED bars only
      bars: [
        { label: S.MAXIMUM_POTENTIAL, value: v[0], tone: "var(--c-teal)" },
        { label: S.ORDERS_MISSED_ALLOCATION, value: v[1], tone: "var(--c-rose)", signed: -1 },
        { label: S.CURRENT_ELIGIBLE, value: v[2], tone: "var(--c-teal)" },
        { label: extra ? S.EXTRA_ORDERS : S.ORDERS_MISSED_CAPACITY, value: v[3],
          tone: extra ? "var(--c-amber)" : "var(--c-rose)", signed: extra ? 1 : -1 },
        { label: S.FINAL_MANIFESTED, value: v[4], tone: "var(--c-teal)" },
      ],
    };
  }, [os]);

  const h = (value) => Math.max((Number(value) || 0) / maxValue * BAR_H, 4);
  // The float offsets. Bar 1 rests on Current Eligible; bar 3 rests on Current Eligible when it
  // is extra (it is added to what you were eligible for) and on Final Manifested when it is a
  // cut (it was taken off the top). If a payload's arithmetic does not close, the bars visibly
  // fail to line up — which is the correct failure mode for a chart of an identity.
  const floatFor = (i) => i === 1 ? h(bars[2].value)
    : i === 3 ? (isExtra ? h(bars[2].value) : h(bars[4].value)) : 0;
  const tips = os.bar_tooltips || [];

  return (
    <div style={{ marginTop: 8 }}>
      <div style={{ display: "flex", alignItems: "flex-end", gap: 10,
        height: BAR_H + 26, minWidth: 0 }}>
        {bars.map((b, i) => (
          <div key={i} title={tips[i] || undefined}
            style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column",
              alignItems: "center", justifyContent: "flex-end", height: "100%",
              marginBottom: floatFor(i) }}>
            <div className="mono" style={{ fontSize: 11.5, fontWeight: 800, color: "var(--text)",
              marginBottom: 3, fontVariantNumeric: "tabular-nums", whiteSpace: "nowrap" }}>
              {b.signed && b.value ? (b.signed > 0 ? "+" : "−") : ""}{n0(b.value)}
            </div>
            {/* 2px surface gap between fills, 4px rounded data-end. */}
            <div style={{ width: "100%", maxWidth: 64, height: h(b.value), background: b.tone,
              borderRadius: "4px 4px 0 0", boxShadow: "0 0 0 2px var(--surface-1)" }} />
          </div>
        ))}
      </div>
      {/* The separate label row — see note 3 above. */}
      <div style={{ display: "flex", gap: 10, marginTop: 7 }}>
        {bars.map((b, i) => (
          <div key={i} className="mono" style={{ flex: 1, minWidth: 0, fontSize: 9,
            color: "var(--text-faint)", textAlign: "center", whiteSpace: "pre-line",
            lineHeight: 1.35 }}>{b.label}</div>
        ))}
      </div>
    </div>
  );
}

/* ── MyOrdersBanner — the page's signature element ──────────────────────────────────────
   This rendered as a mono `827 / 1035` line. Upstream it is the one thing everybody
   recognises: a blue gradient slab reading "Getting 827 out of 1035 Total Orders" with a
   trend chip.

   THE GRADIENT GOES IN VERBATIM, and it is safe on a dark page for a non-obvious reason: it
   is TWO stacked gradients with a 30% black layer ON TOP. Composited, the painted result is
   #324D90 → #506CB2, and white text on those measures 8.1:1 and 5.1:1. Anyone "simplifying"
   the two layers into one #486ece → #739bff would silently turn it into a 3.4:1 slab. That is
   why the black layer is not redundant.

   Every OTHER upstream hex on this page is a light-page tint and is transformed instead — see
   the note on MetricCard. ── */
function TrendChip({ text, isGood }) {
  // Upstream returns null when `is_good === null`, and the check is `=== null`, NOT falsy.
  // `is_good: false` is the case that matters most (two of three fixtures), so a truthiness
  // test here would suppress exactly the chip a struggling captain needs to see.
  if (!text || isGood === null || isGood === undefined) return null;
  const good = isGood === true;
  const Arrow = good ? TrendingUp : TrendingDown;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "4px 10px",
      borderRadius: 16, fontSize: 11.5, fontWeight: 600, whiteSpace: "nowrap",
      // A translucent fill rather than the --good-soft/--bad-soft tokens, because this chip
      // sits on the blue gradient rather than on --bg.
      background: good ? "rgba(78,222,163,0.18)" : "rgba(255,180,171,0.20)",
      color: good ? "var(--good)" : "var(--bad)" }}>
      <Arrow size={12} />{text}
    </span>
  );
}

function MyOrdersBanner({ ym }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, padding: "16px 20px",
      borderRadius: 12, color: "#fff",
      background: "linear-gradient(0deg, rgba(0,0,0,0.3) 0%, rgba(0,0,0,0.3) 100%), "
                + "linear-gradient(90deg, #486ece 0%, #739bff 100%)",
      // Two additions so it reads as elevated rather than pasted onto the glass card — it is
      // the only fully opaque element on a page with a shader and a grid behind it.
      border: "1px solid rgba(255,255,255,0.10)",
      boxShadow: "0 8px 24px -14px rgba(72,110,206,0.85)" }}>
      <div style={{ fontSize: 12, fontWeight: 500, opacity: 0.92 }}>
        {S.YOUR_ORDERS_SUBTITLE}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, flexWrap: "wrap" }}>
        <span style={{ fontSize: 15, fontWeight: 700 }}>{S.GETTING}</span>
        <span style={{ fontSize: 21, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
          {n0(ym.current_orders)}</span>
        <span style={{ fontSize: 15, fontWeight: 700 }}>{S.OUT_OF}</span>
        <span style={{ fontSize: 21, fontWeight: 700, fontVariantNumeric: "tabular-nums" }}>
          {n0(ym.max_potential)}</span>
        <span style={{ fontSize: 15, fontWeight: 700 }}>{S.TOTAL_ORDERS}</span>
        <TrendChip text={ym.order_change_text} isGood={ym.is_good} />
      </div>
    </div>
  );
}

/* ── MetricCard ─────────────────────────────────────────────────────────────────────────
   Four changes from the `df-tile` version, each reproducing something upstream does:

   1. **The HEADER BAND is tinted** — that tint is the entire "this card is bad" signal
      upstream, not a 2px top border. Good is a quiet surface; not_good is a rose wash with a
      rose bottom border.
   2. **A two-column body** split by a 1px border: Current on the left, Target on the right.
   3. **The target carries a dotted underline and a hover tooltip.** Previously `why` was a
      native `title=` attribute — invisible until you wait, and unreachable by keyboard. The
      tooltip is a CSS class (`.gd-tt`) rather than inline styles because its 6px arrow is an
      `::after` pseudo-element, which a React style object cannot express; the CSS also gives
      it `:focus-within`, so tabbing to the value shows it.
   4. **A `View Details ›` footer on EXACTLY two of four cards** — pilot-rate-card and pendency.
      Putting it on all four would look tidier and be wrong.

   ON THE PALETTE: upstream tints a good header #F2F5FA and a bad one #FFCBC9. Both are
   light-page values — #FFCBC9 on this surface is a near-white patch, and upstream's own
   #43A92C-on-#E7FBE0 pairing measures 2.77:1, which fails even on white. So the ROLE is
   reproduced (a tinted band carries the verdict) with tokens that survive this surface. ── */
function MetricCard({ id, title, current, target, status, why, hasDetails, onAsk }) {
  const bad = status === "not_good";
  const tone = status === "good" ? "var(--good)" : bad ? "var(--bad)" : "var(--text-faint)";
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 10, overflow: "hidden",
      background: "var(--surface-1)", display: "flex", flexDirection: "column", minWidth: 0 }}>
      <div style={{ padding: "9px 12px", fontSize: 11.5, fontWeight: 700,
        color: "var(--text-mute)", background: bad ? "rgba(196,62,96,0.18)" : "var(--surface-2)",
        borderBottom: `1px solid ${bad ? "rgba(196,62,96,0.42)" : "var(--line-soft)"}` }}>
        {title}
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", flex: 1 }}>
        <div style={{ padding: 11, minWidth: 0 }}>
          <div className="mono" style={{ fontSize: 9, letterSpacing: ".1em",
            textTransform: "uppercase", color: "var(--text-faint)" }}>{S.CURRENT}</div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 3,
            flexWrap: "wrap" }}>
            <span style={{ fontSize: 17, fontWeight: 800, fontVariantNumeric: "tabular-nums" }}>
              {current ?? "—"}</span>
            <span style={{ fontSize: 9.5, fontWeight: 700, padding: "2px 6px", borderRadius: 4,
              background: "var(--surface-3)", color: tone, whiteSpace: "nowrap" }}>
              {status === "good" ? RP.GOOD.trim() : bad ? RP.NOT_GOOD.trim() : "unreadable"}
            </span>
          </div>
        </div>
        <div style={{ padding: 11, borderLeft: "1px solid var(--line)", minWidth: 0,
          display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
          <div className="mono" style={{ fontSize: 9, letterSpacing: ".1em",
            textTransform: "uppercase", color: "var(--text-faint)" }}>{S.TARGET}</div>
          <span className="gd-tt" tabIndex={why ? 0 : undefined} style={{ marginTop: 3 }}>
            <span style={{ fontSize: 15, fontWeight: 700, color: "var(--text-mute)",
              textDecoration: why ? "underline" : "none", textDecorationStyle: "dotted",
              textDecorationColor: "var(--text-faint)", textUnderlineOffset: 4 }}>
              {target ?? "—"}</span>
            {why && <span className="gd-tt-bubble" role="tooltip">{why}</span>}
          </span>
        </div>
      </div>
      {hasDetails && (
        <button onClick={() => onAsk?.(id)} style={{ display: "flex", alignItems: "center",
          justifyContent: "space-between", padding: "8px 12px", fontSize: 11, fontWeight: 600,
          color: "var(--accent)", background: "none", border: "none",
          borderTop: "1px solid var(--line-soft)", cursor: "pointer", width: "100%" }}>
          {S.VIEW_DETAILS}<ChevronRight size={13} />
        </button>
      )}
    </div>
  );
}

/* ── The right panel — the 35% column of the order-summary section ──────────────────────
   Verbatim copy, because these are the sentences a captain has read before. Four details that
   are upstream's and look like typos until you check:

   · Singular "Reason For Allocation Miss" but plural "Reasons For Capacity Loss".
   · `(-545 Orders)` when the count is positive, but `(0 Orders)` at zero — never `(-0 Orders)`.
   · "Pendency(DOH)" with no space, in the capacity Well-Done line.
   · A trailing space inside "Good " / "Not Good ".

   BOTH LOSS SECTIONS RENDER ALWAYS, treating an absent one as `{count: 0, metrics: []}`. LZ5
   and LZI carry no `capacity_loss` key at all, and the `(0 Orders)` rule only means anything if
   the section is drawn at zero — which is also what surfaces the verbatim "Well Done! your Day 0
   Attempt & Pendency(DOH) are good and on track." box on a hub whose day-0 and pendency really
   are fine.

   Only metrics with `status !== 'good'` are listed. That is upstream's design, not an omission:
   the panel answers "why did I miss orders", so a passing metric is not part of the answer. ── */
function Badge({ good }) {
  return (
    <span style={{ fontSize: 9.5, fontWeight: 700, padding: "2px 6px", borderRadius: 4,
      background: "var(--surface-3)", whiteSpace: "nowrap",
      color: good ? "var(--good)" : "var(--bad)" }}>
      {good ? RP.GOOD.trim() : RP.NOT_GOOD.trim()}
    </span>
  );
}

function WellDone({ message }) {
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "flex-start", padding: "11px 13px",
      background: "var(--surface-2)", border: "1px solid rgba(78,222,163,0.28)",
      borderRadius: 8 }}>
      <ThumbsUp size={14} style={{ color: "var(--good)", flex: "none", marginTop: 1 }} />
      <span style={{ fontSize: 11.5, color: "var(--good)", lineHeight: 1.5 }}>{message}</span>
    </div>
  );
}

function PanelSection({ label, count, metrics, dotColour, wellDone, extra, children }) {
  const bad = (metrics || []).filter((m) => m.status !== "good");
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0 8px" }}>
        {/* A 20x20 SQUARE with radius 2 — upstream's shape, and it matches the waterfall bar
            fill it refers to, so dot and bar carry the same identity. */}
        <span style={{ width: 16, height: 16, borderRadius: 2, background: dotColour,
          flex: "none" }} />
        <span style={{ fontSize: 11.5, fontWeight: 600, color: "var(--text-mute)" }}>
          {label} {extra ? RP.extraCountLabel(count) : RP.countLabel(count)}
        </span>
      </div>
      {bad.length === 0 && wellDone ? <WellDone message={wellDone} /> : (
        <div style={{ border: "1px solid var(--line)", borderRadius: 8, overflow: "hidden" }}>
          {bad.map((m, i) => (
            <div key={i} style={{ display: "flex", gap: 10, alignItems: "flex-start",
              padding: "10px 12px", borderBottom: i < bad.length - 1
                ? "1px solid var(--line-soft)" : "none" }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                {/* CSS capitalize, NOT a JS title-caser — that would turn "RTO Performance:"
                    into "Rto Performance:". The trailing colon is already in the payload. */}
                <div style={{ fontSize: 11.5, color: "var(--text-mute)",
                  textTransform: "capitalize" }}>{m.label}</div>
                {m.comparison && (
                  <div style={{ fontSize: 10.5, color: "var(--bad)", marginTop: 2 }}>
                    {m.comparison}</div>
                )}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, flex: "none" }}>
                <span style={{ fontSize: 12.5, fontWeight: 700,
                  fontVariantNumeric: "tabular-nums" }}>{m.value}</span>
                <Badge good={m.status === "good"} />
              </div>
            </div>
          ))}
          {children}
        </div>
      )}
    </div>
  );
}

function RightPanel({ rp, totalMissed, onAsk }) {
  const alloc = rp.allocation_miss || { count: 0, metrics: [] };
  const cap = rp.capacity_loss || { count: 0, metrics: [] };
  return (
    <div style={{ width: "35%", minWidth: 0, maxHeight: 500, display: "flex",
      flexDirection: "column", borderLeft: "1px solid var(--line)", paddingLeft: 20 }}>
      <div className="custom-scrollbar" style={{ flex: 1, minHeight: 0, overflowY: "auto",
        display: "flex", flexDirection: "column", gap: 14, paddingRight: 4 }}>
        {/* Sticky must be a DIRECT child of the scrolling element — any wrapper with its own
            overflow or transform breaks it. */}
        <div className="gd-sticky-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 12.5, fontWeight: 700, color: "var(--text)" }}>
            {totalMissed === 0 ? RP.PERFECT_TITLE : RP.missedTitle(n0(totalMissed))}
          </span>
          {totalMissed === 0
            ? <PartyPopper size={14} style={{ color: "var(--good)", flex: "none" }} />
            : <ArrowDown size={14} style={{ color: "var(--bad)", flex: "none" }} />}
        </div>

        <PanelSection label={RP.ALLOCATION_MISS} count={alloc.count} metrics={alloc.metrics}
          dotColour="var(--c-rose)" wellDone={RP.WELL_DONE_ALLOCATION} />

        <PanelSection label={RP.CAPACITY_LOSS} count={cap.count} metrics={cap.metrics}
          dotColour="var(--c-rose)" wellDone={RP.WELL_DONE_CAPACITY}>
          {cap.low_capacity_message && (
            <div style={{ padding: "10px 12px", borderTop: "1px solid var(--line-soft)" }}>
              <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 7 }}>
                {cap.low_capacity_message}
              </div>
              <button className="chip" onClick={() => onAsk?.("capacity")}>
                {RP.SET_DC_CAPACITY}
              </button>
            </div>
          )}
        </PanelSection>

        {rp.extra_orders && (
          <PanelSection label={RP.EXTRA} count={rp.extra_orders.count} metrics={[]} extra
            dotColour="var(--c-amber)">
            <div style={{ padding: "10px 12px", fontSize: 11.5, color: "var(--text-mute)",
              lineHeight: 1.5 }}>{rp.extra_orders.message}</div>
          </PanelSection>
        )}
      </div>
    </div>
  );
}

/* ── The docked widget. Deliberately minimal — it posts to the SAME /api/chat the Captain
   Advocate uses and streams the same trace, but shows only what a captain would see plus the
   two things that make the point on stage: which named query ran, and what the engine decided.
   Not a second chat implementation; a second VIEW of the one that exists. ── */
function SupportWidget({ hub, partnerId, askRef }) {
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [steps, setSteps] = useState([]);
  const convRef = useRef(null);
  const endRef = useRef(null);
  const abortRef = useRef(null);
  // Monotonic turn id. An in-flight stream from a previous hub can still deliver events after
  // abort() resolves, and its `reply` would land in a conversation that has been cleared — so
  // every callback checks it is still the current turn before touching state.
  const turnRef = useRef(0);

  // Cancel any in-flight stream when the captain/hub changes OR the component unmounts.
  // Without this the read loop kept running against a cleared conversation, and `busy` never
  // reset, so the input stayed disabled until the orphaned turn happened to finish.
  useEffect(() => {
    return () => { abortRef.current?.abort(); };
  }, []);
  useEffect(() => {
    abortRef.current?.abort();
    turnRef.current += 1;
    setMsgs([]); setSteps([]); setBusy(false); convRef.current = null;
  }, [hub, partnerId]);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, steps]);
  // Publish `send` upward so the dashboard's own CTAs can drive this widget. Kept as a ref
  // assignment rather than lifted state: the widget owns its conversation id, its AbortController
  // and its turn guard, and hoisting those into the page would mean re-plumbing three things
  // that already work correctly.
  useEffect(() => { if (askRef) askRef.current = send; });

  async function send(text) {
    const msg = (text ?? input).trim();
    if (!msg || busy || !partnerId) return;
    setInput(""); setMsgs((m) => [...m, { who: "captain", text: msg }]);
    setSteps([]); setBusy(true);
    if (!convRef.current) convRef.current = crypto.randomUUID();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const myTurn = turnRef.current;
    const mine = () => turnRef.current === myTurn && !ctrl.signal.aborted;
    // try/finally, because setBusy(false) used to live ONLY in onEnd — so any throw from
    // stream() (network drop, null body, reader reset) left the widget permanently disabled.
    try {
      await stream(
      { url: "/api/chat", method: "POST", signal: ctrl.signal,
        body: { captain_id: partnerId, message: msg, conversation_id: convRef.current } },
      (ev) => {
        if (!mine()) return;
        const d = ev.data || {};
        if (ev.node === "query" && d.query) setSteps((s) => [...s, { kind: "query", text: `named query: ${d.query}` }]);
        if (ev.node === "policy") setSteps((s) => [...s, { kind: "policy", text: `policy → ${d.action} · confidence ${d.confidence}` }]);
        if (ev.node === "gate") setSteps((s) => [...s, { kind: "gate", text: `trust gate ${d.passed ? "PASS" : "BLOCK"}${d.money_moving ? " · money-moving" : " · no money at stake"}` }]);
        if (ev.node === "act") setSteps((s) => [...s, { kind: d.simulated ? "warn" : "ok", text: ev.label }]);
        if (ev.node === "cost" && d.cost_usd != null) setSteps((s) => [...s, { kind: "cost", text: `turn cost $${Number(d.cost_usd).toFixed(4)} · ${d.calls} call(s)` }]);
        if (ev.node === "reply" && d.reply) {
          setMsgs((m) => [...m, { who: "psp", text: d.reply, failed: !!d.engine_error }]);
        }
      },
      () => { if (mine()) setBusy(false); });
    } catch {
      if (mine()) setMsgs((m) => [...m, { who: "psp", failed: true,
        text: "The connection dropped mid-answer. Please ask again." }]);
    } finally {
      if (mine()) setBusy(false);
    }
  }

  const TONE = { query: "var(--c-teal)", policy: "var(--c-violet)", gate: "var(--c-amber)",
                 ok: "var(--c-teal)", warn: "var(--warn)", cost: "var(--text-faint)" };

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div className="card-head">
        <h3><Sparkles size={14} />Partner Support</h3>
        <span className="mono" style={{ fontSize: 9.5, color: "var(--text-faint)" }}>
          {partnerId ? `hub ${hub}` : "no partner mapped"}
        </span>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: "12px 14px", display: "flex",
        flexDirection: "column", gap: 9, minHeight: 190 }}>
        {msgs.length === 0 && (
          <div className="df-note" style={{ border: "none", padding: 0 }}>
            The dashboard shows the numbers. Ask what they mean.
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
              {["why is my load low", "why did my capacity get cut", "how do I get more orders"].map((q) => (
                <button key={q} className="chip" onClick={() => send(q)} disabled={busy || !partnerId}>{q}</button>
              ))}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} style={{ alignSelf: m.who === "captain" ? "flex-end" : "flex-start",
            maxWidth: "88%", padding: "8px 11px", borderRadius: 10, fontSize: 12.5, lineHeight: 1.55,
            background: m.who === "captain" ? "var(--surface-3)"
              : m.failed ? "var(--warn-soft)" : "var(--surface-2)",
            border: `1px solid ${m.failed ? "rgba(255,185,95,.35)" : "var(--line-soft)"}`,
            color: "var(--text)" }}>
            {m.text}
          </div>
        ))}
        {steps.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 3, marginTop: 2 }}>
            {steps.map((s, i) => (
              <div key={i} className="mono" style={{ fontSize: 9.5, color: TONE[s.kind] || "var(--text-faint)" }}>
                ▸ {s.text}
              </div>
            ))}
          </div>
        )}
        {busy && <div className="mono" style={{ fontSize: 10, color: "var(--text-faint)" }}>thinking…</div>}
        <div ref={endRef} />
      </div>

      <div style={{ display: "flex", gap: 7, padding: "10px 12px", borderTop: "1px solid var(--line-soft)" }}>
        <input value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") send(); }}
          placeholder={partnerId ? "Ask about your load…" : "no partner mapped to this hub"}
          disabled={busy || !partnerId}
          style={{ flex: 1, background: "var(--surface-0)", border: "1px solid var(--line)",
            borderRadius: 8, padding: "8px 10px", color: "var(--text)", fontSize: 12.5,
            fontFamily: "inherit", outline: "none" }} />
        <button className="icon-btn" onClick={() => send()} disabled={busy || !partnerId}
          title="Send" style={{ width: 34, height: 34 }}><Send size={14} /></button>
      </div>
    </div>
  );
}

export default function GrowthDashboard() {
  const [index, setIndex] = useState(null);
  const [hub, setHub] = useState("");
  const [data, setData] = useState(null);
  // THREE error slots, not one.
  //
  // This was a single sticky `err` string, and `if (err) return <banner/>` blanked the ENTIRE
  // page. That is survivable while one fetch exists; it stops being survivable the moment a
  // second, independent fetch is added, because a failure in the newer one would blank the
  // dashboard that was working. Upstream makes the same split for the same reason — its two
  // sections have independent loading/error/data triads, so one can show data while the other
  // shows Retry.
  //
  //   err       — the INDEX failed. Nothing can be drawn, so this one may blank the page.
  //   growthErr — this hub's payload failed. Scoped to the panels that read it.
  const [err, setErr] = useState("");
  const [growthErr, setGrowthErr] = useState("");
  // Set by SupportWidget so the dashboard's CTAs can drive it. A ref rather than lifted state:
  // the widget owns its own conversation and stream lifecycle, and hoisting that into the page
  // would mean re-plumbing the abort/turn guards that already work.
  const askRef = useRef(null);
  const [riskData, setRiskData] = useState(null);

  useEffect(() => {
    getGrowthIndex().then((r) => {
      setIndex(r);
      // Default to an allocation-miss hub if one is present — that is the demo's main path.
      setHub((r.hubs || []).includes("LZ5") ? "LZ5" : (r.hubs || [])[0] || "");
    }).catch(() => setErr("Could not read the growth index."));
  }, []);

  // A STALENESS GUARD, and it is not theoretical.
  //
  // This was `getGrowth(hub).then(setData)` with nothing tying the response to the request that
  // asked for it. Click LZ5 → HKS quickly and whichever response lands last wins, so the slower
  // LZ5 payload can overwrite HKS. With a second hub-keyed fetch alongside it the failure gets
  // worse than stale: LZ5's waterfall can render beside HKS's at-risk rows, and a viewer reads
  // that as ONE hub. Numbers from two different hubs on one screen, presented as a single hub,
  // is the kind of wrong that a demo cannot recover from.
  //
  // The ref is compared on arrival; a response for a hub that is no longer selected is dropped.
  const wantHub = useRef("");
  useEffect(() => {
    if (!hub) return;
    wantHub.current = hub;
    setData(null);
    setGrowthErr("");
    getGrowth(hub)
      .then((r) => { if (wantHub.current === hub) setData(r); })
      .catch(() => { if (wantHub.current === hub) setGrowthErr(`No growth data for hub ${hub}.`); });
  }, [hub]);

  if (err) {
    return <div className="df-note" style={{ color: "var(--warn)", background: "var(--warn-soft)",
      border: "1px solid rgba(255,185,95,.3)", padding: "13px 15px" }}>{err}</div>;
  }
  if (!index) {
    return <div className="mono" style={{ fontSize: 12, color: "var(--text-faint)", padding: 18 }}>
      Reading the growth dashboard…</div>;
  }
  if (growthErr) {
    return <div className="df-note" style={{ color: "var(--warn)", background: "var(--warn-soft)",
      border: "1px solid rgba(255,185,95,.3)", padding: "13px 15px" }}>{growthErr}</div>;
  }
  if (!data) {
    return <div className="mono" style={{ fontSize: 12, color: "var(--text-faint)", padding: 18 }}>
      Reading hub {hub}…</div>;
  }

  const ym = data.your_metrics || {};
  const os = data.order_summary || {};
  const rp = os.right_panel || {};
  const partnerId = (index.partners || {})[hub];
  const levers = LEVERS.map((l) => {
    const raw = ym[l.key] || {};
    return { ...l, current: raw.current, target: raw.target,
             status: statusOf(raw.current, raw.target, l.higherIsBetter), why: WHY[l.key] };
  });
  const bannerTone = { green: "var(--c-teal)", grey: "var(--text-faint)",
                       red: "var(--c-rose)", red_capacity_cut: "var(--c-rose)" }[os.banner_type]
                     || "var(--text-faint)";
  const totalMissed = (rp.allocation_miss?.count || 0) + (rp.capacity_loss?.count || 0);

  // THE CTAs NOW DO SOMETHING. `View Details ›` (×2) and `Set DC Capacity Here` are real
  // affordances upstream, and reproducing them as buttons that navigate nowhere reads as an
  // unfinished mock-up — worse than not having them. Each one asks the docked widget the
  // question it corresponds to, which is also the page's whole argument: the panel shows you a
  // number, the widget explains it.
  const ASK = {
    "pilot-rate-card": "mera rate card target se zyada kyun hai",
    pendency: "meri pendency kyun badh rahi hai",
    capacity: "why did my capacity get cut",
    risk: "mere kitne shipment loss mein gaye hain",
  };
  const ask = (key) => askRef.current?.(ASK[key] || "why is my load low");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 13 }}>
      {/* ── TWO SOURCES, TWO CHIPS ───────────────────────────────────────────────────────
          This was ONE full-width amber banner, and its width was the problem: it read as
          page-wide provenance, so a viewer discounted everything below it as fixture — including
          the at-risk panel, which is the only real data on the screen. Undersell is a form of
          dishonesty too.

          Chip 1 (amber) covers the order figures. Chip 2 (info) covers the at-risk rows. ── */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div className="df-note" style={{ display: "flex", alignItems: "center", gap: 9,
          flexWrap: "wrap", borderColor: "rgba(255,185,95,.3)", background: "var(--warn-soft)",
          color: "var(--warn)", padding: "8px 11px" }}>
          <TriangleAlert size={11} style={{ flex: "none" }} />
          <b>{data.source}</b>
          <span style={{ color: "var(--text-faint)" }}>
            — the ORDER figures are shaped to the captain panel's real contract and served from a
            file. Live is one env var ({data.mode === "fixture" ? "PSP_GROWTH_SOURCE=live" : data.mode}) away.
          </span>
          {Object.values(data.endpoints || {}).map((u) => (
            <span key={u} className="tag mono" style={{ fontSize: 9 }}>
              <ExternalLink size={8} style={{ marginRight: 3 }} />GET {u}
            </span>
          ))}
        </div>
        <div className="df-note" style={{ display: "flex", alignItems: "center", gap: 9,
          flexWrap: "wrap", borderColor: "rgba(173,198,255,.28)", background: "var(--info-soft)",
          color: "var(--info)", padding: "8px 11px" }}>
          <Database size={11} style={{ flex: "none" }} />
          <b>valmo.db</b>
          <span style={{ color: "var(--text-faint)" }}>
            — the AT-RISK shipments are real rows from the loss ledger, keyed on a real partner
            id. Different source, different chip. Every one of them already became a loss, so the
            panel is a hindsight replay and carries no success rate.
          </span>
        </div>
      </div>

      {/* Proactive, and before the captain has asked anything. Returns null when there are no
          rows, while loading, on error, and when `as_of` is missing — a severity with no clock
          behind it is the overclaim this whole panel is built to avoid. */}
      <SeverityBanner data={riskData}
        onOpen={() => document.getElementById("at-risk")
          ?.scrollIntoView({ behavior: "smooth", block: "center" })} />

      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 16, display: "flex", alignItems: "center", gap: 7 }}>
          <BarChart3 size={16} />{S.PAGE_TITLE}
        </h2>
        <div style={{ display: "flex", gap: 5 }}>
          {(index.hubs || []).map((h) => (
            <button key={h} className="chip" onClick={() => setHub(h)}
              style={h === hub ? { borderColor: "var(--c-teal)", color: "var(--c-teal)" } : undefined}>
              {h}
            </button>
          ))}
        </div>
        <span className="mono" style={{ fontSize: 10, color: "var(--text-faint)", marginLeft: "auto" }}>
          {S.GRAPH_DATE} {os.start_date} – {os.end_date}
        </span>
      </div>

      {/* ── Metrics + the docked widget, side by side ────────────────────────────────────
          Side by side is the whole point: one payload, two presentations, both on screen at
          once. The ORDER-SUMMARY section moved out of this grid to full width below — see the
          note there; it is an arithmetic constraint, not a preference. */}
      {/* `gd-dock` carries the breakpoint. Below 1180px the widget would be crushed to its
          320px floor beside a metrics card, so the two stack — matching `.split`'s existing
          breakpoint in styles.css so the whole app collapses at one width rather than two. */}
      <div className="gd-dock" style={{ display: "grid", gap: 13,
        gridTemplateColumns: "minmax(0, 1.55fr) minmax(320px, 1fr)", alignItems: "start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 13, minWidth: 0 }}>
          <div className="card">
            <div className="card-head"><h3>{S.YOUR_METRICS}</h3></div>
            <div style={{ padding: "13px 15px", display: "flex", flexDirection: "column",
              gap: 13 }}>
              <MyOrdersBanner ym={ym} />
              {/* 2x2, NOT 4-across. At four abreast each card is ~160px in this column, and the
                  two-column body then gives ~70px per side — "Pilot Rate Card (CPS)" wraps to
                  three lines and "0.9 Days" cannot sit beside a "Not Good" pill. */}
              <div style={{ display: "grid", gap: 10,
                gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}>
                {levers.map((l) => <MetricCard key={l.key} {...l} onAsk={ask} />)}
              </div>
            </div>
          </div>
        </div>

        {/* The sticky column needs a bounded height and its own scroll. Without them, once the
            at-risk panel is added the column runs to ~800px and at a 900px viewport its bottom
            is permanently off-screen WHILE STUCK — unreachable by scrolling, because the thing
            that would scroll it is pinned. */}
        <div style={{ position: "sticky", top: 8, display: "flex", flexDirection: "column",
          gap: 13, maxHeight: "calc(100vh - 96px)", overflowY: "auto", paddingRight: 2 }}
          className="custom-scrollbar">
          <SupportWidget hub={hub} partnerId={partnerId} askRef={askRef} />
          <AtRiskPanel hub={hub} onAsk={ask} onData={setRiskData} />
          <div className="df-note">
            The widget answers from the <b style={{ color: "var(--text-mute)" }}>same payload</b>
            {" "}this page is drawing — one <span className="mono">load_status</span> named query,
            no SQL, and the decision runs through the trust gate. Nothing here moves money.
          </div>
        </div>
      </div>

      {/* ── Your Order Summary, at FULL WIDTH ─────────────────────────────────────────────
          Moved out of the docked grid, and the reason is arithmetic rather than taste. At a
          1440px viewport the content area is 1132px; the 1.55fr column of the grid above is
          ~680px, less the card's padding ~650. A 65/35 split of 650 puts the right sub-panel at
          ~220px — "Reasons For Capacity Loss (-188 Orders)" does not fit on one line there, and
          five 64px bars plus a two-line "Final Manifested Orders" label are cramped in the 421px
          that remains.

          At full width the same split gives 723px for the chart and 373px for the panel, which
          is what upstream has on upstream's page. The support widget is still on screen beside
          "Your Metrics", so the demo beat — same payload, two presentations, both visible — is
          intact. ── */}
      <div className="card">
        <div className="card-head">
          <h3>{S.YOUR_ORDER_SUMMARY}</h3>
          <span className="mono" style={{ fontSize: 9.5, color: "var(--text-faint)" }}>
            {S.ORDER_SUMMARY_SUBTITLE}
          </span>
        </div>
        <div className="gd-summary" style={{ padding: "15px 17px", display: "flex", gap: 20,
          alignItems: "flex-start" }}>
          {/* minWidth 0 on BOTH panels is mandatory. 65% + 35% + a 20px gap exceeds 100%, so
              they must be allowed to shrink; without it a long unbroken string or the 5-bar row
              pins the section at content width and `.content` (overflow: auto) gains a
              horizontal scrollbar for the entire app. */}
          <div style={{ width: "65%", minWidth: 0, display: "flex", flexDirection: "column",
            gap: 12 }}>
            {/* `financial_summary` verbatim, and NOTHING appended. This used to gain
                "₹X forgone" from `extra_earnings_loss` — a field that is in the contract but is
                never rendered anywhere upstream, so the sentence a captain would recognise was
                being extended with one they would not, in the panel's own voice. A display
                string is passed through or omitted; it is not edited. */}
            {os.financial_summary && (
              <div style={{ display: "flex", gap: 9, alignItems: "flex-start",
                padding: "11px 14px", borderRadius: 8, background: "var(--surface-2)",
                border: `1px solid ${bannerTone}` }}>
                {os.banner_type === "red_capacity_cut"
                  ? <Scissors size={14} style={{ color: bannerTone, flex: "none", marginTop: 2 }} />
                  : <Star size={14} style={{ color: bannerTone, flex: "none", marginTop: 2 }} />}
                <span style={{ fontSize: 12, color: bannerTone, lineHeight: 1.5 }}>
                  {os.financial_summary}
                </span>
              </div>
            )}
            <Waterfall os={os} />
            <div className="df-note" style={{ padding: "9px 0 0" }}>
              {S.GRAPH_DATE} {os.start_date} – {os.end_date}
            </div>
          </div>
          <RightPanel rp={rp} totalMissed={totalMissed} onAsk={ask} />
        </div>
      </div>
    </div>
  );
}
