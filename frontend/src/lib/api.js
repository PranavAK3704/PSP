// Thin API client for the resolution-engine backend.
// Every request carries the session token (Authorization: Bearer <token>); a 401
// clears the stored token and dispatches `valmo-unauthed` so the app returns to login.

const TOKEN_KEY = "valmo.token";

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t) => { if (t) localStorage.setItem(TOKEN_KEY, t); };
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

function authHeaders(extra = {}) {
  const t = getToken();
  return t ? { ...extra, Authorization: `Bearer ${t}` } : { ...extra };
}

// Trip the app back to login on an expired/invalid session. Returns the response
// so callers can chain. Never fires for the (public) login call.
function guard(res) {
  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event("valmo-unauthed"));
  }
  return res;
}

const J = (r) => r.json();

async function apiGet(url) {
  return J(guard(await fetch(url, { headers: authHeaders() })));
}
async function apiPost(url, body) {
  return J(guard(await fetch(url, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  })));
}
// Multipart POST (file upload). Carries the auth bearer token but does NOT set
// Content-Type — the browser adds the multipart boundary automatically.
async function apiPostForm(url, formData) {
  return J(guard(await fetch(url, {
    method: "POST",
    headers: authHeaders(),
    body: formData,
  })));
}

// ── Auth ──
// Render's free tier SLEEPS the instance after ~15 min idle, and the first request then takes
// ~25s to boot the container (measured: 24.2s cold vs 0.58s warm). The browser abandons the
// request long before that, so the very first login attempt of the day failed with a bare
// "Failed to fetch" — indistinguishable, to the person typing, from a wrong password or a dead
// server. So a network-level failure is now RETRIED against the public /api/health probe until
// the instance answers, with progress reported so the wait is explained rather than mysterious.
//
// Only transport failures retry. An HTTP response — including 401 — is a real answer from a live
// server and returns immediately; retrying a wrong password would be both useless and confusing.
const COLD_START_TRIES = 6;
const COLD_START_GAP_MS = 5000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// A TypeError from fetch means the request never got an HTTP reply (DNS, connection reset,
// instance still booting). Anything else is a real server answer.
const isTransportError = (e) => e instanceof TypeError;

export async function login(email, password, onStatus = () => {}) {
  let lastErr;
  for (let attempt = 0; attempt < COLD_START_TRIES; attempt++) {
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        let msg = "Login failed";
        try { const d = await res.json(); if (d && d.detail) msg = d.detail; } catch { /* ignore */ }
        const err = new Error(msg); err.status = res.status; throw err;   // a real answer — do not retry
      }
      const data = await res.json();   // { token, user }
      setToken(data.token);
      onStatus("");
      return data;
    } catch (e) {
      if (!isTransportError(e)) throw e;     // 401/400/500 — surface it straight away
      lastErr = e;
      if (attempt === COLD_START_TRIES - 1) break;
      onStatus(attempt === 0
        ? "Waking the server — the free tier sleeps when idle, this can take up to a minute…"
        : `Still waking the server… (attempt ${attempt + 1} of ${COLD_START_TRIES - 1})`);
      // Poke the public health route: it boots the instance and, unlike login, cannot fail on
      // credentials, so a 200 here means the next login attempt will reach a live server.
      try { await fetch("/api/health", { cache: "no-store" }); } catch { /* still booting */ }
      await sleep(COLD_START_GAP_MS);
    }
  }
  const err = new Error(
    "Can't reach the server. It may still be starting up (the free tier sleeps when idle) — "
    + "please try again in a moment.");
  err.cause = lastErr;
  err.transport = true;
  throw err;
}
// getMe THROWS on any non-200 (used by the auth gate to decide login vs app).
export async function getMe() {
  const res = await fetch("/api/auth/me", { headers: authHeaders() });
  if (!res.ok) { if (res.status === 401) clearToken(); throw new Error("not authenticated"); }
  return res.json();
}
export function logout() { clearToken(); }
export const listUsers = () => apiGet("/api/auth/users");
export const createUser = (payload) => apiPost("/api/auth/users", payload);

export const getHealth = () => apiGet("/api/health");
export const getCaptains = () => apiGet("/api/captains");
export const getLedger = () => apiGet("/api/ledger");
export const getCaptainCases = (id) => apiGet(`/api/captain/${id}/cases`);
// SOP compile now streams stages (SSE) so the UI can animate the structuring/tiering.
export const compileSopStream = (sop_text, onStage, onEnd) =>
  stream({ url: "/api/sop/compile", method: "POST", body: { sop_text } }, onStage, onEnd);
// sop_id set → update that SOP in place (editing from the Knowledge Base); omit → create new.
export const approveSop = (policy, contributor = "sop-author", sop_id = "") =>
  apiPost("/api/sop/approve", { policy, contributor, sop_id });
