import { createContext, createElement, useContext, useEffect, useState } from "react";

/* ── The audience toggle ───────────────────────────────────────────────────────────────────────

   A captain should NOT see the engine's internals. They want an answer, or a reference number and
   the name of the team now holding their case. The checks, the confidence dial and the gate verdict
   are for an audience being shown how the thing works.

   Both are true at once, so the honest move is a toggle that SAYS it is one — and the flip itself
   is the most convincing ten seconds available: same conversation, same two replies, nothing
   re-run, and the trace vanishes. "That is what he actually sees. Everything else was for you."

   MECHANISM, and why not the alternatives:
     · A build-time env var (VITE_*) cannot be flipped live, which loses exactly that beat.
     · "Always on but visually subordinate" fails the requirement outright — if the trace is always
       there, nobody can tell which pixels a captain sees.
     · A UI-only toggle means remembering to click it before the room is looking.
   So: `?demo=1` for a bookmark, persisted to localStorage, flippable live. The localStorage key
   follows the `valmo.voiceLang` precedent already in CaptainPanel.

   DEFAULT OFF. The persona IS the captain, so the app opens on the real user's screen and the
   deviation is what has to announce itself.

   SCOPED TO ONE VIEW. It is not a global demo mode: an operator reading a trace on Support
   Command or the test bench is doing their job, and a global pill saying "a captain does not see
   this" would be wrong on both — it would tell the room that the persona which OWNS traces is a
   demo artefact. Only the captain view reads this. ── */

const KEY = "valmo.audience";
const Ctx = createContext([false, () => {}]);

export function AudienceProvider({ children }) {
  const [on, setOn] = useState(() => {
    try {
      const q = new URLSearchParams(window.location.search).get("demo");
      if (q != null) return q !== "0" && q !== "false";
      return localStorage.getItem(KEY) === "1";
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try { localStorage.setItem(KEY, on ? "1" : "0"); } catch { /* private mode — ignore */ }
  }, [on]);
  return createElement(Ctx.Provider, { value: [on, setOn] }, children);
}

/** `[showingEngine, setShowingEngine]`. Read only by the captain view. */
export const useAudience = () => useContext(Ctx);
