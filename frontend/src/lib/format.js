/* ── Number formatting, in one place ──────────────────────────────────────────────────────
   `n0` and `inr` were copy-pasted between DataFoundation.jsx and GrowthDashboard.jsx. Two
   copies of a locale formatter is two places for "en-IN" to drift into "en-US", which silently
   changes ₹11,567 into ₹11,567.00 or 11567 depending on which one drifts.

   `num` is the parser, and its `null` return is load-bearing: the growth panel's own
   `parseNumeric` returns 0 on an unparseable string, which for a LOWER-is-better metric reads
   as passing. Returning null lets the caller treat unreadable as UNKNOWN, matching the
   backend's Tri discipline (see backend/app/substrate/adapters/growth/contract.py). ── */

export const n0 = (v) => Number(v || 0).toLocaleString("en-IN");
export const inr = (v) => "₹" + Number(v || 0).toLocaleString("en-IN");

/** First number in a string, or null. Never 0-on-failure — see the note above. */
export const num = (v) => {
  if (typeof v === "number") return v;
  const m = String(v ?? "").replace(/,/g, "").match(/-?\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : null;
};
