"""Mock prism-sidecar — speaks the real sidecar's HTTP contract with canned rows.

Why this exists: the real sidecar is Java and needs `com.meesho:prismsdk` (internal Nexus) plus a
`client_token` from the data team. This mock lets us build and TEST the whole Python path —
named-query translation, transport, error codes, provenance — before any of that lands, and lets
anyone run the app end-to-end with PSP_DATA_PROVIDER=prism locally.

    python scripts/prism_sidecar_mock.py &            # :8099
    PSP_DATA_PROVIDER=prism PRISM_SIDECAR_URL=http://localhost:8099 uvicorn app.main:app --port 8077

It mirrors the real contract exactly, including failure codes, so the Python side is exercised
honestly: pass ?fail=401 (or 409/500) on /fetch to simulate PrismSDK errors.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PRISM_SIDECAR_PORT", "8099"))

# Canned rows keyed by the named query — shaped like the REAL tables (the loss columns are the
# actual gold.valmo_lost_awb_2k24_v1 columns), so the Python mapping is genuinely exercised.
_ROWS: dict[str, list[dict]] = {
    "get_loss_for_awb": [{
        "awb": "VL0093310077", "consolidation_awb": "", "created_date": "2026-06-20",
        "lost_date": "2026-06-25", "actual_lost_date": "2026-06-25",
        "current_movement_type": "forward", "shipment_value": "1860", "loss_percentage": "100%",
        "loss_value": "1860", "location": "PUN-DC", "leg": "LM", "loc2": "", "leg2": "",
        "reason": "hardstop_not_connected_within_sla", "reason_l1": "hardstop",
        "attribution_changed": "yes", "facility_inscan": "2026-06-25", "DC_Tenurity": ">56 days",
    }],
    "get_loss_attribution": [{
        "awb": "VL0093310077", "lost_date": "2026-06-25", "reason_l1": "hardstop",
        "loss_value": "1860", "loss_percentage": "100%", "location": "PUN-DC",
        "attribution_changed": "yes", "facility_inscan": "2026-06-25",
    }],
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        if urlparse(self.path).path == "/health":
            self._send(200, {"ok": True, "token_present": True, "environment": "MOCK",
                             "detail": "mock sidecar — canned rows, no data lake"})
        else:
            self._send(404, {"ok": False, "message": "not found"})

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/fetch":
            self._send(404, {"ok": False, "message": "not found"})
            return
        # simulate PrismSDK failures: /fetch?fail=401|409|500
        fail = (parse_qs(parsed.query).get("fail") or [""])[0]
        if fail:
            msgs = {"401": "Unauthorized", "409": "TABLE_NOT_FOUND", "500": "internal engine error"}
            self._send(int(fail) if fail in ("401", "409") else 502,
                       {"ok": False, "code": fail, "message": msgs.get(fail, "error")})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
        except (TypeError, ValueError):
            self._send(400, {"ok": False, "code": "400", "message": "invalid JSON"})
            return

        table = str(req.get("table", ""))
        if not table or "select" in table.lower() or " " in table:
            self._send(400, {"ok": False, "code": "400", "message": "bad table identifier"})
            return
        if table.startswith("silver.") and not (req.get("startDate") and req.get("endDate")):
            self._send(400, {"ok": False, "code": "400",
                             "message": f"partitioned table '{table}' requires startDate and endDate"})
            return

        name = str(req.get("queryName", ""))
        rows = [dict(r) for r in _ROWS.get(name, [])][: int(req.get("limit") or 100)]
        cols = req.get("columns") or []
        if cols:   # return only requested columns, like the real engine
            rows = [{k: r.get(k) for k in cols if k in r} for r in rows]
        self._send(200, {"rows": rows, "rowCount": len(rows), "queryName": name, "tookMs": 1})

    def log_message(self, fmt, *args):   # quieter test output
        print("[mock-sidecar] " + fmt % args)


if __name__ == "__main__":
    print(f"mock prism-sidecar on 127.0.0.1:{PORT} (canned rows; not the data lake)")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
