import React, { createContext, useContext, useEffect, useState } from "react";
import CaptainPanel from "./pages/CaptainPanel.jsx";
import ResolutionTrace from "./pages/ResolutionTrace.jsx";
import Monitor from "./pages/Monitor.jsx";
import L3Workspace from "./pages/L3Workspace.jsx";
import { Command, AuthoringStudio, AuditingStudio, GovernanceFramework, Ledger } from "./pages/SupportCommand.jsx";
import Shader from "./components/Shader.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { ChatStoreProvider } from "./lib/chatStore.jsx";
import { getHealth, listUsers, createUser } from "./lib/api.js";
import { AuthProvider, useAuth } from "./lib/auth.jsx";

/* ── Views: every nav item maps to a standalone component. `title`/`sub` drive the
   slim topbar. Grouped into the two sidebar sections below. ── */
const VIEWS = {
  captain:    { label: "Captain Advocate",    icon: "forum",           comp: CaptainPanel,
                title: "Captain Advocate",    sub: "Live partner-support advocacy — resolve concerns in-conversation." },
  trace:      { label: "Resolution Trace",    icon: "account_tree",    comp: ResolutionTrace,
                title: "Resolution Trace",    sub: "The latest resolution's decision core + trust-spine pipeline." },
  monitor:    { label: "Proactive Monitoring", icon: "radar",          comp: Monitor,
                title: "Proactive Monitoring", sub: "Always-on, shadow-first risk detection on the event stream." },
  l3:         { label: "L3 Console",          icon: "inbox",           comp: L3Workspace,
                title: "L3 Console",          sub: "Escalated cases worked by the L3 desk." },
  command:    { label: "Command Deck",        icon: "space_dashboard", comp: Command,
                title: "Command Deck",        sub: "Operational metrics across the resolution engine." },
  authoring:  { label: "Authoring Studio",    icon: "edit_note",       comp: AuthoringStudio,
                title: "Authoring Studio",    sub: "Author the engine's brain in plain language." },
  auditing:   { label: "Auditing Studio",     icon: "fact_check",      comp: AuditingStudio,
                title: "Auditing Studio",     sub: "LLM-judge scoring, the audit trail, and the learning queue." },
  governance: { label: "Governance",          icon: "gavel",           comp: GovernanceFramework,
                title: "Governance",          sub: "The dynamic, fully editable governance model." },
  concernlog: { label: "Concern Log",         icon: "receipt_long",    comp: Ledger,
                title: "Concern Log",         sub: "Append-only log of every concern + its resolution trace." },
};
const GROUP_1 = ["captain", "trace", "monitor", "l3"];
const GROUP_2 = ["command", "authoring", "auditing", "governance", "concernlog"];

// Nav context — some pages (e.g. Monitor) call useNav() to jump views. Exposes setView.
const NavCtx = createContext(() => {});
export const useNav = () => useContext(NavCtx);

const ROLE_TONE = {
  approver: "text-tertiary bg-tertiary/10 border-tertiary/30",
  author: "text-secondary-container bg-secondary-container/10 border-secondary-container/30",
  viewer: "text-on-surface-variant bg-surface-variant/40 border-on-primary-fixed-variant/20",
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

  async function submit(e) {
    e.preventDefault();
    if (busy || !email.trim() || !password) return;
    setBusy(true); setErr("");
    try { await login(email.trim(), password); }
    catch (ex) { setErr(ex.message || "Login failed"); setBusy(false); }
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

        <button type="submit" disabled={busy || !email.trim() || !password}
          className="w-full bg-secondary-container text-on-secondary py-sm rounded-lg font-bold text-sm flex items-center justify-center gap-2 hover:brightness-110 disabled:opacity-50 transition-all">
          <span className="material-symbols-outlined" style={{ fontSize: 18 }}>{busy ? "progress_activity" : "login"}</span>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="text-[10px] text-on-surface-variant/70 mt-md text-center leading-relaxed">
          Access is role-based. Ask an approver on your team to create your account.
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
  const [form, setForm] = useState({ email: "", name: "", role: "author", password: "" });
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => listUsers().then((d) => setUsers(d.users || [])).catch(() => {});
  useEffect(() => { load(); }, []);

  async function add(e) {
    e.preventDefault();
    if (busy || !form.email.trim() || !form.password) return;
    setBusy(true); setErr(""); setOk("");
    try {
      const r = await createUser({ ...form, email: form.email.trim() });
      if (r.ok) { setOk(`Added ${r.user.email} (${r.user.role}).`); setForm({ email: "", name: "", role: "author", password: "" }); load(); }
      else setErr(r.detail || "Could not create user");
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
                  <span className={`text-[10px] font-bold px-2 py-0.5 rounded border ${ROLE_TONE[u.role] || ROLE_TONE.viewer}`}>{u.role}</span>
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
              <input value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} placeholder="temp password" type="text"
                className="bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded-lg px-sm py-1.5 text-[13px] focus:outline-none focus:border-secondary-container placeholder:text-on-surface-variant/40" />
              <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}
                className="bg-surface-container-lowest border border-on-primary-fixed-variant/20 rounded-lg px-sm py-1.5 text-[13px] focus:outline-none focus:border-secondary-container" style={{ fontFamily: "JetBrains Mono" }}>
                {ADMIN_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
            </div>
            {err && <div className="text-xs text-error mb-sm flex items-center gap-1"><span className="material-symbols-outlined" style={{ fontSize: 15 }}>error</span>{err}</div>}
            {ok && <div className="text-xs text-tertiary mb-sm flex items-center gap-1"><span className="material-symbols-outlined" style={{ fontSize: 15 }}>check_circle</span>{ok}</div>}
            <button type="submit" disabled={busy || !form.email.trim() || !form.password}
              className="bg-secondary-container text-on-secondary px-lg py-sm rounded-lg font-bold text-sm flex items-center gap-2 hover:brightness-110 disabled:opacity-50 transition-all">
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>person_add</span>Add member
            </button>
            <p className="text-[10px] text-on-surface-variant/70 mt-sm leading-relaxed">
              Roles: <b>viewer</b> reads only · <b>author</b> can draft/queue knowledge · <b>approver</b> can make things go live and manage the team.
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
      <ChatStoreProvider>
        {/* background: animated field + faint grid, behind the shell (z 0 / 1; .app is z 2) */}
        <Shader opacity={0.16} />
        <div className="fixed inset-0 z-[1] pointer-events-none"
          style={{ backgroundImage: "linear-gradient(rgba(65,71,83,0.05) 1px,transparent 1px),linear-gradient(90deg,rgba(65,71,83,0.05) 1px,transparent 1px)", backgroundSize: "46px 46px" }} />

        <div className="app">
          {/* ── SIDEBAR ── */}
          <aside className="sidebar">
            <div className="brand">
              <span className="logo">Valmo<em> Advocate</em></span>
              <span className="tag">command center</span>
            </div>

            {GROUP_1.map((k) => <NavItem key={k} k={k} />)}
            <div className="nav-sep">Support Command</div>
            {GROUP_2.map((k) => <NavItem key={k} k={k} />)}

            {/* ── FOOT: signed-in user · role · logout · (approver) team · health ── */}
            <div className="sidebar-foot">
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ color: "var(--text-mute)", fontSize: 11.5, fontWeight: 600,
                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: 128 }}
                    title={user.name || user.email}>{user.name || user.email}</div>
                  <span className={`inline-block mt-1 text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded border ${ROLE_TONE[user.role] || ROLE_TONE.viewer}`}>
                    {user.role}</span>
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
    </NavCtx.Provider>
  );
}
