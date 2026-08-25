import { createContext, useContext, useEffect, useState } from "react";
import CaptainPanel from "./pages/CaptainPanel.jsx";
import GrowthDashboard from "./pages/GrowthDashboard.jsx";
import Connectors from "./pages/Connectors.jsx";
import Calibration from "./pages/Calibration.jsx";
import L3Workspace from "./pages/L3Workspace.jsx";
import SupportCommand from "./pages/SupportCommand.jsx";
import Shader from "./components/Shader.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { ChatStoreProvider } from "./lib/chatStore.jsx";
import { AudienceProvider } from "./lib/audienceMode.js";
import { getHealth, listUsers, createUser } from "./lib/api.js";
import { AuthProvider, useAuth } from "./lib/auth.jsx";

/* ── Views, ORGANISED BY WHO IS LOOKING ───────────────────────────────────────────────────────

   This was seven flat feature tabs, which is a map of what got built rather than of who uses it.
   The platform has exactly three kinds of user and each sees a different projection of ONE
   record (the concern and its trace):

     a CAPTAIN   sees a widget on the panel they already open every morning
     an L3 MEMBER sees the cases routed to them
     the SUPPORT TEAM sees everything, and tests the engine on the bench

   Grouped that way, the sidebar states the claim. Flat, it hid it.

   ── TWO RENAMES, and why they matter more than they look ─────────────────────────────────
   `growth` -> `captain`: the partner's own screen IS the Captain Panel, and it is now the
   destination the app OPENS ON. It was one row below an internal console also called "Captain
   Advocate", so on load you saw the old bench and the entire captain surface was invisible.
   Old `captain` -> `bench`: it is the Advocate TEST BENCH — a colleague driving the engine as
   a captain — and calling it "Captain Advocate" made two adjacent rows both start with
   "Captain" and share a hue, which is how one got mistaken for the other.

   `monitor` is GONE as a destination. Proactive monitoring is docked on the Captain Panel,
   where the nudge actually lands. A tab of its own made it read as a demo of itself. ── */
const VIEWS = {
  // The partner's OWN screen, with support docked beside it. Same payload, two presentations —
  // which is only a demonstrable claim if both are on the projector at the same time.
  captain: { label: "Captain Panel",        icon: "bar_chart",       comp: GrowthDashboard,
             title: "Captain Panel · Growth Dashboard",
             sub: "What the captain sees — support widget and risk monitoring docked beside it." },
  l3:      { label: "L3 Console",           icon: "inbox",           comp: L3Workspace,
             title: "L3 Console",           sub: "Escalated cases worked by the L3 desk." },
  support: { label: "Support Command",      icon: "space_dashboard", comp: SupportCommand,
             title: "Support Command",      sub: "Command deck, authoring, auditing, governance & the concern log." },
  bench:   { label: "Advocate Test Bench",  icon: "forum",           comp: CaptainPanel,
             title: "Advocate Test Bench",
             sub: "Drive the engine as any captain. Writes are logged source=operator, not partner." },
  // The access ask, as a table. Declarative — the backend never calls anything to build it.
  connectors: { label: "Connectors",      icon: "power",           comp: Connectors,
             title: "Connector Registry",
             sub: "Which real endpoints exist, what PSP does with each today, and what going live costs." },
  // Is the gate's confidence a probability, or a label? Measured, and the answer is "a label".
  calibration: { label: "Calibration",     icon: "speed",           comp: Calibration,
             title: "Calibration",
             sub: "Whether the trust gate's confidence means what its notation implies." },
};

/* One group per PERSONA. `.nav-sep` has existed in styles.css since the first build and was
   never used — this is what it was for. */
const NAV_GROUPS = [
  { label: "captain",      rows: ["captain"] },
  { label: "l3 desk",      rows: ["l3"] },
  // `connectors` and `calibration` sit here because a support-team person is who reads them,
  // and they fold INTO Support Command as sub-tabs in the next pass — at which point this
  // group is two rows.
  { label: "support team", rows: ["support", "bench", "connectors", "calibration"] },
];

// Nav context — some pages (e.g. Monitor) call useNav() to jump views. Exposes setView.
const NavCtx = createContext(() => {});
export const useNav = () => useContext(NavCtx);

