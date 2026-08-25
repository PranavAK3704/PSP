/* ── Render every page for real, in Node, and fail on a throw ─────────────────────────────────
 *
 *     node scripts/smoke-render.mjs
 *
 * WHY THIS EXISTS. The Captain Panel shipped broken and `vite build` said "✓ built in 1.21s".
 * Two lucide icons — `FolderOpen` and `Sparkles` — were used in JSX but not imported, because
 * they had come across from another file's import line when two components were extracted. React
 * received `undefined` as an element type and threw, so the entire panel fell into the error
 * boundary and read "This panel hit a snag".
 *
 * A bundler CANNOT catch that. `<Sparkles />` compiles to `jsx(Sparkles, …)`, and an undefined
 * identifier is perfectly valid JavaScript right up to the moment it is evaluated. The only
 * thing that finds it is EVALUATION — actually rendering the component.
 *
 * I also tried to catch it statically first, with a regex that resolved every `<Capitalised`
 * against the file's imports and declarations. It found the real bug and three false positives:
 * a URL inside a comment (`/captain/<DC>/payments`), a destructured prop (`function Tile({ icon:
 * Icon })`), and a default-plus-named import (`import AtRiskPanel, { SeverityBanner } from`).
 * A check with a 75% false-positive rate is a check people learn to ignore. Rendering has none.
 *
 * ── WHAT IT DOES AND DOES NOT COVER ──────────────────────────────────────────────────────────
 * `renderToString` runs the component body and every hook EXCEPT effects: `useState`
 * initialisers run, `useMemo` runs, the whole JSX tree is evaluated. So it catches undefined
 * element types, render-time exceptions, and bad initial state. It does NOT catch anything that
 * only happens in `useEffect`, on a click, or after data arrives — those need a browser.
 *
 * That is a real limit and it is still worth having: every failure this session that made a page
 * blank was a render-time failure.
 *
 * Network and storage are stubbed rather than mocked richly. A page must survive its own first
 * paint with NO data, because that is the state every page is in for the first few hundred
 * milliseconds in front of an audience.
 * ── */
import { build } from "esbuild";
import { renderToString } from "react-dom/server";
import { createElement } from "react";
import { readdirSync, mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

/* ── stubs: enough for a first paint, no more ── */
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  clear: () => store.clear(),
};
globalThis.window = globalThis.window || {
  location: { search: "", pathname: "/", href: "http://localhost/" },
  addEventListener() {}, removeEventListener() {}, dispatchEvent() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
  localStorage: globalThis.localStorage,
};
globalThis.document = globalThis.document || {
  documentElement: { style: { setProperty() {} } },
  addEventListener() {}, removeEventListener() {}, getElementById: () => null,
  createElement: () => ({ style: {}, setAttribute() {}, appendChild() {} }),
};
// `navigator` is a getter-only global on Node 21+, so plain assignment throws. Only define it
// if it is genuinely absent, and via defineProperty so we are not fighting the platform.
if (!globalThis.navigator) {
  Object.defineProperty(globalThis, "navigator", {
    value: { userAgent: "node", language: "en-IN" }, configurable: true });
}
globalThis.fetch = () => new Promise(() => {});      // never resolves: the pre-data state
// Same getter-only story as `navigator`. Node ships a real webcrypto, so this is only a
// fallback for older runtimes.
if (!globalThis.crypto) {
  Object.defineProperty(globalThis, "crypto", {
    value: { randomUUID: () => "00000000-0000-4000-8000-000000000000" }, configurable: true });
}
globalThis.EventSource = class { close() {} };
// Referenced at RENDER time by Pipeline's ConfidenceDial (an SVG ref type-check), so a stub is
// required rather than optional — without it that component throws and reads as a real bug.
globalThis.SVGElement = globalThis.SVGElement || class {};
globalThis.HTMLElement = globalThis.HTMLElement || class {};
globalThis.ResizeObserver = globalThis.ResizeObserver || class {
  observe() {} unobserve() {} disconnect() {} };

const SRC = resolve("src");
const TARGETS = [
  ...readdirSync(join(SRC, "pages")).filter((f) => f.endsWith(".jsx"))
     .map((f) => ["pages/" + f, `pages/${f}`]),
  ["captain/CaptainPanelReplica.jsx", "captain/CaptainPanelReplica.jsx"],
  ...readdirSync(join(SRC, "captain/modules")).filter((f) => f.endsWith(".jsx"))
     .map((f) => ["captain/modules/" + f, `captain/modules/${f}`]),
  ...readdirSync(join(SRC, "components")).filter((f) => f.endsWith(".jsx"))
     .map((f) => ["components/" + f, `components/${f}`]),
];

