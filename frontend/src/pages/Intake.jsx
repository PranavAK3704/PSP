/* Intake — the ticket register, as a working queue rather than a list.
 *
 * Every row came from a partner writing in a channel, not from anyone filling a form. The
 * pipeline read the message, decided it was an issue, pulled the identifiers out and created
 * this. An agent's job here is to pick the next one, move it along, and — when it matters —
 * write a line the raiser will actually see.
 *
 * ── THE BIFURCATION IS THE POINT ──────────────────────────────────────────────────────────
 * A flat list of tickets is unreadable because it hides the only question an agent has: what
 * needs me next. The queue is split into four, and the FIRST group is not a state at all:
 *
 *   NEEDS A CATEGORY  the matcher refused to classify it. That is a question addressed to a
 *                     human, not a category — rendering "NOVEL" as though it were one is what
 *                     made the first version of this page meaningless.
 *   OPEN              categorised, waiting for someone.
 *   BEING WORKED ON   somebody has it.
 *   RESOLVED          collapsed by default; it is history, not work.
 *
 * ── TWO THINGS ARE EASY TO MISREAD, so both are labelled in the UI ────────────────────────
 *   · "raised 7×" is RECURRENCE, not duplication. One ticket carrying a 7 — not seven tickets.
 *   · A note written here is shown to the PARTNER on their status page. It is not an internal
 *     comment, and the field says so, because discovering that afterwards is expensive.
 */
import { useEffect, useMemo, useState } from "react";
import { getIntakeChannels, getIntakeTickets, updateIntakeTicket } from "../lib/api.js";

const STATES = [
  { k: "open", label: "Open" },
  { k: "in_progress", label: "Being worked on" },
  { k: "resolved", label: "Resolved" },
];

const MONO = { fontFamily: "JetBrains Mono", fontVariantNumeric: "tabular-nums" };

