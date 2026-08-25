import { useEffect, useMemo, useState } from "react";
import { Plug, Check, X, ChevronRight, Database, MessageSquare,
         TriangleAlert, ArrowRight } from "lucide-react";
import { getConnectors, getTickets } from "../lib/api.js";

/* ── "What can this system see?" ──────────────────────────────────────────────────────────────

   This page was 46 endpoint rows with paths, services, adapters and timeout values. Nobody
   reads that. It answered "what endpoints exist", which is a question one engineer asks once.

   The question a support person actually has is: **when a captain asks me X, can this thing see
   the data to answer it?** So the page is now one row per captain-question domain, in four
   columns that are the four states anything can be in:

       DISCOVERED     it exists, we found it, here is the path
       CONNECTED      PSP reads it today
       ANSWERABLE     a captain's question in this domain gets a real answer now
       WHAT IT TAKES  for the rest — the single specific thing needed, not "integration work"

   The endpoint detail moves behind a disclosure, for the one engineer who wants it. It is not
   deleted: the access ask is more credible with the paths attached than without them.

   ── AND THE NUMBERS ARE MEASURED, INCLUDING THE UNFLATTERING ONE ─────────────────────────────
   Ticket volumes come from the real Kapture export (140,875 tickets). The share-by-domain column
   is deliberately NOT a percentage of all tickets, because only ~1.2% of that export carries a
   sub-type at all — 1,750 of 140,875. That is itself worth showing: the incumbent system barely
   categorises what comes in, which is precisely why nobody can say what support spends its time
   on. Quoting a confident "26% payments" off a 1.2% sample would be the kind of number this
   project exists to stop producing.

   Previously this page also carried "already live in production" directly under
   `total endpoints = 46` while the status tiles beside it read 0 live / 31 fixture / 15 none.
   That sentence is gone.

   ── AND THE REGISTRY BEHIND IT WAS CORRECTED AGAINST THE SERVICE SOURCE ─────────────────────
   This page was built on a registry assembled from the captain panel's CLIENT-SIDE route table.
   Six service repositories were then read directly, and that table was wrong in three ways that
   would each have broken a real integration: every captain path was missing its `/api` prefix
   (including the two this page calls CONNECTED), service ownership was guessed and mostly wrong
   ("payouts-auto" is not a service that exists — the owner is `vetan`), and one catalogued
   endpoint does not exist at all. So the "what it takes" column below now names paths that
   would actually resolve. See backend/app/substrate/connectors.py for the full correction. ── */

const cx = (...a) => a.filter(Boolean).join(" ");

/* Domains, in the order a captain meets them. `question` is the phrasing from the real ticket
   corpus, not a category name — it is what makes the row readable by someone who has never seen
   the endpoint list. */
const DOMAINS = [
  { key: "losses", label: "Losses & debits",
    question: "“Rs 583 galat kata hai” — a debit I did not earn",
    queues: ["Losses & Debits · Cash/COD · Service area · Misroute", "Losses & Debits (prevention)"],
    answerable: true,
    have: "The real loss ledger — 1,000,001 attribution rows, per-AWB, joined to debits.",
    takes: null },
  { key: "orders", label: "Load & allocation",
    question: "“Mera load kam kyun hua” — why did my order volume drop",
    queues: ["Orders & Planning"],
    answerable: true,
    have: "Growth-dashboard contract, served from a fixture shaped to the live endpoint.",
    takes: "PSP_GROWTH_SOURCE=live — the adapter is written and the contract matches; this is one env var and a network allow-list." },
  { key: "payments", label: "Payments",
    question: "“Mera payment nahi aaya” — where is my money",
    queues: ["Payments"],
    answerable: false,
    have: "No adapter — but the owner is now known. `vetan` (\"Partner Payout compute and scheduler\") serves it, and it already composes the answer sentence itself: “Payment credited on {date} to {account}”, “Your payment has failed due to {reason}”, “Payment initiated on {date} — it will be credited in 2-3 days”.",
    takes: "Read access to vetan. The one endpoint that matters most is POST /api/v2/payments/details — credited-on timestamp, masked account, UTR and failure reason. GET /api/v2/earnings/current-cycle-details is the only endpoint anywhere that answers “payment kab aayega” rather than “what happened”." },
  { key: "capacity", label: "Capacity cuts",
    question: "“Capacity cut kyun laga” — why was my volume capped",
    queues: ["Orders & Planning (capacity)"],
    answerable: false,
    have: "No adapter. But ValmoLogisticsService's order-summary already returns a COMPOSED answer — banner_type “red_capacity_cut” with the sentence naming the cause and the date it lifts.",
    takes: "GET /api/v1/captain/hub-capacity/{hubId} — already serving the captain's own panel, so this is read access to what they can see themselves. Note: the WRITE counterpart often quoted alongside it, /v1/captain/dc-capacity/update, does not exist in the service; the real write is PUT on the same hub-capacity path." },
  // Split out of "capacity & pendency". Lumping them together made pendency look further away
  // than it is: it has a DIRECT endpoint match, already catalogued in PSP's own registry, and the
  // data-discovery table for gold.dc_cod_pendency_v1 marks it the one unambiguous ✅ of its set.
  { key: "pendency", label: "COD pendency",
    question: "“COD pendency clear karo” — cash I have collected and not deposited",
    queues: ["Losses & Debits · Cash/COD · Service area · Misroute"],
    answerable: false,
    have: "No adapter — but the closest thing to a solved gap in the estate. LMS exposes the aging buckets directly and PSP already catalogues the route.",
    takes: "LMS /v1/cod-pendency/get-cod-pendency-buckets. One adapter against an endpoint that already matches the warehouse table one-for-one. Cash HANDOVER is the opposite case: five Google Sheets plus a Razorpay leg, and no system to integrate with at all." },
];

