import React from "react";

// A production resolution platform must NEVER show a captain a black screen. This boundary
// catches any render/effect throw in the wrapped panel, keeps the top bar + state switcher
// alive, and offers a one-click recovery — while logging the real error for us.
// Wrap each panel with a `key` (the state name) so navigating away resets a crashed panel.
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    // Surfaced in the browser console with the component stack for diagnosis.
    console.error("[Valmo] panel render crash:", error, info?.componentStack);
    this.setState({ info });
  }

  render() {
    if (!this.state.error) return this.props.children;
    const msg = String(this.state.error?.message || this.state.error || "Unknown error");
    return (
      <div style={{ height: "100%", display: "grid", placeItems: "center", padding: 24 }}>
        <div style={{ maxWidth: 520, width: "100%", background: "var(--surface-2, #201f20)",
          border: "1px solid var(--line, #35343536)", borderRadius: 14, padding: "22px 24px",
          fontFamily: "system-ui, sans-serif" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
            <span style={{ fontSize: 22 }}>⚠️</span>
            <div style={{ color: "var(--text, #e5e5e7)", fontWeight: 700, fontSize: 15 }}>
              This panel hit a snag
            </div>
          </div>
          <div style={{ color: "var(--text-mute, #a8a8ad)", fontSize: 13, lineHeight: 1.5 }}>
            The rest of the platform is fine — your open cases are safe. Reopen this panel or reload.
          </div>
          <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
            <button onClick={() => this.setState({ error: null, info: null })}
              style={{ background: "var(--signal, #00f1fe)", color: "#001b1c", border: "none",
                borderRadius: 8, padding: "8px 16px", fontWeight: 700, fontSize: 13, cursor: "pointer" }}>
              Try again
            </button>
            <button onClick={() => window.location.reload()}
              style={{ background: "transparent", color: "var(--text-mute, #a8a8ad)",
                border: "1px solid var(--line, #444)", borderRadius: 8, padding: "8px 16px",
                fontSize: 13, cursor: "pointer" }}>
              Reload
            </button>
          </div>
          <details style={{ marginTop: 16 }}>
            <summary style={{ color: "var(--text-faint, #6b6b70)", fontSize: 11, cursor: "pointer",
              fontFamily: "var(--mono, monospace)" }}>technical details</summary>
            <pre style={{ marginTop: 8, color: "var(--text-faint, #6b6b70)", fontSize: 10.5,
              whiteSpace: "pre-wrap", wordBreak: "break-word", fontFamily: "var(--mono, monospace)" }}>{msg}</pre>
          </details>
        </div>
      </div>
    );
  }
}
