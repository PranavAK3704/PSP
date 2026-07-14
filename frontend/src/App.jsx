import React, { createContext, useContext, useEffect, useState } from "react";
import CaptainPanel from "./pages/CaptainPanel.jsx";
import Monitor from "./pages/Monitor.jsx";
import L3Workspace from "./pages/L3Workspace.jsx";
import SupportCommand from "./pages/SupportCommand.jsx";
import Shader from "./components/Shader.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { ChatStoreProvider } from "./lib/chatStore.jsx";
import { getHealth, listUsers, createUser } from "./lib/api.js";
import { AuthProvider, useAuth } from "./lib/auth.jsx";

const STATES = {
  captain: { label: "Captain Advocate", icon: "forum", comp: CaptainPanel },
  monitor: { label: "Proactive Monitoring", icon: "radar", comp: Monitor },
  l3: { label: "L3 Console", icon: "inbox", comp: L3Workspace },
  support: { label: "Support Command", icon: "space_dashboard", comp: SupportCommand },
};

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
   SHELL — the authenticated app (was App). Header carries user · role · logout.
   ══════════════════════════════════════════════════════════════════════════ */
function Shell() {
  const { user, isApprover, logout } = useAuth();
  const [state, setState] = useState("captain");
  const [health, setHealth] = useState(null);
  const [teamOpen, setTeamOpen] = useState(false);
  useEffect(() => { getHealth().then(setHealth).catch(() => setHealth({ down: true })); }, []);
  const Comp = STATES[state].comp;

  return (
    <NavCtx.Provider value={setState}>
      <ChatStoreProvider>
      <Shader opacity={0.16} />
      <div className="fixed inset-0 z-[1] pointer-events-none"
        style={{ backgroundImage: "linear-gradient(rgba(65,71,83,0.05) 1px,transparent 1px),linear-gradient(90deg,rgba(65,71,83,0.05) 1px,transparent 1px)", backgroundSize: "46px 46px" }} />

      {/* Top bar: brand · glass state switcheroo · status + user */}
      <header className="fixed top-0 inset-x-0 z-50 h-16 px-lg flex items-center justify-between bg-surface/40 backdrop-blur-xl border-b border-on-primary-fixed-variant/20">
        <div className="flex items-center gap-2 w-56">
          <span className="material-symbols-outlined text-secondary-container" style={{ fontSize: 22 }}>security</span>
          <div className="hidden md:block">
            <div className="text-secondary-container font-bold leading-none">Valmo Advocate</div>
            <div className="text-[9px] text-on-surface-variant uppercase tracking-[0.2em] mt-0.5 opacity-60">Partner-support platform</div>
          </div>
        </div>

        {/* Glass switcheroo */}
        <div className="glass-card rounded-full p-1 flex items-center gap-1 shadow-lg">
          {Object.entries(STATES).map(([k, v]) => {
            const active = state === k;
            return (
              <button key={k} onClick={() => setState(k)}
                className={`relative flex items-center gap-2 px-3 lg:px-4 py-2 rounded-full text-sm font-semibold transition-all duration-300 ${
                  active ? "bg-secondary-container text-on-secondary shadow-[0_0_20px_-4px_#4d8eff]"
                         : "text-on-surface-variant hover:text-secondary-container hover:bg-surface-variant/30"}`}>
                <span className="material-symbols-outlined" style={{ fontSize: 18 }}>{v.icon}</span>
                <span className="hidden lg:block">{v.label}</span>
              </button>
            );
          })}
        </div>

        <div className="flex items-center gap-sm justify-end">
          <div className="hidden xl:flex items-center gap-1.5 text-[10px] px-3 py-1 rounded-full" style={{ fontFamily: "JetBrains Mono" }}>
            <span className={`w-1.5 h-1.5 rounded-full ${health?.down ? "bg-error" : "bg-tertiary"}`}
              style={{ boxShadow: health?.down ? "0 0 8px #ffb4ab" : "0 0 8px #4edea3" }} />
            <span className="text-on-surface-variant">{health?.down ? "offline" : `${health?.provider || "…"} · tiered`}</span>
          </div>

          {/* user · role pill */}
          <div className="hidden sm:flex flex-col items-end leading-none">
            <span className="text-[12px] font-semibold text-on-surface max-w-[140px] truncate">{user.name || user.email}</span>
            <span className={`mt-0.5 text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded border ${ROLE_TONE[user.role] || ROLE_TONE.viewer}`}>{user.role}</span>
          </div>

          {/* team admin (approver only) */}
          {isApprover && (
            <button onClick={() => setTeamOpen(true)} title="Team access"
              className="w-9 h-9 rounded-full bg-secondary-container/10 border border-secondary-container/30 grid place-items-center text-secondary-container hover:bg-secondary-container/20 transition-all">
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>group</span>
            </button>
          )}

          {/* logout */}
          <button onClick={logout} title="Sign out"
            className="w-9 h-9 rounded-full bg-surface-variant/30 border border-on-primary-fixed-variant/20 grid place-items-center text-on-surface-variant hover:text-error hover:border-error/40 transition-all">
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>logout</span>
          </button>
        </div>
      </header>

      <main className="mt-16 h-[calc(100vh-4rem)] overflow-y-auto custom-scrollbar p-gutter relative z-10">
        {/* keyed per state → navigating to another panel resets a crashed one; the top bar stays alive */}
        <ErrorBoundary key={state}>
          <Comp />
        </ErrorBoundary>
      </main>

      {teamOpen && <TeamAdmin onClose={() => setTeamOpen(false)} />}
      </ChatStoreProvider>
    </NavCtx.Provider>
  );
}
