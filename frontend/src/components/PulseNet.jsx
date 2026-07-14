import React, { useEffect, useRef } from "react";

// PulseNet — a living node-link "signal field": nodes drift, nearby nodes link,
// and bright pulses travel along the links. Reacts to hover + page scroll (speeds up).
// Pure canvas 2D, no deps. Cyan control-room palette.
export default function PulseNet({ height = 150, nodes = 34, color = "77,142,255" }) {
  const ref = useRef(null);
  const boost = useRef(1);
  const hover = useRef(false);

  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas.getContext("2d");
    let W = 0, H = height, raf;
    const dpr = window.devicePixelRatio || 1;
    const resize = () => {
      W = canvas.clientWidth || 600;
      canvas.width = W * dpr; canvas.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    ro?.observe(canvas);

    const N = Array.from({ length: nodes }, () => ({
      x: Math.random() * W, y: Math.random() * H,
      vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3,
    }));
    // pulses travel between node pairs
    const P = Array.from({ length: 10 }, () => ({
      a: (Math.random() * nodes) | 0, b: (Math.random() * nodes) | 0, t: Math.random(),
      sp: 0.004 + Math.random() * 0.006,
    }));
    const LINK = 120;

    const onScroll = () => { boost.current = Math.max(boost.current, 3); };
    window.addEventListener("wheel", onScroll, { passive: true });
    window.addEventListener("scroll", onScroll, { passive: true, capture: true });

    const draw = () => {
      raf = requestAnimationFrame(draw);
      const target = hover.current ? 2.6 : 1;
      boost.current += (target - boost.current) * 0.05;
      const m = boost.current;
      ctx.clearRect(0, 0, W, H);

      for (const n of N) {
        n.x += n.vx * m; n.y += n.vy * m;
        if (n.x < 0 || n.x > W) n.vx *= -1;
        if (n.y < 0 || n.y > H) n.vy *= -1;
      }
      // links
      for (let i = 0; i < N.length; i++) {
        for (let j = i + 1; j < N.length; j++) {
          const dx = N[i].x - N[j].x, dy = N[i].y - N[j].y;
          const d = Math.hypot(dx, dy);
          if (d < LINK) {
            ctx.strokeStyle = `rgba(${color},${(1 - d / LINK) * 0.18})`;
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(N[i].x, N[i].y); ctx.lineTo(N[j].x, N[j].y); ctx.stroke();
          }
        }
      }
      // nodes
      for (const n of N) {
        ctx.fillStyle = `rgba(${color},0.5)`;
        ctx.beginPath(); ctx.arc(n.x, n.y, 1.6, 0, 7); ctx.fill();
      }
      // pulses
      for (const p of P) {
        p.t += p.sp * m;
        if (p.t >= 1) { p.t = 0; p.a = (Math.random() * nodes) | 0; p.b = (Math.random() * nodes) | 0; }
        const A = N[p.a], B = N[p.b];
        if (!A || !B) continue;
        const x = A.x + (B.x - A.x) * p.t, y = A.y + (B.y - A.y) * p.t;
        const g = ctx.createRadialGradient(x, y, 0, x, y, 6);
        g.addColorStop(0, `rgba(${color},0.9)`); g.addColorStop(1, `rgba(${color},0)`);
        ctx.fillStyle = g; ctx.beginPath(); ctx.arc(x, y, 6, 0, 7); ctx.fill();
      }
    };
    draw();

    return () => {
      cancelAnimationFrame(raf); ro?.disconnect();
      window.removeEventListener("wheel", onScroll);
      window.removeEventListener("scroll", onScroll, { capture: true });
    };
  }, [height, nodes, color]);

  return <canvas ref={ref} style={{ width: "100%", height, display: "block" }}
    onMouseEnter={() => { hover.current = true; }} onMouseLeave={() => { hover.current = false; }} />;
}
