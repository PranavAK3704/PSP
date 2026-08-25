import { useEffect, useState } from "react";
import { HelpCircle, Wallet, Truck, TrendingUp, MapPin, PackageX, Coins,
         ReceiptText, Users, LogOut, ChevronDown, X, Info } from "lucide-react";
import { getGrowthIndex, getGrowth } from "../lib/api.js";
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
  const [hubs, setHubs] = useState([]);
  const [hub, setHub] = useState("");
  const [partnerId, setPartnerId] = useState("");
  const [active, setActive] = useState("payments");
  const [supportOpen, setSupportOpen] = useState(false);
  const [hubOpen, setHubOpen] = useState(false);
  const [showEngine] = useAudience();

  useEffect(() => {
    getGrowthIndex().then((r) => {
      const list = r.hubs || [];
      setHubs(list);
      // LZ5 first — it is the hub with an allocation miss AND real at-risk rows, so it is the
      // one where every module has something to show.
      setHub(list.includes("LZ5") ? "LZ5" : list[0] || "");
    }).catch(() => {});
  }, []);

  // The panel's partner id — the widget posts as this captain, so it has to be the real one.
  // Through `getGrowth`, NOT a hand-rolled fetch: api.js already owns the auth header and the
  // 401 -> back-to-login behaviour, and a second place that builds its own request is exactly how
  // the chat body ended up hand-assembled in two files with a required field missing from one.
  useEffect(() => {
    if (!hub) return;
    let live = true;
    getGrowth(hub)
      .then((d) => { if (live) setPartnerId(d?.provenance?.partner_id || d?.partner_id || ""); })
      .catch(() => {});
    return () => { live = false; };   // a fast hub switch must not land the old hub's partner
  }, [hub]);

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
          {!supportOpen && <span className="cp-support-dot" />}
        </button>

        <nav className="cp-nav">
          {MODULES.map((m) => {
            const Icon = m.icon;
            return (
              <button key={m.key} className={`cp-nav-item ${m.key === active ? "on" : ""}`}
                onClick={() => setActive(m.key)}>
                <Icon size={19} />
                <span>{m.label}</span>
                {m.isNew && <span className="cp-new">NEW</span>}
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
