import AtRiskPanel from "../../components/AtRiskPanel.jsx";

/* ── Loss Management — REAL DATA ──────────────────────────────────────────────────────────────

   UPSTREAM: modules/captain/loss-management/{risk-summary,shipment-pendency}

   The one module where the replica is not a replica: upstream's risk summary and PSP's at-risk
   ladder are the same idea over the same ledger, and PSP's is derived from 5,614 real
   attribution rows joined to real losses on AWB. So this renders the real panel rather than a
   generated stand-in.

   It is also the module the demo's escalating turn is ABOUT — a captain disputing a hard-stop
   debit is looking at this screen when they open the widget. ── */
export default function LossManagement({ hub }) {
  // `hub` only — AtRiskPanel's signature is ({ hub, onAsk, onData, id }); it resolves the partner
  // itself. Passing a partnerId it does not accept would look wired and do nothing.
  return (
    <div className="cp-embed">
      <AtRiskPanel hub={hub} />
    </div>
  );
}