// Save a compiled SOP as a draft so it's never lost (shows in the library, approve later).
export const saveSopDraft = (policy, contributor = "sop-author", sop_id = "") =>
  apiPost("/api/sop/save", { policy, contributor, sop_id });
export const deleteSop = (sop_id) => apiPost("/api/sop/delete", { sop_id });
// Upload a real ops artifact (Excel / Word / PDF / CSV / text) → extracted text to prefill the editor.
export function extractSop(file) {
  const fd = new FormData();
  fd.append("file", file);
  return apiPostForm("/api/sop/extract", fd);
}

// ── Authoring Studio: Domain Blueprints (a domain's stage-0 "brain") ──
export const compileBlueprintStream = (raw_text, domain, onStage, onEnd) =>
  stream({ url: "/api/blueprint/compile", method: "POST", body: { raw_text, domain } }, onStage, onEnd);
export const getBlueprints = () => apiGet("/api/blueprints");
export const saveBlueprint = (blueprint, contributor = "author") =>
  apiPost("/api/blueprint/save", { blueprint, contributor });
export const approveBlueprint = (domain) => apiPost("/api/blueprint/approve", { domain });

// ── Concern Log: per-concern resolution trace + export ──
export const getConcernTrace = (id) => apiGet(`/api/concern/${id}/trace`);
// Trigger a file download of the concern log (csv | json). Fetches WITH the auth
// token (an <a href> can't carry a header), then downloads the blob.
export async function exportLedger(format = "csv") {
  const res = guard(await fetch(`/api/ledger/export?format=${format}`, { headers: authHeaders() }));
  if (!res.ok) return;
  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = href;
  a.download = `concern_log.${format}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(href);
}

// ── Auditing Studio: rubric + LLM judge + scores ──
export const getAuditRubric = () => apiGet("/api/audit/rubric");
export const saveAuditRubric = (dimensions) => apiPost("/api/audit/rubric", { dimensions });
export const runAudit = (concern_id) => apiPost("/api/audit/run", { concern_id });
export const runAuditBatch = (limit = 10) => apiPost("/api/audit/run_batch", { limit });
export const getAuditScores = () => apiGet("/api/audit/scores");

// ── Auditing Studio: dynamic, editable Governance Framework ──
export const getFramework = () => apiGet("/api/framework");
export const saveFramework = (framework) => apiPost("/api/framework", { framework });
export const approveFramework = () => apiPost("/api/framework/approve");
// Upload a framework doc → the machine structures it into a draft framework.
export function uploadFramework(file) {
  const fd = new FormData();
  fd.append("file", file);
  return apiPostForm("/api/framework/upload", fd);
}


export const getL3 = () => apiGet("/api/l3/inbox");
export const resolveL3 = (concern_id, resolution_note) => apiPost("/api/l3/resolve", { concern_id, resolution_note });
export const getInsights = () => apiGet("/api/insights");
// Data Foundation — corpus-level aggregates over BOTH databases. Aggregates only: no partner
// id, AWB or per-captain breakdown, which is what makes it safe to display.
export const getDataFoundation = () => apiGet("/api/data/foundation");
export const getAudit = () => apiGet("/api/audit");
export const getKt = () => apiGet("/api/kt");
export const checkSopConformance = (policy) => apiPost("/api/sop/conformance", { policy });
export const submitNuance = (payload) => apiPost("/api/sop/nuance", payload);
export const submitKt = (text, contributor) => apiPost("/api/kt/submit", { text, contributor });
export const reviewKt = (kt_id, approve, reviewer) => apiPost("/api/kt/review", { kt_id, approve, reviewer });
export const sendSatisfaction = (concern_id, captain_id, satisfied, note) =>
  apiPost("/api/satisfaction", { concern_id, captain_id, satisfied, note });

// Stream SSE from a POST (chat) or GET (monitor). onTrace(event) per stage,
// onEnd() when the stream closes. Uses fetch + ReadableStream (works for POST SSE)
// and carries the auth token; a 401 trips the app back to login.
export async function stream({ url, method = "GET", body }, onTrace, onEnd) {
  const res = await fetch(url, {
    method,
    headers: authHeaders(body ? { "Content-Type": "application/json" } : {}),
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event("valmo-unauthed"));
    onEnd && onEnd();
    return;
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    // SSE frames are separated by a blank line — CRLF (sse-starlette) or LF.
    const frames = buf.split(/\r\n\r\n|\n\n/);
    buf = frames.pop() || "";
    for (const frame of frames) {
      const lines = frame.split(/\r?\n/);
      const evLine = lines.find((l) => l.startsWith("event:"));
      const dataLine = lines.find((l) => l.startsWith("data:"));
      if (!dataLine) continue;
      const kind = evLine ? evLine.slice(6).trim() : "trace";
      const raw = dataLine.slice(5).trim();
      if (kind === "end") { onEnd && onEnd(); return; }
      try { onTrace(JSON.parse(raw)); } catch { /* ignore */ }
    }
  }
  onEnd && onEnd();
}
