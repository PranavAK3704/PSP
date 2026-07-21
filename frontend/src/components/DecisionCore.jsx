import React, { useEffect, useRef } from "react";

// The "Decision Core" — glowing icosahedron + wireframe shell (Three.js).
// Stateful + interactive:
//   state: "idle" (calm cyan) | "thinking" (fast, bright, agitated) | "resolved" (green, steady)
//   reacts to hover and to page scroll (spins up briefly).
//
// IMPORTANT: the WebGL context is created ONCE (per mount/size) — state changes only mutate the
// existing materials. Recreating the renderer on every state flip leaked WebGL contexts (browsers
// cap ~16); once exhausted, `new WebGLRenderer` throws inside the effect and, with no error
// boundary, unmounts the whole app to a black screen. Context creation + render are also guarded
// so GPU/context exhaustion degrades silently instead of crashing.
const CFG = {
  idle:      { color: 0x4d8eff, emissive: 0.5, base: 1.0 },
  thinking:  { color: 0x4d8eff, emissive: 1.0, base: 2.8 },
  resolved:  { color: 0x4edea3, emissive: 0.8, base: 1.4 },
  listening: { color: 0x4d8eff, emissive: 0.7, base: 1.15, reactive: true },  // deforms to live mic amplitude
  speaking:  { color: 0x4edea3, emissive: 1.0, base: 1.6, pulse: true },       // rhythmic bloom while it talks
};

// levelRef (optional): a ref whose .current is a 0..1 signal. Pass one and the orb deforms /
// spins up / brightens with it — the reactive field. The signal is generic: mic amplitude in
// voice mode, live event cadence on the Monitor, resolution energy anywhere else. No levelRef
// → the orb keeps its calm state-driven motion (unchanged for every non-reactive caller).
export default function DecisionCore({ size = 220, state = "idle", levelRef = null }) {
  const ref = useRef(null);
  const boost = useRef(1);
  const hover = useRef(false);
  const cfgRef = useRef(CFG[state] || CFG.idle);
  const matsRef = useRef(null);   // { coreMat, shellMat, light } — mutated on state change

  // State change → recolor the live materials in place. No new context.
  useEffect(() => {
    const cfg = CFG[state] || CFG.idle;
    cfgRef.current = cfg;
    const m = matsRef.current;
    if (m) {
      m.coreMat.color.setHex(cfg.color);
      m.coreMat.emissive.setHex(cfg.color);
      m.coreMat.emissiveIntensity = cfg.emissive;
      m.shellMat.color.setHex(cfg.color);
      m.light.color.setHex(cfg.color);
    }
  }, [state]);

  // Create the renderer/scene ONCE per size. Cleanup force-releases the context.
  useEffect(() => {
    const THREE = window.THREE;
    const container = ref.current;
    if (!THREE || !container) return;

    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    } catch (_) {
      return;   // GPU/context unavailable — degrade to nothing rather than crash the app
    }

    // Everything below runs AFTER a context is already allocated. If any of it throws
    // (appendChild, shader compile, geometry alloc…) we must release that context — otherwise
    // it leaks against the browser's ~16-context cap, the very thing this component guards.
    // `cleanup` is declared out here so both the success path and the failure path use it, and
    // it tolerates partial setup (listeners/raf may not be wired yet).
    const canvas = renderer.domElement;
    let raf = 0;
    let lost = false;
    let onLost, onRestored, onScroll;
    const cleanup = () => {
      cancelAnimationFrame(raf);
      if (onLost) canvas.removeEventListener("webglcontextlost", onLost);
      if (onRestored) canvas.removeEventListener("webglcontextrestored", onRestored);
      if (onScroll) {
        window.removeEventListener("wheel", onScroll);
        window.removeEventListener("scroll", onScroll, { capture: true });
      }
      matsRef.current = null;
      try { renderer.forceContextLoss?.(); } catch (_) { /* noop */ }
      try { renderer.dispose?.(); } catch (_) { /* noop */ }
      if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    };

    try {
      const cfg = cfgRef.current;
      renderer.setSize(size, size);
      renderer.setPixelRatio(window.devicePixelRatio || 1);
      container.appendChild(canvas);

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(75, 1, 0.1, 1000);

      const coreMat = new THREE.MeshPhongMaterial({ color: cfg.color, emissive: cfg.color,
        emissiveIntensity: cfg.emissive, transparent: true, opacity: 0.85, flatShading: true });
      const core = new THREE.Mesh(new THREE.IcosahedronGeometry(1.5, 0), coreMat);
      scene.add(core);
      const shellMat = new THREE.MeshBasicMaterial({ color: cfg.color, wireframe: true,
        transparent: true, opacity: 0.22 });
      const shell = new THREE.Mesh(new THREE.IcosahedronGeometry(2.2, 1), shellMat);
      scene.add(shell);
      const light = new THREE.PointLight(cfg.color, 2, 10); light.position.set(2, 2, 2); scene.add(light);
      scene.add(new THREE.AmbientLight(0x404040));
      camera.position.z = 5;
      matsRef.current = { coreMat, shellMat, light };

      const animate = () => {
        if (lost) return;
        raf = requestAnimationFrame(animate);
        const c = cfgRef.current;
        const t = Date.now();
        // generic 0..1 signal (mic amplitude, live event cadence, …) — reacts whenever a
        // caller opts in by passing a levelRef, regardless of state.
        const lvl = (levelRef && typeof levelRef.current === "number")
          ? Math.min(1, Math.max(0, levelRef.current)) : 0;
        const target = hover.current ? 3.4 : 1;
        boost.current += (target - boost.current) * 0.06;          // decay toward hover/1
        const react = 1 + lvl * 2.2;                               // spin up on louder input
        const m = c.base * boost.current * react;
        core.rotation.x += 0.005 * m; core.rotation.y += 0.008 * m;
        shell.rotation.x -= 0.003 * m; shell.rotation.y -= 0.002 * m;
        const pulse = c.pulse ? (0.13 + 0.10 * Math.sin(t * 0.012)) : 0;   // speaking bloom cadence
        const amp = 0.05 + (boost.current - 1) * 0.05 + (c.base - 1) * 0.03 + lvl * 0.30 + pulse;
        const s = 1 + Math.sin(t * 0.002) * amp + lvl * 0.18; core.scale.set(s, s, s);
        shell.scale.setScalar(1 + lvl * 0.12 + pulse * 0.5);       // shell breathes with voice / speech
        coreMat.emissiveIntensity = c.emissive + lvl * 0.9 + pulse * 0.6;  // brightens with input
        try { renderer.render(scene, camera); } catch (_) { cancelAnimationFrame(raf); }
      };

      onLost = (e) => { e.preventDefault(); lost = true; cancelAnimationFrame(raf); };
      onRestored = () => { lost = false; animate(); };
      canvas.addEventListener("webglcontextlost", onLost, false);
      canvas.addEventListener("webglcontextrestored", onRestored, false);
      onScroll = () => { boost.current = Math.max(boost.current, 2.6); };
      window.addEventListener("wheel", onScroll, { passive: true });
      window.addEventListener("scroll", onScroll, { passive: true, capture: true });

      animate();
      return cleanup;
    } catch (_) {
      cleanup();   // setup failed after the context was created — release it, don't leak
      return;
    }
  }, [size]);

  return <div ref={ref} style={{ width: size, height: size, margin: "0 auto", cursor: "pointer" }}
    onMouseEnter={() => { hover.current = true; }} onMouseLeave={() => { hover.current = false; }} />;
}
