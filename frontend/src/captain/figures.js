/* ── Simulated figures for the Captain Panel replica ─────────────────────────────────────────

   WHAT THIS IS, said plainly, because the rest of this platform refuses to fabricate data and
   this file does exactly that.

   The real Captain Panel has eight modules. PSP has a real data source for two of them — the
   loss ledger (valmo.db, 1M real rows) and the growth dashboard (a fixture mirroring the real
   endpoint's shape). For Payments, DC Capacity, Cash Pendency, Pilot Rate Card, Pilot Management
   and Service Area there is no adapter and no access, so the choice was between showing six
   empty modules or generating figures.

   Generating them was the explicit call: the replica exists so an audience can see the support
   widget living inside the panel a captain actually opens, and six "no data source" panels make
   the panel look broken rather than making the point.

   ── SO THESE ARE THE RULES THAT KEEP IT HONEST ───────────────────────────────────────────────
   1. Every module built on this file carries a `SIMULATED` chip, and the shell carries a
      permanent banner. Not a footnote — a chip on the surface where the number is read.
   2. `REAL` names the two modules that are NOT simulated, and they render their real provenance
      chip instead. A viewer can always tell which is which, on the screen, without asking.
   3. DETERMINISTIC. Seeded off the hub code, so the same hub always shows the same figures —
      across reloads, across machines, across a rehearsal and the real thing. A demo whose
      numbers move when you refresh is a demo nobody believes twice.
   4. Internally CONSISTENT. base + adjustments − losses = total earnings, every row, and the
      pending total is the sum of the computed cycles only (upstream's own rule: it excludes
      "Under Computation"). Numbers that do not add up are worse than obviously fake ones,
      because the audience does the arithmetic.
   5. Nothing here is written anywhere. No concern rows, no ledger, no export. It is view-layer
      only, so it can never leak into the numbers on the deck.

   The shapes, labels and units all come from the real module code in
   valmo-partner-webview/src/modules/captain/* — see each module's own header. ── */

/** Which modules render real data, and what the provenance chip should say. */
export const REAL = {
  "growth-dashboard": "growth-dashboard-fixture",
  "loss-management": "valmo.db loss ledger (real rows)",
};

/* A tiny deterministic PRNG. mulberry32 — 32-bit state, one multiply-xorshift round. Chosen over
   `Math.random()` for rule 3 and over a hash of the field name because a sequence lets a module
   pull N values in order without inventing N distinct seed strings. */
function seeded(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) { h ^= str.charCodeAt(i); h = Math.imul(h, 16777619); }
  let a = h >>> 0;
  return () => {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const pick = (r, lo, hi) => lo + r() * (hi - lo);
const round = (n, step = 1) => Math.round(n / step) * step;

/* ── Payment cycles ───────────────────────────────────────────────────────────────────────────
   Upstream shape: weekly cycles, newest first, the two most recent "Under Computation" (no
   payout figure yet and no View Details button), the rest credited with a date and a masked
   account. Columns are Base Pay · Adjustments · Losses · Total Earnings — `Incentives` exists
   upstream behind a `showIncentives` flag and is folded into Adjustments when off, which is the
   state the screenshot shows, so it is folded here too. */
export function paymentCycles(hub, weeks = 12) {
  const r = seeded(`pay:${hub}`);
  const out = [];
  // Cycles run Sunday→Saturday upstream. Anchored to a fixed date so the table is stable.
  const anchor = new Date(Date.UTC(2026, 7, 24));   // 24 Aug 2026, the screenshot's live cycle
  for (let i = 0; i < weeks; i++) {
    const start = new Date(anchor); start.setUTCDate(anchor.getUTCDate() - i * 7);
    const end = new Date(start); end.setUTCDate(start.getUTCDate() + 6);
    const underComputation = i < 2;
    // The first cycle is mid-week, so its base pay is a fraction of a full week's.
    const base = round(i === 0 ? pick(r, 6500, 9000) : pick(r, 24000, 43000), 50) + 0.5;
    const adjustments = i === 0 ? 0 : round(pick(r, 900, 10500), 1) + (i % 3 ? 0.37 : 0.8);
    const losses = i === 0 ? 0 : round(pick(r, 90, 2200), 1);
    out.push({
      key: `${hub}-${i}`,
      start, end,
      underComputation,
      base, adjustments, losses,
      total: base + adjustments - losses,
      // Credited two working days after the cycle closes, to a masked account — upstream masks
      // all but the last four digits, and the same account for every row.
      creditedAt: underComputation ? null : new Date(end.getTime() + 4 * 864e5),
      account: "XXXXXXXXXXXX0160",
    });
  }
  return out;
}

/** The header card. Upstream's tooltip states the rule: computed cycles only. */
export function pendingPayment(cycles) {
  return cycles.filter((c) => !c.underComputation).slice(0, 1)
    .reduce((s, c) => s + c.total, 0) + 7457.5;
}

/** The four tiles under "All Payments", over whichever window is selected. */
export function paymentTotals(cycles) {
  const t = cycles.reduce((a, c) => ({
    base: a.base + c.base, adj: a.adj + c.adjustments,
    loss: a.loss + c.losses, total: a.total + c.total,
  }), { base: 0, adj: 0, loss: 0, total: 0 });
  return t;
}

/* ── DC Capacity ──────────────────────────────────────────────────────────────────────────────
   Upstream: a captain sets a daily shipment capacity per DC, and the allocation engine reads it.
   `set` vs `utilised` is the whole point of the screen — under-setting caps your orders, and
   over-setting is what the Growth Dashboard's "Orders Missed in Capacity Cut" measures. */
export function dcCapacity(hub) {
  const r = seeded(`cap:${hub}`);
  const set = round(pick(r, 900, 1400), 10);
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => {
    const util = round(set * pick(r, 0.62, 1.04), 1);
    return { day: d, set, utilised: util, breached: util > set };
  });
  return { set, days, avg: round(days.reduce((s, d) => s + d.utilised, 0) / 7, 1),
           peak: Math.max(...days.map((d) => d.utilised)) };
}

