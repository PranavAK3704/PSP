import { useEffect, useState } from "react";
import { HelpCircle, Wallet, Truck, TrendingUp, MapPin, PackageX, Coins,
         ReceiptText, Users, LogOut, ChevronDown, X, Info, Radar } from "lucide-react";
import { getGrowthIndex, getCaptainNudges } from "../lib/api.js";
import { REAL } from "./figures.js";
import { SupportWidget } from "../components/SupportWidget.jsx";
import { useAudience } from "../lib/audienceMode.js";
import Payments from "./modules/Payments.jsx";
import GrowthModule from "./modules/GrowthModule.jsx";
import LossManagement from "./modules/LossManagement.jsx";
import { DcCapacity, CashPendency, PilotRateCard, PilotManagement,
         ServiceArea } from "./modules/Simulated.jsx";

/* ── The Captain Panel, reproduced ────────────────────────────────────────────────────────────

   WHY THIS EXISTS AT ALL, and why the Growth Dashboard on its own was not enough.

   The real product at partner-app.valmo.in/captain/<DC>/payments is EIGHT modules behind one
   sidebar, and Payments — not the Growth Dashboard — is where a captain lands. PSP had rebuilt
   exactly one of the eight, so the support widget was sitting beside a module most captains open
   occasionally, in an app that looked nothing like the one they use.

   ── AND THE ONE LINE IN THEIR REPO THAT THIS IS ALL ABOUT ────────────────────────────────────
   `Valmo Support` is the FIRST row of the real sidebar, above every module. Here is what it does
   today (modules/shared/hooks/useSidebar.ts):

       const VALMO_SUPPORT_LINK =
           'https://selfserveapp.kapturecrm.com/support-portals/valmo/index.php';
       const handleSupportClick = () => {
           dispatchCaptainSupportMenuClickedEvent({ hubCode });
           window.open(VALMO_SUPPORT_LINK, '_blank', 'noopener,noreferrer');
       };

   A captain who needs help is ejected out of the app into an external Kapture ticket portal, in
   a new tab. Payments' own tooltip says the same thing in words — "please raise a ticket using
   Kapture or connect with your AM".

   So the widget is not docked beside a dashboard here. It IS that row. Same position, same
   label, same click — and instead of a new tab to a ticket form, a panel opens in place and
   answers. Nothing about the argument needs narrating: the row is where they already look.

   ── WHAT IS REAL AND WHAT IS NOT ─────────────────────────────────────────────────────────────
   Two modules carry real data and say so: Growth Dashboard (the fixture mirroring the live
   endpoint) and Loss Management (valmo.db, real rows, plus the derived at-risk ladder). The
   other six are generated — see figures.js for the rules — and each carries a SIMULATED chip,
   with a banner on the shell. The distinction is visible on every screen rather than explained
   once, because "which of these numbers are real" is the first question anyone sensible asks.

   The chrome is deliberately the REAL panel's chrome and not PSP's: navy sidebar, white content,
   Meesho's own type scale. PSP's dark theme here would quietly turn a replica into a redesign,
   and the point of the screen is that a captain recognises it. ── */

const MODULES = [
  { key: "payments",         label: "Payments",         icon: Wallet,      comp: Payments },
  { key: "dc-capacity",      label: "DC Capacity",      icon: Truck,       comp: DcCapacity,     isNew: true },
  { key: "growth-dashboard", label: "Growth Dashboard", icon: TrendingUp,  comp: GrowthModule,   isNew: true },
  { key: "service-area",     label: "Service Area",     icon: MapPin,      comp: ServiceArea },
  { key: "loss-management",  label: "Loss Management",  icon: PackageX,    comp: LossManagement },
  { key: "cash-pendency",    label: "Cash Pendency",    icon: Coins,       comp: CashPendency,   isNew: true },
  { key: "pilot-rate-card",  label: "Pilot Rate Card",  icon: ReceiptText, comp: PilotRateCard,  isNew: true },
  { key: "pilot-management", label: "Pilot Management", icon: Users,       comp: PilotManagement, isNew: true },
];

