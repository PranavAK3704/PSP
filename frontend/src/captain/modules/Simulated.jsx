import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { dcCapacity, cashPendency, pilotRateCard, pilots, serviceArea } from "../figures.js";

/* ── The five modules PSP cannot see into ─────────────────────────────────────────────────────

   DC Capacity · Cash Pendency · Pilot Rate Card · Pilot Management · Service Area.

   Each reproduces the real module's SHAPE — its metric, its units, its actionable split — over
   generated figures, and each says on the surface that the figures are generated. The layouts
   come from the corresponding directory under
   valmo-partner-webview/src/modules/captain/, noted per component.

   These are one file rather than five because none of them is doing anything a captain would
   interact with here: they exist so the sidebar is real and so the widget is reachable from the
   module a captain happens to be on. The two modules that ARE the product — Growth Dashboard and
   Loss Management — get their own files and their own real data.

   THE ONE THING WORTH READING ON THESE SCREENS is the access line at the bottom of each: the
   endpoint PSP would need. Four of these five map directly onto ticket categories the support
   corpus is full of — capacity cuts, COD pendency, rate-card disputes, pilot onboarding failures
   — so "no adapter" is not an abstract gap, it is a named list of questions the engine currently
   has to escalate. That is the access ask, stated where the missing data would have been. ── */