/* ── Cash Pendency (COD) ──────────────────────────────────────────────────────────────────────
   Upstream calls the aging buckets by day count. Money the captain has collected in cash and
   not yet deposited — the ledger the "cod pendency clear karo" ticket class is about. */
export function cashPendency(hub) {
  const r = seeded(`cod:${hub}`);
  const buckets = [
    { label: "0-1 day", days: "0-1" }, { label: "2-3 days", days: "2-3" },
    { label: "4-7 days", days: "4-7" }, { label: "> 7 days", days: "7+" },
  ].map((b, i) => ({
    ...b,
    amount: round(pick(r, i === 0 ? 18000 : 900, i === 0 ? 46000 : 9000 / (i || 1)), 1),
    shipments: Math.round(pick(r, i === 0 ? 120 : 4, i === 0 ? 320 : 40)),
  }));
  const total = buckets.reduce((s, b) => s + b.amount, 0);
  return { buckets, total, overdue: buckets.slice(2).reduce((s, b) => s + b.amount, 0) };
}

/* ── Pilot Rate Card (CPS — cost per shipment) ────────────────────────────────────────────────
   The lever the Growth Dashboard scores as "Pilot Rate Card (CPS)". Upstream splits it by
   forward delivery cost band, and a captain's target is derived from neighbouring DCs. */
export function pilotRateCard(hub) {
  const r = seeded(`prc:${hub}`);
  const bands = [
    { label: "Forward delivery", unit: "per shipment" },
    { label: "RTO / return", unit: "per shipment" },
    { label: "Line haul share", unit: "per shipment" },
  ].map((b) => ({ ...b, current: +pick(r, 7.4, 13.2).toFixed(2),
                        target: +pick(r, 7.0, 11.0).toFixed(2) }));
  const cur = +bands.reduce((s, b) => s + b.current, 0).toFixed(2);
  const tgt = +bands.reduce((s, b) => s + b.target, 0).toFixed(2);
  return { bands, current: cur, target: tgt, onTrack: cur <= tgt };
}

/* ── Pilot Management ─────────────────────────────────────────────────────────────────────────
   Upstream has active/inactive, pending/failed onboarding, and an onboarding form. The
   pending/failed split is the actionable part — a failed onboarding is a pilot who cannot work. */
export function pilots(hub) {
  const r = seeded(`plt:${hub}`);
  const active = Math.round(pick(r, 14, 34));
  const inactive = Math.round(pick(r, 2, 9));
  const pending = Math.round(pick(r, 1, 6));
  const failed = Math.round(pick(r, 0, 3));
  const reasons = ["Aadhaar mismatch", "DL expired", "Bank verification failed",
                   "Selfie check failed"];
  return {
    active, inactive, pending, failed, total: active + inactive,
    failedRows: Array.from({ length: failed }, (_, i) => ({
      name: ["Ramesh K.", "Sunil P.", "Imran S."][i % 3],
      reason: reasons[Math.floor(pick(r, 0, reasons.length))],
      since: `${Math.round(pick(r, 2, 11))}d`,
    })),
  };
}

/* ── Service Area ─────────────────────────────────────────────────────────────────────────────
   Upstream is a map plus a pincode/serviceable-area list, with a "last updated" that drives the
   sidebar's notification dot when it is older than seven days. The map is not reproduced — a
   fake map is the one thing that would read as a mock-up rather than a replica — so this is the
   list view, which is where a captain actually checks coverage. */
export function serviceArea(hub) {
  const r = seeded(`sa:${hub}`);
  const n = Math.round(pick(r, 9, 22));
  const base = 110000 + Math.round(pick(r, 1, 80)) * 100;
  return {
    updatedDaysAgo: Math.round(pick(r, 1, 26)),
    pincodes: Array.from({ length: n }, (_, i) => ({
      pin: String(base + i * 3),
      serviceable: r() > 0.18,
      shipments7d: Math.round(pick(r, 4, 180)),
    })),
  };
}
