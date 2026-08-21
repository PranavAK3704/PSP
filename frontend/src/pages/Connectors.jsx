import { useEffect, useState } from "react";
import { Plug, ShieldOff, FileJson, Radio, ChevronRight } from "lucide-react";
import { getConnectors } from "../lib/api.js";

/* ── Connector registry ───────────────────────────────────────────────────────────────────
   This is the ask slide, not plumbing. It answers "what would it take to run on real data?"
   with a list rather than an argument.

   The paths are copied verbatim from the captain panel's own route constants, so the claim is
   not "these endpoints could exist" — it is "the app the captain has open is calling them right
   now, authenticated as the partner." Which makes the remaining question narrow: which adapter,
   and which env var.

   Nothing on this screen triggers a call. The backend builds it from a static table. ── */

const STATUS = {
  live:    { label: "live",     icon: Radio,     tone: "var(--c-teal)",
             blurb: "PSP calls this today" },
  fixture: { label: "fixture",  icon: FileJson,  tone: "var(--c-amber)",
             blurb: "reads a file shaped to the real contract — one env var flips it" },
  none:    { label: "no adapter", icon: ShieldOff, tone: "var(--c-rose)",
             blurb: "not wired; named so the gap is visible rather than implied" },
};

function Pill({ status }) {
  const s = STATUS[status] || STATUS.none;
  const Icon = s.icon;
  return (
    <span className="tag mono" style={{ color: s.tone, borderColor: s.tone, fontSize: 9.5,
      display: "inline-flex", alignItems: "center", gap: 4 }}>
      <Icon size={9} />{s.label}
    </span>
  );
}

function Group({ g }) {
  const [open, setOpen] = useState(g.status === "fixture" && g.count <= 4);
  return (
    <div className="card">
      <div className="card-head" style={{ cursor: "pointer" }} onClick={() => setOpen((v) => !v)}>
        <h3 style={{ display: "flex", alignItems: "center", gap: 7 }}>
          <ChevronRight size={13} style={{ transform: open ? "rotate(90deg)" : "none",
            transition: "transform .15s" }} />
          {g.queue}
        </h3>
        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span className="mono" style={{ fontSize: 9.5, color: "var(--text-faint)" }}>
            {g.count} endpoint{g.count === 1 ? "" : "s"}
          </span>
          <Pill status={g.status} />
        </span>
      </div>
      <div style={{ padding: "11px 15px" }}>
        <div className="df-note" style={{ border: "none", padding: 0, marginBottom: 9 }}>
          {g.note}
        </div>
        <div className="df-row" style={{ gridTemplateColumns: "minmax(90px,auto) 1fr", padding: "3px 0" }}>
          <span className="k">service</span><span className="v" style={{ textAlign: "left" }}>{g.service}</span>
        </div>
        <div className="df-row" style={{ gridTemplateColumns: "minmax(90px,auto) 1fr", padding: "3px 0" }}>
          <span className="k">adapter</span><span className="v" style={{ textAlign: "left" }}>{g.adapter}</span>
        </div>
        <div className="df-row" style={{ gridTemplateColumns: "minmax(90px,auto) 1fr", padding: "3px 0" }}>
          <span className="k">go live</span>
          <span className="v" style={{ textAlign: "left",
            color: g.env === "—" ? "var(--text-faint)" : "var(--c-teal)" }}>{g.env}</span>
        </div>
        {g.detail && (
          <div className="df-note" style={{ border: "none", padding: "7px 0 0", color: "var(--c-amber)" }}>
            {g.detail}
          </div>
        )}
        {open && (
          <div style={{ marginTop: 10, borderTop: "1px solid var(--line-soft)", paddingTop: 8 }}>
            {g.routes.map((r) => (
              <div key={r.name} className="df-row" style={{ gridTemplateColumns: "1fr auto",
                padding: "3px 0" }}>
                <span className="k mono" style={{ color: "var(--text-mute)", fontSize: 10.5 }}>
                  {r.path}
                </span>
                <span className="v" style={{ color: "var(--text-faint)", fontSize: 10 }}>
                  {r.timeout_ms}ms
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function Connectors() {
  const [d, setD] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { getConnectors().then(setD).catch(() => setErr("Could not read the registry.")); }, []);

  if (err) return <div className="df-note" style={{ color: "var(--warn)" }}>{err}</div>;
  if (!d) return <div className="mono" style={{ fontSize: 12, color: "var(--text-faint)", padding: 18 }}>
    Reading the connector registry…</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 13 }}>
      <div className="df-tiles">
        {Object.entries(d.by_status).map(([k, n]) => {
          const s = STATUS[k] || STATUS.none;
          return (
            <div key={k} className="df-tile" style={{ borderTop: `2px solid ${s.tone}` }}>
              <div className="lab">{s.label}</div>
              <div className="val" style={{ color: s.tone }}>{n}</div>
              <div className="sub">{s.blurb}</div>
            </div>
          );
        })}
        <div className="df-tile">
          <div className="lab"><Plug size={11} />total endpoints</div>
          <div className="val">{d.total}</div>
          <div className="sub">already live in production</div>
        </div>
      </div>

      <div className="df-note">
        Sourced verbatim from <b style={{ color: "var(--text-mute)" }}>{d.source}</b>.
        {" "}{d.note}
        {d.engine && (
          <> Engine is on <b style={{ color: "var(--text-mute)" }}>{d.engine.account_provider}</b>
            {" "}with <b style={{ color: "var(--warn)" }}>WRITE_MODE={d.engine.write_mode}</b>.</>
        )}
      </div>

      <div className="df-grid">
        {d.groups.map((g) => <Group key={g.group} g={g} />)}
      </div>

      <div className="card">
        <div className="card-head"><h3>Outside the captain panel</h3></div>
        <div style={{ padding: "11px 15px", display: "flex", flexDirection: "column", gap: 9 }}>
          {d.other_services.map((o, i) => (
            <div key={i}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <Pill status={o.status} />
                <span className="mono" style={{ fontSize: 10.5, color: "var(--text-mute)" }}>{o.path}</span>
                <span className="tag mono" style={{ fontSize: 9 }}>{o.service}</span>
              </div>
              <div className="df-note" style={{ border: "none", padding: "3px 0 0" }}>{o.note}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
