/* ── The captain panel's own vocabulary, mirrored ─────────────────────────────────────────
   UPSTREAM: valmo-partner-webview/src/modules/captain/growth-dashboard/constants.ts
             (GROWTH_DASHBOARD_STRINGS, METRIC_TARGET_TOOLTIPS)
   plus the literals hardcoded in that module's components, noted per entry.

   WHY THIS IS ITS OWN FILE, and not inline in the page: it is a MIRROR of an upstream file
   across a language boundary. Mixed into JSX, drift against upstream is invisible. As one
   exported block with the upstream path in this header, it is diffable — someone can open the
   real constants.ts beside it and read down.

   THERE IS A SECOND MIRROR, deliberately: backend/app/substrate/adapters/growth/contract.py
   holds LEVERS and LEVER_WHY for the composers. That is not a duplication to be removed —
   they are two independent mirrors of one upstream file, one per language. Serving these
   strings from /api/growth would make visible captain-facing copy depend on a PSP endpoint for
   text that upstream ships in its own bundle: it would HIDE drift rather than surface it, and
   add a failure mode where an endpoint hiccup silently changes rendered words. Two mirrors,
   both named, is the honest shape.

   A captain recognises the words. An approximation of the layout with invented labels would
   read as a mock-up of something else — which is why the strings are verbatim even where the
   layout is not. ── */

export const S = {
  PAGE_TITLE: "Growth Dashboard",
  YOUR_METRICS: "Your Metrics",
  YOUR_ORDERS_SUBTITLE: "Your Orders (Out of Max Potential):",
  // These four are the ENTIRE visible copy of the blue banner — the page's signature element.
  // They were missing, which is why that banner rendered as a bare `827 / 1035`.
  GETTING: "Getting",
  OUT_OF: "out of",
  TOTAL_ORDERS: "Total Orders",
  VIEW_DETAILS: "View Details",
  CURRENT: "Current",
  TARGET: "Target",
  YOUR_ORDER_SUMMARY: "Your Order Summary",
  ORDER_SUMMARY_SUBTITLE: "View the reasons for missing out extra orders",
  GRAPH_DATE: "Graph Date:",
  // The \n is load-bearing: upstream renders these in a label row with `white-space: pre-line`,
  // so each becomes two lines under its bar. Collapsing them to spaces widens every bar cell.
  MAXIMUM_POTENTIAL: "Maximum\nPotential",
  ORDERS_MISSED_ALLOCATION: "Orders Missed\nin Allocation",
  CURRENT_ELIGIBLE: "Current\nEligible",
  ORDERS_MISSED_CAPACITY: "Orders Missed in\nCapacity Cut",
  EXTRA_ORDERS: "Extra\nOrders",
  FINAL_MANIFESTED: "Final Manifested\nOrders",
};

/* ── Right-panel copy. Hardcoded in MissedOrdersPanel/index.tsx and CapacityLossSection.tsx
   upstream rather than living in constants.ts, so these are quoted from the components. ── */
export const RP = {
  // MissedOrdersPanel/index.tsx
  PERFECT_TITLE: "You Haven't Missed Any Order!",
  missedTitle: (n) => `Why You Have Missed ${n} Orders?`,
  // Note the asymmetry, which is upstream's and not a typo of ours: singular "Reason" for
  // allocation, plural "Reasons" for capacity.
  ALLOCATION_MISS: "Reason For Allocation Miss",
  CAPACITY_LOSS: "Reasons For Capacity Loss",
  EXTRA: "Extra Orders",
  // count 0 renders "(0 Orders)", NOT "(-0 Orders)" — upstream's own ternary.
  countLabel: (n) => `(${n > 0 ? `-${n}` : n} Orders)`,
  extraCountLabel: (n) => `(+${n} Orders)`,
  GOOD: "Good ",          // the trailing space is inside upstream's <span>
  NOT_GOOD: "Not Good ",
  WELL_DONE_ALLOCATION: "Well Done! your RTO and Pilot Rate Card metrics are good and on track.",
  // "Pendency(DOH)" with no space is upstream's, verbatim.
  WELL_DONE_CAPACITY: "Well Done! your Day 0 Attempt & Pendency(DOH) are good and on track.",
  SET_DC_CAPACITY: "Set DC Capacity Here",
};

/* ── The four levers, in the panel's own order, with its own titles.

   `higherIsBetter` matters: day0_attempt is the ONLY one where a bigger number is better, and
   inverting it would tell a captain at 94% against a 70% target that they are failing.

   `hasDetails` reproduces upstream exactly — a `View Details ›` footer appears on precisely two
   of the four cards, pilot-rate-card and pendency. Putting it on all four would look tidier and
   be wrong. ── */
export const LEVERS = [
  { key: "pilot_rate_card", id: "pilot-rate-card", title: "Pilot Rate Card (CPS)", higherIsBetter: false, hasDetails: true },
  { key: "rto_performance", id: "rto-performance", title: "RTO Performance", higherIsBetter: false, hasDetails: false },
  { key: "day0_attempt", id: "day0-attempt", title: "Day-0 Attempt %", higherIsBetter: true, hasDetails: false },
  { key: "pendency", id: "pendency", title: "Pendency (DOH)", higherIsBetter: false, hasDetails: true },
];

/* ── METRIC_TARGET_TOOLTIPS, verbatim. This is the authored explanation of WHY each target is
   what it is — upstream a captain has to find and hover a dotted underline to read it, which is
   precisely the gap the docked widget closes by saying it out loud. ── */
export const WHY = {
  pilot_rate_card: "The target rate card is calculated based on your neighbouring DCs rate",
  rto_performance: "The target RTO is computed based on the best 3PL in your area",
  day0_attempt: "The target is to increase your delivery attempt, keep your performance above 70% to avoid capacity cut",
  pendency: "Target is set to avoid shipment pendency, please clear pendencies within 2.5 days to avoid capacity cut",
};