export default function CaptainPanelReplica() {
  const [index, setIndex] = useState(null);
  const [hubs, setHubs] = useState([]);
  const [hub, setHub] = useState("");
  const [active, setActive] = useState("payments");
  const [supportOpen, setSupportOpen] = useState(false);
  const [hubOpen, setHubOpen] = useState(false);
  const [nudges, setNudges] = useState([]);
  const [dismissed, setDismissed] = useState({});
  const [showEngine] = useAudience();

  useEffect(() => {
    getGrowthIndex().then((r) => {
      setIndex(r);
      const list = r.hubs || [];
      setHubs(list);
      // LZ5 first — it is the hub with an allocation miss AND real at-risk rows, so it is the
      // one where every module has something to show.
      setHub(list.includes("LZ5") ? "LZ5" : list[0] || "");
    }).catch(() => {});
  }, []);

  // The partner id lives on the INDEX as `partners[hub]` — one call, already made above. I had
  // it fetching the per-hub payload and reading `provenance.partner_id`, which does not exist
  // there, so `partnerId` was always "" and the widget rendered disabled: "no partner mapped to
  // this hub". GrowthDashboard has always done it this way (`index.partners[hub]`); the replica
  // invented a second, wrong lookup instead of copying the one that worked.
  const partnerId = ((index && index.partners) || {})[hub] || "";

  // ── PROACTIVE MONITORING, ARRIVING WHERE THE CAPTAIN IS ────────────────────────────────────
  // The scan already ran and already wrote its finding; nothing showed it to the captain. Polled
  // rather than pushed, and said so on the card — there is no push channel, and claiming one
  // would be the same overstatement as the old "Resolve & notify captain" button.
  useEffect(() => {
    if (!partnerId) { setNudges([]); return; }
    let live = true;
    const pull = () => getCaptainNudges(partnerId)
      .then((d) => { if (live) setNudges(d.nudges || []); })
      .catch(() => {});
    pull();
    const t = setInterval(pull, 20000);   // slower than the case poll: findings do not change fast
    return () => { live = false; clearInterval(t); };
  }, [partnerId]);

  const openNudges = nudges.filter((x) => !dismissed[x.id]);
  // Which modules have something wrong on them — this is what turns a notification into a
  // direction. A badge on `Loss Management` says where to look; a bell says only "something".
  const byModule = openNudges.reduce((a, x) => ({ ...a, [x.module]: (a[x.module] || 0) + 1 }), {});
  const forActive = openNudges.filter((x) => x.module === active);

  const mod = MODULES.find((m) => m.key === active) || MODULES[0];
  const Comp = mod.comp;
  const realSource = REAL[mod.key];

  return (
    <div className="cp-root">
      {/* ── SIDEBAR — the real one's order, labels and NEW badges ── */}
      <aside className="cp-sidebar">
        <div className="cp-profile">
          <div className="cp-avatar">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
              <path d="M12 12a5 5 0 100-10 5 5 0 000 10zm0 2c-4.4 0-8 2.2-8 5v1h16v-1c0-2.8-3.6-5-8-5z"/>
            </svg>
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="cp-role">CAPTAIN</div>
            <button className="cp-hub" onClick={() => setHubOpen((v) => !v)}>
              {hub || "—"}<ChevronDown size={13} />
            </button>
            {hubOpen && (
              <div className="cp-hub-menu">
                {hubs.map((h) => (
                  <div key={h} className={`cp-hub-item ${h === hub ? "on" : ""}`}
                    onClick={() => { setHub(h); setHubOpen(false); }}>{h}</div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* THE ROW THIS WHOLE SCREEN IS ABOUT. Upstream: window.open(kapturecrm.com). Here: the
            widget opens in place. Kept at the top, above the modules, exactly where it is. */}
        <button className={`cp-support ${supportOpen ? "on" : ""}`}
          onClick={() => setSupportOpen((v) => !v)}>
          <HelpCircle size={19} />
          <span>Valmo Support</span>
          {/* The dot means "there is something to talk about", not decoration. It was always on
              before, which is the same as never on. */}
          {!supportOpen && openNudges.length > 0 && <span className="cp-support-dot" />}
        </button>

        <nav className="cp-nav">
          {MODULES.map((m) => {
            const Icon = m.icon;
            return (
              <button key={m.key} className={`cp-nav-item ${m.key === active ? "on" : ""}`}
                onClick={() => setActive(m.key)}>
                <Icon size={19} />
                <span>{m.label}</span>
                {byModule[m.key]
                  ? <span className="cp-alert" title={`${byModule[m.key]} thing(s) needing attention`}>
                      {byModule[m.key]}</span>
                  : m.isNew && <span className="cp-new">NEW</span>}
              </button>
            );
          })}
        </nav>

        <div className="cp-foot">
          <button className="cp-nav-item"><LogOut size={19} /><span>Log Out</span></button>
          <div className="cp-brand"><b>VALMO</b><span>Captain Panel</span></div>
        </div>
      </aside>

      {/* ── CONTENT ── */}
      <main className="cp-main">
        <div className="cp-topline">
          <h1>{mod.label}</h1>
          {realSource
            ? <span className="cp-chip real"><Info size={11} />REAL DATA · {realSource}</span>
            : <span className="cp-chip sim"><Info size={11} />SIMULATED · no data source connected</span>}
        </div>

        {!realSource && (
          <div className="cp-simbanner">
            <b>These figures are generated, not measured.</b> PSP has no adapter for this module —
            there is no <code>/v1/captain/{mod.key}</code> access today. The layout, labels and
            units are taken from the real module's source; the numbers are deterministic per hub
            so they never move between refreshes. Growth Dashboard and Loss Management run on real
            data and are chipped as such.
          </div>
        )}

        {/* The finding, on the screen it is about, before the numbers it is about. */}
        {forActive.map((x) => (
          <div key={x.id} className="cp-nudge">
            <div className="cp-nudge-icon"><Radar size={16} /></div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="cp-nudge-head">
                Noticed without being asked
                <span className="cp-nudge-when">
                  {x.shipments ? `${x.shipments} shipments` : ""}
                  {x.amount_inr ? ` · ₹${Math.round(x.amount_inr).toLocaleString("en-IN")} at risk` : ""}
                </span>
              </div>
              <div className="cp-nudge-body">
                {x.message || x.headline}
                {x.detected_only && (
                  <em> — flagged by the severity rules; no written summary was generated for this
                    one, so the numbers above are the whole finding.</em>
                )}
              </div>
              <div className="cp-nudge-foot">
                nobody raised a ticket for this · checked every few minutes, not pushed
              </div>
            </div>
            <button className="cp-x cp-nudge-x" aria-label="Dismiss"
              onClick={() => setDismissed((d) => ({ ...d, [x.id]: true }))}><X size={14} /></button>
          </div>
        ))}

        <div className="cp-body">
          <Comp hub={hub} partnerId={partnerId} />
        </div>
      </main>

      {/* ── THE SUPPORT PANEL — what "Valmo Support" opens instead of Kapture ── */}
      {supportOpen && (
        <aside className="cp-support-panel">
          <div className="cp-support-head">
            <div>
              <div className="cp-support-title"><HelpCircle size={14} />Valmo Support</div>
              <div className="cp-support-sub">
                Upstream this button is <code>window.open(selfserveapp.kapturecrm.com)</code>
              </div>
            </div>
            <button className="cp-x" onClick={() => setSupportOpen(false)} aria-label="Close">
              <X size={15} />
            </button>
          </div>
          <div className="cp-support-body">
            {/* No `embedded` prop — SupportWidget's signature is ({ hub, partnerId, askRef,
                showEngine }), and passing one it does not accept looks wired and does nothing. */}
            <SupportWidget hub={hub} partnerId={partnerId} showEngine={showEngine} />
          </div>
        </aside>
      )}
    </div>
  );
}
