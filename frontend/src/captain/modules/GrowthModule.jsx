import GrowthDashboard from "../../pages/GrowthDashboard.jsx";

/* ── Growth Dashboard, inside the replica ─────────────────────────────────────────────────────

   The existing page, unchanged and unforked. It already mirrors the real module string-for-string
   (see growth/strings.js) and it is one of the two modules running on real data, so wrapping it
   is strictly better than reproducing it: a fork would drift against the upstream mirror the
   first time either side changed.

   It brings its own at-risk panel and monitoring dock, which belong to the captain's surface
   anyway. Its own docked widget stays too — harmless duplication of the shell's Valmo Support
   panel, and cheaper than threading a `hideWidget` prop through 600 lines to save one card. ── */
export default function GrowthModule() {
  return <div className="cp-embed"><GrowthDashboard /></div>;
}