const ROLE_TONE = {
  approver: "text-tertiary bg-tertiary/10 border-tertiary/30",
  author: "text-secondary-container bg-secondary-container/10 border-secondary-container/30",
  viewer: "text-on-surface-variant bg-surface-variant/40 border-on-primary-fixed-variant/20",
};
// Plain-language labels for the operator role model — leadership shouldn't have to parse
// "approver/author/viewer". Backend role values are unchanged; this is display only.
const ROLE_LABEL = { approver: "Admin", author: "Editor", viewer: "Viewer" };
const ROLE_HINT = {
  approver: "Full access — sees everything, approves go-lives, and manages the team. Use this for leadership who need admin rights.",
  author: "Can draft and queue knowledge (SOPs, brains); cannot make things go live.",
  viewer: "Read-only — sees every dashboard, metric, and audit; changes nothing. Good for leadership who only observe.",
};

// Wrap the whole app in the auth gate: no valid session → Login; otherwise the shell.
export default function App() {
  return (
    <AuthProvider>
      <Root />
    </AuthProvider>
  );
}

function Root() {
  const { user } = useAuth();
  if (!user) return <Login />;
  return <Shell />;
}

/* ══════════════════════════════════════════════════════════════════════════
   LOGIN — the pilot gate. Dark ops-room aesthetic, email + password, error state.
   ══════════════════════════════════════════════════════════════════════════ */
