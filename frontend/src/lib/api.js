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
async function apiPatch(url, body) {
  return J(guard(await fetch(url, {
    method: "PATCH",
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
export const setUserRole = (email, role) => apiPost("/api/auth/role", { email, role });
export const setUserPassword = (email, password) => apiPost("/api/auth/password", { email, password });

export const getHealth = () => apiGet("/api/health");
export const getCaptains = () => apiGet("/api/captains");

export const getLedger = () => apiGet("/api/ledger");
export const getCaptainCases = (id) => apiGet(`/api/captain/${id}/cases`);
/* What proactive monitoring found for this captain. Separate from cases on purpose: nobody
   escalated a nudge, it has no SLA, and it must never render as something the captain raised. */
export const getCaptainNudges = (id) => apiGet(`/api/captain/${id}/nudges`);
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
// Each unit of `limit` is one deep-tier LLM call, so the default is deliberately small and
// the backend rejects anything over 20 at the request boundary.
export const runAuditBatch = (limit = 5) => apiPost("/api/audit/run_batch", { limit });
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


// `team` is optional: omitted, the server opens on the caller's own team; "*" is all teams.
export const getL3 = (team) =>
  apiGet("/api/l3/inbox" + (team ? `?team=${encodeURIComponent(team)}` : ""));
/* A resolution is four things, not one string — see components/ResolutionComposer.jsx and
   backend/app/l3/platform.py. `resolution_note` is PARTNER-FACING and is validated server-side;
   `internal_note` never reaches the captain; `outcome` of "need_input" answers them and leaves
   the case OPEN. The extras are optional so any older caller keeps working. */
export const resolveL3 = (concern_id, resolution_note, extra = {}) =>
  apiPost("/api/l3/resolve", { concern_id, resolution_note,
    internal_note: extra.internal_note || "",
    outcome: extra.outcome || "resolved",
    ...(extra.attachments && extra.attachments.length ? { attachments: extra.attachments } : {}) });
export const getInsights = () => apiGet("/api/insights");
// Data Foundation — corpus-level aggregates over BOTH databases. Aggregates only: no partner
// id, AWB or per-captain breakdown, which is what makes it safe to display.
export const getDataFoundation = () => apiGet("/api/data/foundation");
// Growth Dashboard (Orders & Planning) — the captain panel's own two endpoints, fixture-backed.
// The dashboard page and the support widget read the SAME payload the engine reasons over.
export const getGrowthIndex = () => apiGet("/api/growth");
export const getGrowth = (hub) => apiGet(`/api/growth/${encodeURIComponent(hub)}`);
// At-risk shipments, hub-keyed. A SEPARATE route from /api/growth on purpose: these are real
// valmo.db rows, and putting them inside a payload labelled "growth-dashboard-fixture" would
// make the provenance unreconstructable. Two sources, two routes, two chips on screen.
export const getAtRisk = (hub, asOf) =>
  apiGet(`/api/risk/hub/${encodeURIComponent(hub)}` + (asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""));

// Calibration — reliability bins over the real Concern Log, plus the Kapture agreement set.
export const getCalibration = () => apiGet("/api/calibration");

// Connector registry — declarative; the backend never calls anything to build it.
export const getConnectors = () => apiGet("/api/connectors");
/* Real ticket volumes from the Kapture export — supporting evidence on the Connectors page.
   A failure here must not blank that page, so the caller catches it separately. */
export const getTickets = () => apiGet("/api/tickets/summary");

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
// `signal` lets a caller CANCEL an in-flight stream. Without it a component that unmounts (or
// switches captain/hub) mid-turn had no way to stop the read loop, so the orphaned stream kept
// pushing events into a conversation that had already been cleared — one captain's answer
// landing in another's thread.
/* ── The ONE way to start a chat turn ─────────────────────────────────────────────────────────
   Both callers used to hand-build this body — CaptainPanel (the internal bench) and the docked
   widget on the Captain Panel. Two hand-built bodies is how a required field goes missing on
   one screen and nobody notices, and `source` is exactly such a field: the server cannot tell
   the bench from the widget (same endpoint, same auth), so the client is the only thing that
   knows, and a row it fails to label lands in the ledger the deck aggregates.

   `source` is therefore REQUIRED and its absence THROWS rather than defaulting. A default of
   `"partner"` would silently count operator test traffic as captain traffic — the precise
   inaccuracy this whole pass exists to remove — and a default of `"unclassified"` would hide a
   real wiring mistake behind a plausible-looking bucket. A throw shows up the first time the
   screen is used, which for the widget is the app's default surface.

   Allowed values are the two client-visible ones. `monitor`, `l3` and `harness` are asserted
   server-side by the code that owns those writes; a browser cannot claim them. ── */
const CHAT_SOURCES = ["partner", "operator"];

export function chatStream({ captainId, message, conversationId, source, selectedOption,
                             attachments, signal }, onTrace, onEnd) {
  if (!CHAT_SOURCES.includes(source)) {
    throw new Error(
      `chatStream: source must be one of ${CHAT_SOURCES.join(" | ")}, got ${JSON.stringify(source)}. ` +
      `Every concern row carries its provenance; see backend/app/ledger/concern_log.py.`);
  }
  return stream({
    url: "/api/chat", method: "POST", signal,
    body: {
      captain_id: captainId, message, conversation_id: conversationId, source,
      // Omitted rather than sent as null: the backend treats an absent `selected_option` as
      // "the captain typed", and an explicit null would be a third state to handle.
      ...(selectedOption ? { selected_option: selectedOption } : {}),
      ...(attachments && attachments.length ? { attachments } : {}),
    },
  }, onTrace, onEnd);
}

export async function stream({ url, method = "GET", body, signal }, onTrace, onEnd) {
  const res = await fetch(url, {
    method,
    headers: authHeaders(body ? { "Content-Type": "application/json" } : {}),
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  if (res.status === 401) {
    clearToken();
    window.dispatchEvent(new Event("valmo-unauthed"));
    onEnd && onEnd();
    return;
  }
  // A 204/empty response has no body. Reading `.getReader()` off null threw, and because the
  // only `setBusy(false)` lived in onEnd, the caller's input stayed disabled forever.
  if (!res.body) { onEnd && onEnd(); return; }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    let value, done;
    try {
      ({ value, done } = await reader.read());
    } catch (e) {
      // An aborted or reset connection lands here. Ending cleanly is what lets the caller
      // re-enable its input; propagating would leave it stuck mid-turn.
      onEnd && onEnd();
      return;
    }
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