const inr = (n) => `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;

function Need({ endpoint, tickets }) {
  return (
    <div className="cp-need">
      <b>To make this real:</b> <code>{endpoint}</code>
      {tickets && <> — and it answers <b>{tickets}</b> without escalating.</>}
    </div>
  );
}

function Bar({ pct, tone }) {
  return <div className="cp-bar"><div className={`cp-bar-fill ${tone || ""}`}
    style={{ width: `${Math.max(2, Math.min(100, pct))}%` }} /></div>;
}

/* ── DC Capacity ── modules/captain/dc-capacity/ ───────────────────────────────────────────── */
export function DcCapacity({ hub }) {
  const d = dcCapacity(hub || "LZ5");
  return (
    <>
      <section className="cp-card">
        <div className="cp-card-top"><h2>Daily Capacity</h2></div>
        <div className="cp-totals">
          <div className="cp-total"><div className="cp-total-label">Capacity Set</div>
            <div className="cp-total-amt">{d.set.toLocaleString("en-IN")}</div></div>
          <div className="cp-total"><div className="cp-total-label">7-day Average Utilised</div>
            <div className="cp-total-amt">{d.avg.toLocaleString("en-IN")}</div></div>
          <div className="cp-total"><div className="cp-total-label">Peak Day</div>
            <div className="cp-total-amt">{d.peak.toLocaleString("en-IN")}</div></div>
          <div className="cp-total"><div className="cp-total-label">Days Over Capacity</div>
            <div className={`cp-total-amt ${d.days.some((x) => x.breached) ? "neg" : ""}`}>
              {d.days.filter((x) => x.breached).length} / 7</div></div>
        </div>
        <div className="cp-rows">
          {d.days.map((x) => (
            <div key={x.day} className="cp-row">
              <span className="cp-row-k">{x.day}</span>
              <Bar pct={(x.utilised / d.set) * 100} tone={x.breached ? "warn" : ""} />
              <span className="cp-row-v">
                {x.utilised.toLocaleString("en-IN")} / {d.set.toLocaleString("en-IN")}
                {x.breached && <> <AlertTriangle size={11} /></>}
              </span>
            </div>
          ))}
        </div>
        <p className="cp-note">
          Capacity set too low caps allocation; exceeded too often and the allocation engine
          applies a capacity cut. Both show up on the Growth Dashboard as
          <b> Orders Missed in Capacity Cut</b> — which is real data, one module down.
        </p>
        <Need endpoint="/v1/captain/dc-capacity/:hubID" tickets="capacity-cut disputes" />
      </section>
    </>
  );
}

/* ── Cash Pendency ── modules/captain/cash-pendency/ ───────────────────────────────────────── */
export function CashPendency({ hub }) {
  const d = cashPendency(hub || "LZ5");
  return (
    <section className="cp-card">
      <div className="cp-card-top"><h2>COD Pendency (DOH)</h2></div>
      <div className="cp-totals">
        <div className="cp-total"><div className="cp-total-label">Total Pending</div>
          <div className="cp-total-amt">{inr(d.total)}</div></div>
        <div className="cp-total"><div className="cp-total-label">Overdue (&gt; 3 days)</div>
          <div className="cp-total-amt neg">{inr(d.overdue)}</div></div>
      </div>
      <div className="cp-rows">
        {d.buckets.map((b) => (
          <div key={b.label} className="cp-row">
            <span className="cp-row-k">{b.label}</span>
            <Bar pct={(b.amount / d.total) * 100} tone={b.days === "7+" ? "warn" : ""} />
            <span className="cp-row-v">{inr(b.amount)} · {b.shipments} shipments</span>
          </div>
        ))}
      </div>
      <p className="cp-note">
        Pendency above 2.5 days is what the Growth Dashboard scores as <b>Pendency (DOH)</b>, and
        clearing it is a precondition for avoiding a capacity cut.
      </p>
      <Need endpoint="/v1/captain/cash-pendency/:hubID/summary"
            tickets="~16% of tickets (COD / cash)" />
    </section>
  );
}

/* ── Pilot Rate Card ── modules/captain/pilot-rate-card/ ───────────────────────────────────── */
export function PilotRateCard({ hub }) {
  const d = pilotRateCard(hub || "LZ5");
  return (
    <section className="cp-card">
      <div className="cp-card-top"><h2>Cost Per Shipment</h2></div>
      <div className="cp-totals">
        <div className="cp-total"><div className="cp-total-label">Current CPS</div>
          <div className={`cp-total-amt ${d.onTrack ? "" : "neg"}`}>₹{d.current}</div></div>
        <div className="cp-total"><div className="cp-total-label">Target CPS</div>
          <div className="cp-total-amt">₹{d.target}</div></div>
        <div className="cp-total"><div className="cp-total-label">Status</div>
          <div className="cp-total-amt">
            {d.onTrack ? <><CheckCircle2 size={15} /> On track</>
                       : <><AlertTriangle size={15} /> Above target</>}</div></div>
      </div>
      <div className="cp-rows">
        {d.bands.map((b) => (
          <div key={b.label} className="cp-row">
            <span className="cp-row-k">{b.label}</span>
            <Bar pct={(b.current / (d.target * 1.4)) * 100}
                 tone={b.current > b.target ? "warn" : ""} />
            <span className="cp-row-v">₹{b.current} vs ₹{b.target} target</span>
          </div>
        ))}
      </div>
      <p className="cp-note">
        The target is derived from neighbouring DCs' rate cards — which is why a captain cannot
        reproduce it themselves, and why "why is my target this number" is a support question
        rather than a self-serve one.
      </p>
      <Need endpoint="/v1/captain/pilot-rate-card/:hubID" tickets="rate-card disputes" />
    </section>
  );
}

/* ── Pilot Management ── modules/captain/pilot-management/ ─────────────────────────────────── */
export function PilotManagement({ hub }) {
  const d = pilots(hub || "LZ5");
  return (
    <section className="cp-card">
      <div className="cp-card-top"><h2>Pilots</h2></div>
      <div className="cp-totals">
        <div className="cp-total"><div className="cp-total-label">Active</div>
          <div className="cp-total-amt">{d.active}</div></div>
        <div className="cp-total"><div className="cp-total-label">Inactive</div>
          <div className="cp-total-amt">{d.inactive}</div></div>
        <div className="cp-total"><div className="cp-total-label">Onboarding Pending</div>
          <div className="cp-total-amt">{d.pending}</div></div>
        <div className="cp-total"><div className="cp-total-label">Onboarding Failed</div>
          <div className={`cp-total-amt ${d.failed ? "neg" : ""}`}>{d.failed}</div></div>
      </div>
      {d.failedRows.length > 0 && (
        <div className="cp-rows">
          {d.failedRows.map((r, i) => (
            <div key={i} className="cp-row">
              <span className="cp-row-k">{r.name}</span>
              <span style={{ flex: 1, fontSize: 13 }}>{r.reason}</span>
              <span className="cp-row-v">blocked {r.since}</span>
            </div>
          ))}
        </div>
      )}
      <p className="cp-note">
        A failed onboarding is a pilot who cannot work, and the reason codes here
        (Aadhaar mismatch, DL expired, bank verification) are the exact set the FE-onboarding
        ticket class is made of.
      </p>
      <Need endpoint="/v1/captain/pilot-management/:hubID/pilots"
            tickets="pilot onboarding failures" />
    </section>
  );
}

/* ── Service Area ── modules/captain/service-area/ ─────────────────────────────────────────── */
export function ServiceArea({ hub }) {
  const d = serviceArea(hub || "LZ5");
  const stale = d.updatedDaysAgo > 7;
  return (
    <section className="cp-card">
      <div className="cp-card-top">
        <h2>Serviceable Pincodes</h2>
        <span className={`cp-inline-chip ${stale ? "warn" : ""}`}>
          updated {d.updatedDaysAgo}d ago{stale ? " · needs review" : ""}
        </span>
      </div>
      <p className="cp-note" style={{ marginTop: 0 }}>
        The real module is a map with a drawable service area. A fake map is the one thing that
        would read as a mock-up rather than a replica, so this is the pincode list view — which is
        where a captain actually checks coverage.
      </p>
      <div className="cp-pins">
        {d.pincodes.map((p) => (
          <div key={p.pin} className={`cp-pin ${p.serviceable ? "" : "off"}`}>
            <b>{p.pin}</b>
            <span>{p.serviceable ? `${p.shipments7d} shipments / 7d` : "not serviceable"}</span>
          </div>
        ))}
      </div>
      <Need endpoint="/v1/captain/service-area/:hubID/my-service-area"
            tickets="misroute and wrong-pincode escalations" />
    </section>
  );
}