function Tile({ icon: Icon, n, label, sub, tone }) {
  return (
    <div className="glass-card rounded-xl p-md">
      <div className="flex items-center gap-sm text-[10px] uppercase tracking-[0.1em] text-on-surface-variant"
        style={{ fontFamily: "JetBrains Mono" }}>
        <Icon size={12} />{label}
      </div>
      <div className={cx("text-2xl font-bold mt-1", tone)} style={{ fontVariantNumeric: "tabular-nums" }}>{n}</div>
      <div className="text-[10.5px] text-on-surface-variant/80 mt-1 leading-relaxed">{sub}</div>
    </div>
  );
}

export default function Connectors() {
  const [d, setD] = useState(null);
  const [t, setT] = useState(null);
  const [err, setErr] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    getConnectors().then(setD).catch(() => setErr("Could not read the registry."));
    // Ticket volumes are a separate, real source. A failure here must not blank the page — the
    // registry is the point and the volumes are supporting evidence.
    getTickets().then(setT).catch(() => {});
  }, []);

  const counts = useMemo(() => {
    if (!d) return null;
    const groups = d.groups || [];
    const others = d.other_services || [];
    const all = [...groups.map((g) => ({ status: g.status, n: g.count })),
                 ...others.map((o) => ({ status: o.status, n: 1 }))];
    const by = (s) => all.filter((x) => x.status === s).reduce((a, x) => a + x.n, 0);
    return { total: all.reduce((a, x) => a + x.n, 0), live: by("live"),
             fixture: by("fixture"), none: by("none") };
  }, [d]);

  if (err) return <div className="glass-card rounded-xl p-lg text-warn">{err}</div>;
  if (!d || !counts) return <div className="glass-card rounded-xl p-lg text-on-surface-variant">Reading the registry…</div>;

  const answerable = DOMAINS.filter((x) => x.answerable);
  const blocked = DOMAINS.filter((x) => !x.answerable);

  return (
    <div className="space-y-lg">
      {/* ── the four states, as four numbers ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-md">
        <Tile icon={Database} n={counts.total} label="Discovered"
          sub="endpoints catalogued across the captain BFF, Prism, Nexus, log10 and LossManagement" />
        <Tile icon={Plug} n={counts.fixture + counts.live} label="Connected"
          tone="text-tertiary"
          sub={`${counts.live} live · ${counts.fixture} reading a file shaped to the real contract`} />
        <Tile icon={Check} n={`${answerable.length}/${DOMAINS.length}`} label="Answerable today"
          tone="text-tertiary" sub="captain-question domains the engine can resolve without escalating" />
        <Tile icon={X} n={counts.none} label="No adapter" tone="text-warn"
          sub="named rather than implied — the gap is the ask" />
      </div>

      {/* ── the honest caveat about the ticket data, stated once, up front ── */}
      {t?.available && (
        <div className="glass-card rounded-xl p-md flex items-start gap-sm text-[11.5px] leading-relaxed text-on-surface-variant">
          <MessageSquare size={13} className="mt-0.5 flex-none" />
          <div>
            <b className="text-on-surface">{(t.total || 0).toLocaleString("en-IN")} tickets</b> in the
            Kapture export, {" "}
            <b className="text-on-surface">{Math.round(100 * ((t.by_folder?.["Whatsapp folder"] || 0) / (t.total || 1)))}%</b>
            {" "}arriving on WhatsApp. Only <b className="text-on-surface">~1.2%</b> carry a sub-type
            at all — so there is no honest percentage-of-all-tickets per domain below, and that
            absence is the finding: the incumbent barely categorises what comes in, which is why
            nobody can say what support spends its time on.
          </div>
        </div>
      )}

      {/* ── CAN WE ANSWER IT? one row per question ── */}
      <section className="glass-card rounded-xl overflow-hidden">
        <div className="px-lg h-[46px] flex items-center border-b border-on-primary-fixed-variant/20">
          <h2 className="text-sm font-semibold text-secondary-container flex items-center gap-sm">
            <Plug size={14} />When a captain asks this, can the system see it?
          </h2>
        </div>
        <div>
          {DOMAINS.map((x) => (
            <div key={x.key}
              className="px-lg py-md border-b border-on-primary-fixed-variant/10 last:border-b-0
                         grid grid-cols-1 md:grid-cols-[minmax(0,1.4fr)_84px_minmax(0,2fr)] gap-md items-start">
              <div>
                <div className="text-[13px] font-semibold text-on-surface">{x.label}</div>
                <div className="text-[11.5px] text-on-surface-variant mt-0.5 leading-relaxed">{x.question}</div>
              </div>
              <div>
                {x.answerable
                  ? <span className="inline-flex items-center gap-1 rounded-full bg-tertiary/12 text-tertiary border border-tertiary/35 px-2.5 py-1 text-[10px] font-bold">
                      <Check size={11} />YES</span>
                  : <span className="inline-flex items-center gap-1 rounded-full bg-warn/12 text-warn border border-warn/35 px-2.5 py-1 text-[10px] font-bold">
                      <X size={11} />NO</span>}
              </div>
              <div className="text-[11.5px] leading-relaxed">
                <div className="text-on-surface-variant">{x.have}</div>
                {x.takes && (
                  <div className="mt-1.5 flex items-start gap-1.5 text-secondary-container">
                    <ArrowRight size={12} className="mt-0.5 flex-none" />
                    <span>{x.takes}</span>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* ── the ask, as one sentence ── */}
      {blocked.length > 0 && (
        <div className="glass-card rounded-xl p-lg border-l-4 border-l-warn/60">
          <div className="flex items-center gap-sm text-warn text-[11px] font-bold uppercase tracking-[0.12em] mb-sm"
            style={{ fontFamily: "JetBrains Mono" }}>
            <TriangleAlert size={14} />What is actually being asked for
          </div>
          <p className="text-[12.5px] text-on-surface leading-relaxed">
            Read access to <b>{blocked.reduce((a, x) =>
              a + (d.groups || []).filter((g) => x.queues.includes(g.queue))
                                  .reduce((s, g) => s + g.count, 0), 0)} endpoints</b> across{" "}
            {blocked.map((x) => x.label.toLowerCase()).join(" and ")}. Nothing needs to be built
            on their side — every one of these is already serving the captain's own panel. The
            adapters on this side are the small part; the access is the whole ask.
          </p>
        </div>
      )}

      {/* ── the endpoint detail, for the one engineer who wants it ── */}
      <section className="glass-card rounded-xl overflow-hidden">
        <button onClick={() => setOpen((v) => !v)}
          className="w-full px-lg h-[46px] flex items-center justify-between text-left hover:bg-surface-variant/20 transition-colors">
          <span className="text-sm font-semibold text-on-surface-variant flex items-center gap-sm">
            <ChevronRight size={14} style={{ transform: open ? "rotate(90deg)" : "none",
              transition: "transform .15s" }} />
            All {counts.total} endpoints — paths, services, adapters, timeouts
          </span>
          <span className="text-[10px] text-on-surface-variant/70" style={{ fontFamily: "JetBrains Mono" }}>
            {d.declarative === false ? "" : "declarative — this page calls nothing"}
          </span>
        </button>
        {open && (
          <div className="border-t border-on-primary-fixed-variant/20 divide-y divide-on-primary-fixed-variant/10">
            {(d.groups || []).map((g) => (
              <div key={g.group} className="px-lg py-md">
                <div className="flex items-baseline gap-sm flex-wrap">
                  <span className="text-[12px] font-semibold text-on-surface">{g.service}</span>
                  <span className={cx("rounded-full px-2 py-0.5 text-[9px] font-bold",
                    g.status === "live" ? "bg-tertiary/12 text-tertiary"
                    : g.status === "fixture" ? "bg-secondary-container/12 text-secondary-container"
                    : "bg-warn/12 text-warn")}>{g.status}</span>
                  <span className="text-[10px] text-on-surface-variant/70" style={{ fontFamily: "JetBrains Mono" }}>
                    {g.adapter} {g.env ? `· ${g.env}` : ""}
                  </span>
                </div>
                <p className="text-[11px] text-on-surface-variant mt-1 leading-relaxed">{g.note}</p>
                <div className="mt-sm space-y-1">
                  {(g.routes || []).map((r) => (
                    <div key={r.path} className="flex items-baseline gap-sm text-[10.5px]"
                      style={{ fontFamily: "JetBrains Mono" }}>
                      <span className="text-on-surface-variant/60 w-[190px] flex-none truncate">{r.name}</span>
                      <span className="text-on-surface-variant flex-1 truncate">{r.path}</span>
                      <span className="text-on-surface-variant/50 flex-none">{r.timeout_ms}ms</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
            {(d.other_services || []).map((o, i) => (
              <div key={i} className="px-lg py-sm flex items-baseline gap-sm text-[10.5px]"
                style={{ fontFamily: "JetBrains Mono" }}>
                <span className="text-on-surface w-[180px] flex-none truncate">{o.service}</span>
                <span className="text-on-surface-variant flex-1 truncate">{o.path}</span>
                <span className={cx("flex-none", o.status === "fixture" ? "text-secondary-container" : "text-warn")}>
                  {o.status}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