function StatePill({ state, needsCategory }) {
  if (needsCategory) {
    return (
      <span className="inline-flex items-center rounded-full px-2.5 py-1 text-[10px] font-bold tracking-wide bg-warn/10 text-warn border border-warn/40"
        style={MONO}>NEEDS A CATEGORY</span>
    );
  }
  const tone = state === "resolved"
    ? "bg-secondary-container/10 text-secondary-container border-secondary-container/40"
    : state === "in_progress"
      ? "bg-tertiary/10 text-tertiary border-tertiary/40"
      : "bg-warn/10 text-warn border-warn/40";
  const label = (STATES.find((s) => s.k === state) || STATES[0]).label;
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-[10px] font-bold tracking-wide border ${tone}`}
      style={MONO}>{label.toUpperCase()}</span>
  );
}

export default function Intake() {
  const [data, setData] = useState({ tickets: [], counts: {}, total: 0 });
  const [chans, setChans] = useState({ channels: [], updated_at: null });
  const [selRef, setSelRef] = useState(null);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [showResolved, setShowResolved] = useState(false);

  async function load(keepSel = true) {
    try {
      const d = await getIntakeTickets({ q: q.trim() });
      setData(d);
      setErr("");
      setSelRef((cur) => (keepSel && d.tickets.some((t) => t.ref === cur)
        ? cur : (d.tickets[0]?.ref ?? null)));
    } catch (e) { setErr(String(e.message || e)); }
  }

  useEffect(() => { getIntakeChannels().then(setChans).catch(() => {}); }, []);
  useEffect(() => {
    const t = setTimeout(() => load(false), q ? 250 : 0);
    return () => clearTimeout(t);
  }, [q]);
  // A ticket created while someone is looking at this page should ARRIVE, not wait for a reload.
  useEffect(() => {
    const t = setInterval(() => load(true), 8000);
    return () => clearInterval(t);
  }, [q]);

  async function patch(ref, body) {
    setBusy(ref);
    try { await updateIntakeTicket(ref, body); await load(true); }
    catch (e) { setErr(String(e.message || e)); }
    finally { setBusy(""); }
  }

  // `NOVEL` is the matcher declining to answer, so those tickets are their own group — ahead of
  // everything else, because a human decision unblocks them and nothing else will.
  const groups = useMemo(() => {
    const needs = [], open = [], working = [], done = [];
    for (const t of data.tickets) {
      const novel = !t.disposition || t.disposition === "NOVEL";
      if (t.state === "resolved") done.push(t);
      else if (t.state === "in_progress") working.push(t);
      else if (novel) needs.push(t);
      else open.push(t);
    }
    return [
      { key: "needs", label: "Needs a category", rows: needs, accent: "text-warn" },
      { key: "open", label: "Open", rows: open, accent: "text-on-surface" },
      { key: "working", label: "Being worked on", rows: working, accent: "text-tertiary" },
      { key: "done", label: "Resolved", rows: done, accent: "text-on-surface-variant",
        collapsible: true },
    ];
  }, [data]);

  const sel = data.tickets.find((t) => t.ref === selRef) || null;

  return (
    <div className="space-y-md">
      {/* ── LISTENING ON — one compact strip. A channel that is connected but silent looks
            exactly like one that is broken, and both look like one the gate excluded; all
            three produce zero tickets. So the counts are always on screen. ── */}
      <div className="flex items-center gap-md flex-wrap rounded-lg border border-on-primary-fixed-variant/15 bg-surface-container-lowest px-md py-sm">
        <span className="text-[10px] uppercase tracking-wide text-on-surface-variant">Listening on</span>
        {!chans.channels.length && (
          <span className="text-[11px] text-on-surface-variant/70">
            nothing reported yet — the pipeline sends this when it runs
          </span>
        )}
        {chans.channels.map((c) => {
          const excluded = c.qualified === false;
          const quiet = !excluded && !c.messages;
          return (
            <span key={c.channel_id} className="flex items-center gap-1.5 text-[11.5px]"
              title={excluded ? `Excluded: ${c.reason || ""}` : ""}>
              <span className={`inline-block w-[7px] h-[7px] rounded-full ${
                excluded ? "bg-tertiary" : quiet ? "bg-on-surface-variant/40" : "bg-secondary-container"}`} />
              <b className="text-on-surface">#{c.name}</b>
              <span className="text-on-surface-variant" style={MONO}>
                {c.messages || 0}m · {c.issues || 0}i · {c.tickets || 0}t
              </span>
              {excluded && <span className="text-tertiary text-[10px]">excluded</span>}
              {quiet && <span className="text-on-surface-variant/60 text-[10px]">quiet</span>}
            </span>
          );
        })}
        {chans.updated_at && (
          <span className="ml-auto text-[10px] text-on-surface-variant/60" style={MONO}>
            {String(chans.updated_at).slice(0, 16).replace("T", " ")}
          </span>
        )}
      </div>

      {err && <div className="rounded-md border border-error/40 bg-error/10 p-sm text-[12px]">{err}</div>}

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(300px,380px)_1fr] gap-md items-start">
        {/* ── THE QUEUE ── */}
        <div className="space-y-md">
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Search title, DC code, raiser…"
            className="w-full rounded-md border border-on-primary-fixed-variant/20 bg-surface-container-lowest px-md py-sm text-[13px]" />

          {groups.map((g) => {
            if (!g.rows.length) return null;
            const collapsed = g.collapsible && !showResolved;
            return (
              <div key={g.key}>
                <button
                  onClick={() => g.collapsible && setShowResolved((v) => !v)}
                  className={`w-full flex items-baseline gap-sm mb-1 ${g.collapsible ? "cursor-pointer" : "cursor-default"}`}>
                  <span className={`text-[10px] uppercase tracking-wide font-bold ${g.accent}`}>
                    {g.label}
                  </span>
                  <span className="text-[10px] text-on-surface-variant" style={MONO}>{g.rows.length}</span>
                  {g.collapsible && (
                    <span className="ml-auto text-[10px] text-on-surface-variant">
                      {collapsed ? "show" : "hide"}
                    </span>
                  )}
                </button>
                {!collapsed && (
                  <div className="space-y-1">
                    {g.rows.map((t) => {
                      const active = t.ref === selRef;
                      return (
                        <button key={t.ref} onClick={() => setSelRef(t.ref)}
                          className={`w-full text-left rounded-lg border px-md py-sm transition ${active
                            ? "border-secondary-container/60 bg-secondary-container/10"
                            : "border-on-primary-fixed-variant/12 bg-surface-container-lowest hover:border-on-primary-fixed-variant/30"}`}>
                          <div className="flex items-center gap-sm">
                            <span className="text-[10px] text-on-surface-variant" style={MONO}>{t.ref}</span>
                            {t.dc_code && (
                              <span className="text-[10px] px-1.5 rounded border border-on-primary-fixed-variant/25 text-on-surface-variant" style={MONO}>
                                {t.dc_code}
                              </span>
                            )}
                            {t.occurrence_count > 1 && (
                              <span className="text-[10px] font-bold text-tertiary" title="Raised this many times — one ticket, not several">
                                ×{t.occurrence_count}
                              </span>
                            )}
                            <span className="ml-auto text-[10px] text-on-surface-variant/70" style={MONO}>
                              {String(t.first_raised_at || "").slice(5, 10)}
                            </span>
                          </div>
                          <div className="mt-1 text-[13px] leading-snug line-clamp-2">{t.title}</div>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}

          {!data.tickets.length && (
            <div className="rounded-lg border border-dashed border-on-primary-fixed-variant/20 p-lg text-center text-[12.5px] text-on-surface-variant">
              Nothing yet. Tickets appear as the pipeline reads the listening channels.
            </div>
          )}
        </div>

        {/* ── THE SELECTED TICKET ── */}
        {sel ? <Detail t={sel} busy={busy === sel.ref} onPatch={patch} /> : (
          <div className="rounded-lg border border-dashed border-on-primary-fixed-variant/20 p-xl text-center text-[12.5px] text-on-surface-variant">
            Pick a ticket.
          </div>
        )}
      </div>
    </div>
  );
}

function Detail({ t, busy, onPatch }) {
  const [note, setNote] = useState(t.status_note || "");
  useEffect(() => { setNote(t.status_note || ""); }, [t.ref, t.status_note]);
  const novel = !t.disposition || t.disposition === "NOVEL";
  const ents = Object.entries(t.entities || {}).filter(([, v]) => (v || []).length);

  return (
    <div className="rounded-lg border border-on-primary-fixed-variant/15 bg-surface-container-lowest p-lg space-y-md">
      <div className="flex items-center gap-sm flex-wrap">
        <span className="text-[11px] text-on-surface-variant" style={MONO}>{t.ref}</span>
        <StatePill state={t.state} needsCategory={novel && t.state !== "resolved"} />
        {!novel && (
          <span className="text-[10px] px-2 py-0.5 rounded border border-on-primary-fixed-variant/25 text-on-surface-variant">
            {t.disposition}
          </span>
        )}
        {t.occurrence_count > 1 && (
          <span className="text-[10px] font-bold px-2 py-0.5 rounded border bg-tertiary/10 text-tertiary border-tertiary/40"
            title="The same issue raised this many times. One ticket, not several.">
            raised {t.occurrence_count}× — counted, not duplicated
          </span>
        )}
      </div>

      <h2 className="text-[17px] leading-snug font-semibold">{t.title}</h2>

      <div className="text-[11.5px] text-on-surface-variant flex gap-md flex-wrap">
        <span>{t.raiser || "unknown"}</span>
        <span style={MONO}>{String(t.first_raised_at || "").slice(0, 16).replace("T", " ")}</span>
        {t.reply_count > 0 && <span>{t.reply_count} repl{t.reply_count === 1 ? "y" : "ies"}</span>}
        {t.permalink && (
          <a href={t.permalink} target="_blank" rel="noreferrer"
            className="underline hover:text-secondary-container">open in Slack</a>
        )}
      </div>

      {t.description && t.description !== t.title && (
        <pre className="whitespace-pre-wrap font-sans text-[12.5px] leading-relaxed text-on-surface-variant border-l-2 border-on-primary-fixed-variant/20 pl-md">
          {t.description}
        </pre>
      )}

      {!!ents.length && (
        <div className="flex gap-sm flex-wrap">
          {ents.map(([kind, vals]) => (vals || []).map((v, i) => (
            <span key={`${kind}${i}`} className="text-[10px] px-2 py-0.5 rounded border border-secondary-container/40 bg-secondary-container/10 text-secondary-container"
              style={MONO}>{kind}: {typeof v === "object" ? v.value : v}</span>
          )))}
        </div>
      )}

      {!!(t.flags || []).length && (
        <div className="text-[11px] text-warn">flags: {t.flags.join(" · ")}</div>
      )}

      <div className="border-t border-on-primary-fixed-variant/10 pt-md space-y-sm">
        <div className="text-[10px] uppercase tracking-wide text-on-surface-variant">Move it along</div>
        <div className="flex gap-sm flex-wrap">
          {STATES.map((s) => (
            <button key={s.k} disabled={busy || t.state === s.k}
              onClick={() => onPatch(t.ref, { state: s.k })}
              className="text-[11.5px] px-3 py-1.5 rounded-md border border-on-primary-fixed-variant/25 hover:border-secondary-container/60 disabled:opacity-35 disabled:hover:border-on-primary-fixed-variant/25">
              {s.label}
            </button>
          ))}
        </div>
      </div>

      <div className="border-t border-on-primary-fixed-variant/10 pt-md">
        <div className="text-[10px] uppercase tracking-wide text-on-surface-variant mb-1">
          Update for the partner
        </div>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2}
          /* The label carries the warning: this text leaves the building. */
          placeholder="They see this on their status page — not an internal note."
          className="w-full rounded-md border border-on-primary-fixed-variant/20 bg-surface px-md py-sm text-[12.5px]" />
        <button disabled={busy || note === (t.status_note || "")}
          onClick={() => onPatch(t.ref, { status_note: note })}
          className="mt-1 text-[11.5px] px-3 py-1.5 rounded-md border border-secondary-container/50 text-secondary-container disabled:opacity-35">
          Save update
        </button>
      </div>
    </div>
  );
}
