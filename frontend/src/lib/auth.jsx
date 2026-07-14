import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { getMe, getToken, clearToken, login as apiLogin, logout as apiLogout } from "./api.js";

// Client-side session state. The SERVER is the source of truth for every gated
// action — this context only drives UX (which screen, which buttons to show).
const AuthCtx = createContext(null);
export const useAuth = () => useContext(AuthCtx);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);

  const check = useCallback(async () => {
    if (!getToken()) { setUser(null); setReady(true); return; }
    try { setUser(await getMe()); }
    catch { clearToken(); setUser(null); }
    setReady(true);
  }, []);

  useEffect(() => { check(); }, [check]);

  // api.js dispatches this on any 401 → drop back to the login screen.
  useEffect(() => {
    const onUnauthed = () => setUser(null);
    window.addEventListener("valmo-unauthed", onUnauthed);
    return () => window.removeEventListener("valmo-unauthed", onUnauthed);
  }, []);

  const login = async (email, password) => {
    const data = await apiLogin(email, password);   // stores token, throws on bad creds
    setUser(data.user);
    return data.user;
  };
  const logout = () => { apiLogout(); setUser(null); };

  const role = user?.role || null;
  const value = {
    user, role,
    isApprover: role === "approver",
    isAuthor: role === "author" || role === "approver",   // approver ≥ author
    login, logout,
  };

  if (!ready) {
    return (
      <div className="fixed inset-0 grid place-items-center bg-background">
        <div className="flex items-center gap-3 text-on-surface-variant">
          <span className="material-symbols-outlined animate-spin text-secondary-container" style={{ fontSize: 22 }}>progress_activity</span>
          <span className="text-sm" style={{ fontFamily: "JetBrains Mono" }}>authenticating…</span>
        </div>
      </div>
    );
  }
  return <AuthCtx.Provider value={value}>{children}</AuthCtx.Provider>;
}
