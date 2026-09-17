/* Intake — the ticket register.
 *
 * Every row here came from a partner writing in a channel, not from anyone filling a form. The
 * pipeline read the message, decided it was an issue, pulled the identifiers out and created
 * this. An agent's job on this page is to move it along and, when it matters, write a line the
 * raiser will actually see.
 *
 * TWO THINGS ON THIS PAGE ARE EASY TO MISREAD, so both are labelled in the UI:
 *
 *   · "raised N×" is RECURRENCE, not duplication. The same issue reported seven times is one
 *     ticket carrying a 7 — not seven tickets, and not six suppressed. The count is the signal;
 *     losing it was the old failure.
 *   · A note written here is shown to the PARTNER on their status page. It is not an internal
 *     comment, and the placeholder says so, because discovering that afterwards is expensive.
 */
import { useEffect, useMemo, useState } from "react";
import { getIntakeChannels, getIntakeTickets, updateIntakeTicket } from "../lib/api.js";

const STATES = [
  { k: "open",        label: "Open",            tone: "text-primary bg-primary/10 border-primary/30" },
  { k: "in_progress", label: "Being worked on", tone: "text-tertiary bg-tertiary/10 border-tertiary/30" },
  { k: "resolved",    label: "Resolved",        tone: "text-secondary-container bg-secondary-container/10 border-secondary-container/30" },
];
const toneOf = (s) => (STATES.find((x) => x.k === s) || STATES[0]).tone;
const labelOf = (s) => (STATES.find((x) => x.k === s) || STATES[0]).label;