// INSIDE the project, not os.tmpdir(): Node resolves bare `react` imports by walking up from
// the importing file, and a bundle in /var/folders has no node_modules above it. Bundling react
// in instead would give renderToString a DIFFERENT React instance than the components use, and
// hooks would throw "invalid hook call" — a fake failure on every file.
const dir = mkdtempSync(join(resolve("node_modules/.cache"), "psp-smoke-"));
let failed = 0, passed = 0, skipped = 0;

for (const [rel, label] of TARGETS) {
  const entry = join(dir, "entry.jsx");
  // `import * as Mod` rather than `import Comp from`: several files export only named components
  // (SupportWidget, the five Simulated modules), and a default import from those is a BUILD
  // error. Star-importing also lets us render EVERY exported component rather than one per file
  // — which is the case that matters, because the bug that started this was inside a named
  // export that nothing rendered at build time.
  //
  // Providers are included because a context read without them returns the provider's DEFAULT,
  // which is a different code path from production and would hide real bugs.
  writeFileSync(entry, `
    import * as Mod from ${JSON.stringify(join(SRC, rel))};
    import { AudienceProvider } from ${JSON.stringify(join(SRC, "lib/audienceMode.js"))};
    import { ChatStoreProvider } from ${JSON.stringify(join(SRC, "lib/chatStore.jsx"))};
    import { AuthCtx } from ${JSON.stringify(join(SRC, "lib/auth.jsx"))};
    export { Mod, AudienceProvider, ChatStoreProvider, AuthCtx };
  `);
  const out = join(dir, "bundle.mjs");
  try {
    await build({ entryPoints: [entry], outfile: out, bundle: true, format: "esm",
                  platform: "neutral", jsx: "automatic", logLevel: "silent",
                  external: ["react", "react-dom", "react/jsx-runtime", "lucide-react"] });
  } catch (e) {
    console.log(`  BUILD  ${label}\n         ${String(e.message).split("\n")[0]}`);
    failed++; continue;
  }
  let mod;
  try {
    mod = await import(pathToFileURL(out).href + `?t=${passed + failed + skipped}`);
  } catch (e) {
    console.log(`  IMPORT ${label}\n         ${String(e.message).split("\n")[0]}`);
    failed++; continue;
  }
  const { Mod, AudienceProvider, ChatStoreProvider, AuthCtx } = mod;
  // A component, by convention: a function whose exported name starts uppercase — which excludes
  // the helpers these files also export (readTrace, dateRange, ...) — PLUS the default export,
  // whose key under a star-import is the literal string "default" and therefore fails that test.
  // Missing it meant every page with a default export reported "no components exported", i.e.
  // the harness silently checked almost nothing while printing a clean run.
  const comps = Object.entries(Mod)
    .filter(([n, v]) => typeof v === "function" && /^[A-Z]/.test(n))
    .concat(typeof Mod.default === "function" ? [["default", Mod.default]] : []);
  if (!comps.length) { console.log(`  --     ${label} (no components exported)`); skipped++; continue; }
  let fileOk = true;
  for (const [name, C] of comps) {
    try {
      // AuthCtx.Provider with a literal value, NOT AuthProvider. AuthProvider resolves the
      // session in an effect, effects do not run under renderToString, so it sits in its
      // pre-auth branch and never renders children — the harness passed 22/22 while rendering
      // the login screen 22 times. An approver is used because it is the permissive path: it
      // reaches the write controls and the branches a viewer never sees.
      renderToString(createElement(AuthCtx.Provider,
        { value: { user: { email: "smoke@test.local", name: "Smoke", role: "approver" },
                   isApprover: true, ready: true, login: async () => {}, logout: () => {} } },
        createElement(AudienceProvider, null,
          createElement(ChatStoreProvider, null, createElement(C)))));
    } catch (e) {
      const msg = String(e.message || e).split("\n")[0];
      console.log(`  THROW  ${label} -> <${name}/>\n         ${msg}`);
      fileOk = false;
    }
  }
  if (fileOk) { console.log(`  ok     ${label} (${comps.map(([n]) => n).join(", ")})`); passed++; }
  else failed++;
}

rmSync(dir, { recursive: true, force: true });
console.log(`\n${passed} rendered, ${failed} failed, ${skipped} skipped (no default export).`);
if (failed) {
  console.log("A THROW here is a page that shows the error boundary in the browser.");
  process.exit(1);
}
console.log("Every page survives its own first paint with no data.");