function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  // Cold-start progress, distinct from `err`: this is "still working, here's why it's slow",
  // not "it failed". Showing it as an error was what made a sleeping free-tier instance look
  // like a broken login.
  const [status, setStatus] = useState("");

  async function submit(e) {
    e.preventDefault();
    if (busy || !email.trim() || !password) return;
    setBusy(true); setErr(""); setStatus("");
    try { await login(email.trim(), password, setStatus); }
    catch (ex) { setErr(ex.message || "Login failed"); setBusy(false); setStatus(""); }
  }

  return (
    <div className="fixed inset-0 grid place-items-center bg-background overflow-hidden">
      {/* ambient field + faint grid, matching the app shell */}
      <div className="absolute inset-0 pointer-events-none"
        style={{ background: "radial-gradient(900px 600px at 82% -8%, rgba(77,142,255,0.10), transparent 60%), radial-gradient(800px 600px at 10% 110%, rgba(78,222,163,0.07), transparent 60%)" }} />
      <div className="absolute inset-0 pointer-events-none"
        style={{ backgroundImage: "linear-gradient(rgba(65,71,83,0.05) 1px,transparent 1px),linear-gradient(90deg,rgba(65,71,83,0.05) 1px,transparent 1px)", backgroundSize: "46px 46px" }} />

      <form onSubmit={submit} className="relative z-10 w-[92%] max-w-[400px] glass-card rounded-2xl p-xl shadow-2xl">
        <div className="flex items-center gap-2 mb-lg">
          <span className="material-symbols-outlined text-secondary-container" style={{ fontSize: 26 }}>security</span>
          <div>
            <div className="text-secondary-container font-bold text-lg leading-none">Valmo Advocate</div>
            <div className="text-[9px] text-on-surface-variant uppercase tracking-[0.2em] mt-1 opacity-60">Partner-support platform</div>
          </div>
        </div>

        <div className="text-[11px] font-bold uppercase tracking-[0.1em] text-on-surface-variant mb-sm">Sign in</div>

        <label className="block text-[11px] text-on-surface-variant mb-1">Email</label>
        <input type="email" autoFocus value={email} onChange={(e) => setEmail(e.target.value)}
          placeholder="you@valmo"
          className="w-full mb-md bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded-lg px-md py-sm text-sm focus:outline-none focus:border-secondary-container placeholder:text-on-surface-variant/40" />

        <label className="block text-[11px] text-on-surface-variant mb-1">Password</label>
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          placeholder="••••••••"
          className="w-full mb-md bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded-lg px-md py-sm text-sm focus:outline-none focus:border-secondary-container placeholder:text-on-surface-variant/40" />

        {err && (
          <div className="flex items-center gap-2 mb-md text-xs text-error bg-error/10 border border-error/30 rounded-lg px-md py-sm">
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>error</span>{err}
          </div>
        )}

        {/* Waiting, not failing — amber and explanatory, so a 25s cold start reads as a slow
            server rather than a rejected password. */}
        {!err && status && (
          <div className="flex items-start gap-2 mb-md text-xs rounded-lg px-md py-sm"
            style={{ color: "var(--warn)", background: "var(--warn-soft)", border: "1px solid rgba(255,185,95,0.3)" }}>
            <span className="material-symbols-outlined animate-spin" style={{ fontSize: 16, flex: "none" }}>progress_activity</span>
            <span style={{ lineHeight: 1.45 }}>{status}</span>
          </div>
        )}

        <button type="submit" disabled={busy || !email.trim() || !password}
          className="w-full bg-secondary-container text-on-secondary py-sm rounded-lg font-bold text-sm flex items-center justify-center gap-2 hover:brightness-110 disabled:opacity-50 transition-all">
          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>{busy ? "progress_activity" : "login"}</span>
          {busy ? (status ? "Waking server…" : "Signing in…") : "Sign in"}
        </button>
        <p className="text-[10px] text-on-surface-variant/70 mt-md text-center leading-relaxed">
          Access is role-based. Ask an admin on your team to create your account.
        </p>
      </form>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
   TEAM ADMIN — approver-only. List + add team members (email, name, role, temp pw).
   ══════════════════════════════════════════════════════════════════════════ */
const ADMIN_ROLES = ["viewer", "author", "approver"];
function TeamAdmin({ onClose }) {
  const [users, setUsers] = useState([]);
  const [form, setForm] = useState({ email: "", name: "", role: "approver", password: "" });
  const [err, setErr] = useState("");
  const [created, setCreated] = useState(null);   // { email, password, role } to hand over
  const [busy, setBusy] = useState(false);

  const load = () => listUsers().then((d) => setUsers(d.users || [])).catch(() => {});
  useEffect(() => { load(); }, []);

  // Strong, unambiguous temp password (no 0/O/1/l/I) so onboarding a leader is one click.
  const genPassword = () => {
    const chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789";
    const arr = new Uint32Array(14);
    (window.crypto || {}).getRandomValues?.(arr);
    const pw = arr.length && arr[0] !== undefined
      ? Array.from(arr, (n) => chars[n % chars.length]).join("")
      : Math.random().toString(36).slice(2, 12) + "Aa9";
    setForm((f) => ({ ...f, password: pw }));
  };

  async function add(e) {
    e.preventDefault();
    if (busy || !form.email.trim() || !form.password) return;
    setBusy(true); setErr(""); setCreated(null);
    try {
      const r = await createUser({ ...form, email: form.email.trim() });
      if (r.ok) {
        setCreated({ email: r.user.email, password: form.password, role: r.user.role });
        setForm((f) => ({ email: "", name: "", role: f.role, password: "" }));   // keep the role for adding several
        load();
      } else setErr(r.detail || "Could not create user");
    } catch (ex) { setErr(ex.message || "Could not create user"); }
    finally { setBusy(false); }
  }

  return (
    <div className="fixed inset-0 z-[100] grid place-items-center bg-black/60 backdrop-blur-sm p-md" onClick={onClose}>
      <div className="glass-card rounded-2xl w-[92%] max-w-[560px] max-h-[86vh] overflow-hidden flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-lg py-md border-b border-on-primary-fixed-variant/20">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-secondary-container" style={{ fontSize: 20 }}>group</span>
            <span className="text-sm font-bold">Team access</span>
          </div>
          <button onClick={onClose} className="w-8 h-8 grid place-items-center rounded-lg text-on-surface-variant hover:text-error hover:bg-error/10">
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>close</span>
          </button>
        </div>

        <div className="overflow-y-auto custom-scrollbar p-lg flex flex-col gap-lg">
          {/* existing team */}
          <div>
            <div className="text-[10px] uppercase tracking-wide text-on-surface-variant mb-sm">Members · {users.length}</div>
            <div className="space-y-sm">
              {users.length === 0 && <div className="text-on-surface-variant text-sm">No users loaded.</div>}
              {users.map((u) => (
                <div key={u.email} className="flex items-center justify-between bg-surface-container-lowest border border-on-primary-fixed-variant/15 rounded-lg px-md py-sm">
                  <div className="min-w-0">
                    <div className="text-sm font-semibold truncate">{u.name || u.email}</div>
                    <div className="text-[10px] text-on-surface-variant truncate" style={{ fontFamily: "JetBrains Mono" }}>{u.email}</div>
                  </div>
                  <span className={`text-[10px] font-bold px-2 py-0.5 rounded border ${ROLE_TONE[u.role] || ROLE_TONE.viewer}`}>{ROLE_LABEL[u.role] || u.role}</span>
                </div>
              ))}
            </div>
          </div>

          {/* add member */}
          <form onSubmit={add} className="border-t border-on-primary-fixed-variant/15 pt-lg">
            <div className="text-[10px] uppercase tracking-wide text-on-surface-variant mb-sm">Add a member</div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-sm mb-sm">
              <input value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} placeholder="email" type="email"
                className="bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded-lg px-sm py-1.5 text-[13px] focus:outline-none focus:border-secondary-container placeholder:text-on-surface-variant/40" />
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="name"
                className="bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded-lg px-sm py-1.5 text-[13px] focus:outline-none focus:border-secondary-container placeholder:text-on-surface-variant/40" />
              <div className="flex gap-1.5">
                <input value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder="temp password" type="text"
                  className="flex-1 min-w-0 bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded-lg px-sm py-1.5 text-[13px] focus:outline-none focus:border-secondary-container placeholder:text-on-surface-variant/40" />
                <button type="button" onClick={genPassword} title="Generate a strong temp password"
                  className="flex-none px-2 rounded-lg border border-on-primary-fixed-variant/20 text-on-surface-variant hover:text-secondary-container hover:border-secondary-container/40">
                  <span className="material-symbols-outlined" style={{ fontSize: 16 }}>casino</span>
                </button>
              </div>
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}
                className="bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded-lg px-sm py-1.5 text-[13px] focus:outline-none focus:border-secondary-container">
                {ADMIN_ROLES.map((r) => <option key={r} value={r}>{ROLE_LABEL[r] || r}</option>)}
              </select>
            </div>
            <p className="text-[10px] text-on-surface-variant/70 mb-sm leading-relaxed">{ROLE_HINT[form.role]}</p>
            {err && <div className="text-xs text-error mb-sm flex items-center gap-1"><span className="material-symbols-outlined" style={{ fontSize: 15 }}>error</span>{err}</div>}
            {created && (
              <div className="mb-sm rounded-lg border border-tertiary/30 bg-tertiary/10 p-md">
                <div className="text-[11px] font-bold text-tertiary flex items-center gap-1 mb-1">
                  <span className="material-symbols-outlined" style={{ fontSize: 15 }}>check_circle</span>
                  {ROLE_LABEL[created.role] || created.role} account created — share these credentials
                </div>
                <div className="text-[12px] leading-relaxed" style={{ fontFamily: "JetBrains Mono" }}>
                  <div>{created.email}</div>
                  <div>{created.password}</div>
                </div>
                <div className="flex gap-2 mt-2">
                  <button type="button"
                    onClick={() => navigator.clipboard?.writeText(`Valmo Advocate login\nURL: ${window.location.origin}\nEmail: ${created.email}\nTemp password: ${created.password}\n(You'll be asked to change it — please do.)`)}
                    className="text-[11px] px-2 py-1 rounded border border-tertiary/30 text-tertiary hover:bg-tertiary/10 flex items-center gap-1">
                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>content_copy</span>Copy invite
                  </button>
                  <button type="button" onClick={() => setCreated(null)}
                    className="text-[11px] px-2 py-1 rounded border border-on-primary-fixed-variant/20 text-on-surface-variant hover:text-error">Dismiss</button>
                </div>
              </div>
            )}
            <button type="submit" disabled={busy || !form.email.trim() || !form.password}
              className="bg-secondary-container text-on-secondary px-lg py-sm rounded-lg font-bold text-sm flex items-center gap-2 hover:brightness-110 disabled:opacity-50 transition-all">
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>person_add</span>Add member
            </button>
            <p className="text-[10px] text-on-surface-variant/70 mt-sm leading-relaxed">
              <b>Admin</b> = full access + manage the team (use for leadership who need control) · <b>Editor</b> = draft/queue knowledge · <b>Viewer</b> = read-only (leadership who only observe).
            </p>
          </form>
        </div>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
   SHELL — the authenticated app. FLAT LEFT SIDEBAR (Stitch "Command Center"):
   .app grid = .sidebar (brand · two nav groups · foot) + .main (.topbar + .content).
   Shader + faint-grid sit behind everything (z-index below the shell).
   ══════════════════════════════════════════════════════════════════════════ */
function Shell() {
  const { user, isApprover, logout } = useAuth();
  const [view, setView] = useState("captain");
  const [health, setHealth] = useState(null);
  const [teamOpen, setTeamOpen] = useState(false);
  useEffect(() => { getHealth().then(setHealth).catch(() => setHealth({ down: true })); }, []);
  const cur = VIEWS[view];
  const Comp = cur.comp;

  const NavItem = ({ k }) => {
    const v = VIEWS[k];
    return (
      <div className={`nav-item ${view === k ? "active" : ""}`} data-view={k} role="button" tabIndex={0}
        onClick={() => setView(k)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setView(k); } }}>
        <span className="material-symbols-outlined" style={{ fontSize: 20 }}>{v.icon}</span>
        <span>{v.label}</span>
      </div>
    );
  };

  const footBtn = { width: 30, height: 30, borderRadius: 8, border: "1px solid var(--line)",
    background: "var(--surface-2)", display: "grid", placeItems: "center", cursor: "pointer", flex: "none" };

  return (
    <NavCtx.Provider value={setView}>
      {/* The audience toggle is view-scoped state, so it lives above the shell but is read only
          by the captain view. See lib/audienceMode.js for why it is not a global demo mode. */}
      <AudienceProvider>
      <ChatStoreProvider>
        {/* background: animated field + faint grid, behind the shell (z 0 / 1; .app is z 2) */}
        <Shader opacity={0.16} />
        <div className="fixed inset-0 z-[1] pointer-events-none"
          style={{ backgroundImage: "linear-gradient(rgba(65,71,83,0.05) 1px,transparent 1px),linear-gradient(90deg,rgba(65,71,83,0.05) 1px,transparent 1px)", backgroundSize: "46px 46px" }} />

        {/* data-view drives the per-destination accent hue (styles.css). One attribute here
            re-tints the nav item, the page rule, headings and focus states, so each destination
            is recognisable at a glance instead of five identical blue screens. */}
        <div className="app" data-view={view}>
          {/* ── SIDEBAR ── */}
          <aside className="sidebar">
            <div className="brand">
              <span className="logo">Valmo<em> Advocate</em></span>
              <span className="tag">command center</span>
            </div>

            {NAV_GROUPS.map((g) => (
              <div key={g.label}>
                <div className="nav-sep">{g.label}</div>
                {g.rows.map((k) => <NavItem key={k} k={k} />)}
              </div>
            ))}

            {/* ── FOOT: signed-in user · role · logout · (approver) team · health ── */}
            <div className="sidebar-foot">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ color: "var(--text-mute)", fontSize: 11.5, fontWeight: 600,
                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 128 }}
                    title={user.name || user.email}>{user.name || user.email}</div>
                  <span className={`inline-block mt-1 text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded border ${ROLE_TONE[user.role] || ROLE_TONE.viewer}`}>
                    {ROLE_LABEL[user.role] || user.role}</span>
                </div>
                <div style={{ display: "flex", gap: 6 }}>
                  {isApprover && (
                    <button onClick={() => setTeamOpen(true)} title="Team access" style={footBtn}
                      className="text-secondary-container hover:brightness-125">
                      <span className="material-symbols-outlined" style={{ fontSize: 16 }}>group</span>
                    </button>
                  )}
                  <button onClick={logout} title="Sign out" style={footBtn}
                    className="text-on-surface-variant hover:text-error">
                    <span className="material-symbols-outlined" style={{ fontSize: 16 }}>logout</span>
                  </button>
                </div>
              </div>
              <div className="pill-provider"
                style={health?.down ? { background: "var(--bad-soft)", color: "var(--bad)", borderColor: "rgba(255,180,171,0.25)" } : undefined}>
                <span className="dot" />
                {health?.down ? "engine offline" : `${health?.provider || "connecting…"} · tiered`}
              </div>
            </div>
          </aside>

          {/* ── MAIN ── */}
          <div className="main">
            <div className="topbar">
              <div>
                <h1>{cur.title}</h1>
                {cur.sub && <div className="sub">{cur.sub}</div>}
              </div>
              <div className="hidden xl:flex items-center gap-1.5 text-[10px]" style={{ fontFamily: "JetBrains Mono", paddingBottom: 6 }}>
                <span className={`w-1.5 h-1.5 rounded-full ${health?.down ? "bg-error" : "bg-tertiary"}`}
                  style={{ boxShadow: health?.down ? "0 0 8px #ffb4ab" : "0 0 8px #4edea3" }} />
                <span className="text-on-surface-variant">{health?.down ? "offline" : `${health?.provider || "…"} · tiered`}</span>
              </div>
            </div>

            <div className="content">
              {/* keyed per view → navigating to another view resets a crashed one; the shell stays alive */}
              <ErrorBoundary key={view}>
                <Comp />
              </ErrorBoundary>
            </div>
          </div>
        </div>

        {teamOpen && <TeamAdmin onClose={() => setTeamOpen(false)} />}
      </ChatStoreProvider>
      </AudienceProvider>
    </NavCtx.Provider>
  );
}
