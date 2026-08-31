import { useEffect, useRef } from "react";

/* ── The live waveform ────────────────────────────────────────────────────────────────────────

   The one question a first-time, low-confidence user has when they hold a microphone is **did it
   hear me?** A spinner cannot answer that; it spins identically whether the mic is live or dead.
   Bars driven by the ACTUAL amplitude answer it without a word of explanation, in any language —
   which matters for an audience where 100% prefer Hindi or a regional language and the adoption
   pilot failed on access rather than willingness.

   Canvas rather than 24 animated DOM nodes: this runs at 60fps for as long as someone is talking,
   and `requestAnimationFrame` mutating a canvas costs one paint instead of two dozen style
   recalculations. It is also read straight from a ref, so the parent never re-renders while
   someone speaks — a `setState` per frame would re-render the whole chat thread sixty times a
   second.

   Falls back to a slow idle shimmer when there is no meter (the captain declined the mic
   permission, or `getUserMedia` is unavailable). A dead-flat line would read as broken, and the
   recogniser may still be working perfectly. ── */

export default function VoiceBars({ levelRef, active, bars = 24, height = 34, tone = "live" }) {
  const cv = useRef(null);
  const raf = useRef(0);
  const hist = useRef([]);

  useEffect(() => {
    const c = cv.current;
    if (!c) return;
    const ctx = c.getContext("2d");
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const resize = () => {
      const w = c.clientWidth || 200;
      c.width = w * dpr;
      c.height = height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    ro?.observe(c);

    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
    const colour = () => {
      const s = getComputedStyle(c);
      return s.getPropertyValue(tone === "live" ? "--live-bar" : "--accent").trim() || "#2563EB";
    };
    let t = 0;
    const draw = () => {
      const w = c.clientWidth || 200;
      ctx.clearRect(0, 0, w, height);
      const lvl = active ? (levelRef?.current ?? 0) : 0;
      // Push the newest sample; the array scrolls right-to-left so the shape reads as time.
      hist.current.push(lvl);
      if (hist.current.length > bars) hist.current.shift();
      const fill = colour();
      const bw = Math.max(2, (w - (bars - 1) * 3) / bars);
      for (let i = 0; i < bars; i++) {
        const v = hist.current[i] ?? 0;
        // An idle shimmer so a missing meter never looks like a dead mic — but only while active.
        const idle = active ? 0.06 + 0.05 * Math.sin(t / 9 + i / 2.2) : 0.03;
        const h = Math.max(2, (Math.max(v, idle)) * height);
        const x = i * (bw + 3);
        const y = (height - h) / 2;
        ctx.fillStyle = fill;
        ctx.globalAlpha = active ? 0.35 + 0.65 * Math.min(1, v * 2.2) : 0.22;
        // Rounded caps: a pill reads as sound, a rectangle reads as a chart.
        const r = Math.min(bw / 2, h / 2);
        ctx.beginPath();
        ctx.roundRect ? ctx.roundRect(x, y, bw, h, r) : ctx.rect(x, y, bw, h);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      t += 1;
      if (!reduced) raf.current = requestAnimationFrame(draw);
    };
    draw();
    return () => { cancelAnimationFrame(raf.current); ro?.disconnect(); };
  }, [levelRef, active, bars, height, tone]);

  return <canvas ref={cv} style={{ width: "100%", height, display: "block" }}
    aria-hidden="true" />;
}
