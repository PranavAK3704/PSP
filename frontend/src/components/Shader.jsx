import React, { useEffect, useRef } from "react";

// Animated deep-teal "neural field" background (ported from the Stitch export).
// Pure WebGL, no deps; degrades to nothing if WebGL is unavailable.
const FRAG = `precision highp float;
varying vec2 v_texCoord;
uniform float u_time;
void main() {
  vec2 uv = v_texCoord;
  float t = u_time * 0.5;
  vec3 c1 = vec3(0.02, 0.04, 0.08);   // deep navy
  vec3 c2 = vec3(0.0, 0.45, 0.5);     // advocate teal
  float noise = sin(uv.x * 10.0 + t) * cos(uv.y * 10.0 - t);
  noise += sin(uv.x * 20.0 - t * 1.5) * 0.5;
  vec3 col = mix(c1, c2, noise * 0.15 + 0.1);
  float pulse = pow(sin(t * 0.8) * 0.5 + 0.5, 8.0);
  col += c2 * pulse * 0.05;
  gl_FragColor = vec4(col, 1.0);
}`;
const VERT = `attribute vec2 a_position; varying vec2 v_texCoord;
void main(){ v_texCoord = a_position * 0.5 + 0.5; gl_Position = vec4(a_position, 0.0, 1.0); }`;

export default function Shader({ opacity = 0.18 }) {
  const ref = useRef(null);
  useEffect(() => {
    const canvas = ref.current;
    let gl;
    try { gl = canvas.getContext("webgl") || canvas.getContext("experimental-webgl"); } catch (_) { gl = null; }
    if (!gl) return;
    // If the browser reclaims this context (e.g. too many live contexts), don't let it throw or
    // freeze — swallow the loss and stop drawing. The background simply falls back to --bg.
    const onLost = (e) => { e.preventDefault(); cancelAnimationFrame(raf); };
    canvas.addEventListener("webglcontextlost", onLost, false);
    const size = () => { canvas.width = canvas.clientWidth || 1280; canvas.height = canvas.clientHeight || 720; };
    size();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(size) : null;
    ro?.observe(canvas);

    const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; };
    const prog = gl.createProgram();
    gl.attachShader(prog, sh(gl.VERTEX_SHADER, VERT));
    gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, FRAG));
    gl.linkProgram(prog); gl.useProgram(prog);
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const pos = gl.getAttribLocation(prog, "a_position");
    gl.enableVertexAttribArray(pos);
    gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 0, 0);
    const uTime = gl.getUniformLocation(prog, "u_time");
    let raf;
    const render = (t) => {
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.uniform1f(uTime, t * 0.001);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      raf = requestAnimationFrame(render);
    };
    render(0);
    return () => { cancelAnimationFrame(raf); ro?.disconnect(); canvas.removeEventListener("webglcontextlost", onLost); };
  }, []);

  return (
    <canvas ref={ref} style={{
      position: "fixed", inset: 0, zIndex: 0, width: "100%", height: "100%",
      opacity, pointerEvents: "none",
    }} />
  );
}