export default function Intake() {
  const [data, setData] = useState({ tickets: [], counts: {}, total: 0 });
  const [filter, setFilter] = useState("");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(null);      // ref of the expanded row
  const [chans, setChans] = useState({ channels: [], updated_at: null });

  useEffect(() => { getIntakeChannels().then(setChans).catch(() => {}); }, []);

  async function load() {
    try {
      setData(await getIntakeTickets({ state: filter, q: q.trim() }));
      setErr("");
    } catch (e) {
      setErr(String(e.message || e));
    }
  }

  // Reload on filter/search. Debounced so typing does not fire a request per keystroke.
  useEffect(() => {
    const t = setTimeout(load, q ? 250 : 0);
    return () => clearTimeout(t);
  }, [filter, q]);

  async function patch(ref, body) {
    setBusy(ref);
    try {
      await updateIntakeTicket(ref, body);
      await load();
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy("");
    }
  }

  const counts = data.counts || {};
  const tiles = useMemo(() => ([
    ["all", data.total || 0, "tickets"],
    ["open", counts.open || 0, "open"],
    ["in_progress", counts.in_progress || 0, "being worked on"],
    ["resolved", counts.resolved || 0, "resolved"],
  ]), [data]);

  return (
    <div className="space-y-lg">
      {/* ── LISTENING CHANNELS ───────────────────────────────────────────────────────────
          A channel that is connected but silent looks exactly like one that is broken, and
          both look like one the gate excluded — all three produce no tickets. So every row
          shows what arrived, what came of it, and the gate's reason when it excluded one. */}
      <div>
        <div className="flex items-baseline gap-sm mb-sm">
          <h3 className="text-[11px] uppercase tracking-wide text-on-surface-variant">
            Listening on
          </h3>
          {chans.updated_at && (
            <span className="text-[10px] text-on-surface-variant/70">
              as of {String(chans.updated_at).slice(0, 16).replace("T", " ")}
            </span>
          )}
        </div>
        {!chans.channels.length ? (
          <div className="rounded-lg border border-dashed border-on-primary-fixed-variant/20 p-md text-center text-[12px] text-on-surface-variant">
            No channel has reported yet — the pipeline sends this when it runs.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-md">
            {chans.channels.map((c) => {
              const excluded = c.qualified === false;
              const quiet = !excluded && !c.messages;
              const dot = excluded ? "bg-tertiary" : quiet ? "bg-on-surface-variant/40" : "bg-primary";
              return (
                <div key={c.channel_id}
                  className={`rounded-lg border p-md ${excluded
                    ? "border-tertiary/30 bg-tertiary/5"
                    : "border-on-primary-fixed-variant/15"}`}>
                  <div className="flex items-center gap-sm">
                    <span className={`inline-block w-[7px] h-[7px] rounded-full ${dot}`} />
                    <span className="text-sm font-semibold truncate">#{c.name}</span>
                    {c.last_at && (
                      <span className="ml-auto text-[10px] text-on-surface-variant">
                        {String(c.last_at).slice(11, 16)}
                      </span>
                    )}
                  </div>
                  <div className="mt-sm flex gap-md text-[11px] text-on-surface-variant">
                    <span><b className="text-on-surface">{c.messages || 0}</b> messages</span>
                    <span><b className="text-on-surface">{c.issues || 0}</b> issues</span>
                    <span><b className="text-on-surface">{c.tickets || 0}</b> tickets</span>
                  </div>
                  {(c.gated || c.not_an_issue) ? (
                    <div className="mt-1 text-[10px] text-on-surface-variant/70">
                      {c.gated || 0} gated · {c.not_an_issue || 0} not an issue
                    </div>
                  ) : null}
                  {excluded && (
                    <div className="mt-sm text-[10px] text-tertiary leading-relaxed">
                      excluded by the qualification gate — {String(c.reason || "").slice(0, 110)}
                    </div>
                  )}
                  {quiet && (
                    <div className="mt-sm text-[10px] text-on-surface-variant/70">
                      connected, nothing yet
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-md">
        {tiles.map(([k, n, label]) => (
          <button key={k} onClick={() => setFilter(k === "all" ? "" : k)}
            className={`text-left rounded-lg border p-md transition ${
              (k === "all" ? filter === "" : filter === k)
                ? "border-primary/50 bg-primary/5" : "border-on-primary-fixed-variant/15"}`}>
            <div className="text-2xl font-semibold">{n}</div>
            <div className="text-[11px] uppercase tracking-wide text-on-surface-variant">{label}</div>
          </button>
        ))}
      </div>

      <input value={q} onChange={(e) => setQ(e.target.value)}
        placeholder="Search title, DC code, raiser, category…"
        className="w-full rounded-md border border-on-primary-fixed-variant/20 bg-surface px-md py-sm text-sm" />

      {err && <div className="rounded-md border border-error/40 bg-error/10 p-md text-sm">{err}</div>}

      {!data.tickets.length && !err && (
        <div className="rounded-lg border border-dashed border-on-primary-fixed-variant/20 p-xl text-center text-sm text-on-surface-variant">
          Nothing here yet. Tickets appear as the pipeline reads the listening channels.
        </div>
      )}

      <div className="space-y-sm">
        {data.tickets.map((t) => (
          <div key={t.ref} className="rounded-lg border border-on-primary-fixed-variant/15 bg-surface p-md">
            <div className="flex items-start gap-sm flex-wrap">
              <span className="font-mono text-[11px] text-on-surface-variant pt-1">{t.ref}</span>
              <span className={`text-[10px] font-bold px-2 py-0.5 rounded border ${toneOf(t.state)}`}>
                {labelOf(t.state)}
              </span>
              {t.occurrence_count > 1 && (
                /* Recurrence, not duplication — spelled out because "7" alone reads as a bug. */
                <span className="text-[10px] font-bold px-2 py-0.5 rounded border text-tertiary bg-tertiary/10 border-tertiary/30"
                  title="The same issue raised this many times. One ticket, not several.">
                  raised {t.occurrence_count}×
                </span>
              )}
              {t.dc_code && <span className="text-[10px] px-2 py-0.5 rounded border border-on-primary-fixed-variant/20">{t.dc_code}</span>}
              <span className="text-[10px] px-2 py-0.5 rounded border border-on-primary-fixed-variant/20 text-on-surface-variant">
                {t.disposition || "uncategorised"}
              </span>
              <button onClick={() => setOpen(open === t.ref ? null : t.ref)}
                className="ml-auto text-[11px] text-on-surface-variant hover:text-on-surface">
                {open === t.ref ? "less" : "more"}
              </button>
            </div>

            <div className="mt-sm text-sm leading-relaxed">{t.title}</div>
            <div className="mt-1 text-[11px] text-on-surface-variant">
              {t.raiser || "unknown"} · {(t.first_raised_at || "").slice(0, 16)}
              {t.permalink && <> · <a href={t.permalink} target="_blank" rel="noreferrer"
                className="underline hover:text-primary">open in Slack</a></>}
            </div>

            {open === t.ref && (
              <div className="mt-md space-y-sm border-t border-on-primary-fixed-variant/10 pt-md">
                {t.description && (
                  <pre className="whitespace-pre-wrap text-[12px] text-on-surface-variant font-sans">{t.description}</pre>
                )}
                {!!(t.flags || []).length && (
                  <div className="text-[11px] text-tertiary">flags: {t.flags.join("; ")}</div>
                )}
                <div className="flex gap-sm flex-wrap items-center">
                  {STATES.map((s) => (
                    <button key={s.k} disabled={busy === t.ref || t.state === s.k}
                      onClick={() => patch(t.ref, { state: s.k })}
                      className={`text-[11px] px-3 py-1 rounded border disabled:opacity-40 ${s.tone}`}>
                      {s.label}
                    </button>
                  ))}
                </div>
                <NoteBox ticket={t} busy={busy === t.ref}
                  onSave={(note) => patch(t.ref, { status_note: note })} />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function NoteBox({ ticket, busy, onSave }) {
  const [note, setNote] = useState(ticket.status_note || "");
  useEffect(() => { setNote(ticket.status_note || ""); }, [ticket.ref, ticket.status_note]);
  return (
    <div>
      <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2}
        /* The placeholder carries the warning: this text leaves the building. */
        placeholder="Update for the partner — they see this on their status page. Not an internal note."
        className="w-full rounded-md border border-on-primary-fixed-variant/20 bg-surface px-md py-sm text-[12px]" />
      <div className="flex items-center gap-sm mt-1">
        <button disabled={busy || note === (ticket.status_note || "")} onClick={() => onSave(note)}
          className="text-[11px] px-3 py-1 rounded border border-primary/40 text-primary disabled:opacity-40">
          Save update
        </button>
        <span className="text-[10px] text-on-surface-variant">shown to the person who raised it</span>
      </div>
    </div>
  );
}
