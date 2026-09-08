"""Stage 9 — the xlsx. The register plus the numbers that make it reviewable.

Written with openpyxl, which is already a dependency (used elsewhere only for READING uploaded
sheets — this is the repo's first writer).

── THE REVIEW SHEET IS A COPY, NOT THE PIPELINE ──────────────────────────────────────────────
It exists so people can read and audit the register. If a person has to upload it for anything
to happen, the point has been lost.

── PII IS MASKED HERE, NOT IN THE STORE ──────────────────────────────────────────────────────
Grouping and dedupe join on mobiles and ticket ids, so the store keeps them intact. This is the
boundary, and `redact` defaults to True.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from . import redact, store

HEAD = Font(bold=True)


def _sheet(wb, title, headers, rows, widths=None):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font = HEAD
        c.alignment = Alignment(vertical="top", wrap_text=True)
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    for i, w in enumerate(widths or [], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    return ws


def run(run_id: str, out_path: str | Path, *, con: sqlite3.Connection | None = None,
        redact_pii: bool = True) -> dict:
    own = con is None
    con = con or store.connect()
    try:
        wb = Workbook()
        wb.remove(wb.active)

        # ── register ────────────────────────────────────────────────────────────────────────
        issues = con.execute(
            "SELECT i.*, m.text FROM issues i "
            "JOIN messages m ON m.channel_id=i.anchor_channel_id "
            "               AND m.message_id=i.anchor_message_id "
            "WHERE i.run_id=? ORDER BY m.ts_epoch", (run_id,)).fetchall()
        rows = []
        for i in issues:
            toks = json.loads(i["entity_tokens_json"] or "{}")
            if redact_pii:
                toks = redact.mask_tokens(toks)
            text = redact.mask_text(i["text"]) if redact_pii else i["text"]
            rows.append([
                i["issue_id"], i["anchor_channel_id"], i["anchor_message_id"], i["permalink"],
                i["raiser_name"], i["dc_code"], i["intent"] or "(unadjudicated)",
                "yes" if i["informational"] else "",
                ", ".join(json.loads(i["kapture_ticket_ids_json"] or "[]")),
                json.dumps(toks, sort_keys=True),
                i["first_response_latency_s"], i["latency_excluded_reason"],
                i["reply_count"], i["closure_signal"] or "(none detected)",
                i["closure_detected_by"], i["closure_ts"], i["days_open"],
                i["state"], i["duplicate_of"], text[:400],
            ])
        _sheet(wb, "register",
               ["issue_id", "channel_id", "anchor_message_id", "permalink", "raiser", "dc_code",
                "intent", "informational", "kapture_ticket_ids", "entity_tokens",
                "first_response_s", "latency_excluded_reason", "reply_count", "closure_signal",
                "closure_detected_by", "closure_ts", "days_open", "state", "duplicate_of",
                "anchor_text"],
               rows, [26, 14, 20, 46, 18, 9, 22, 12, 20, 34, 15, 34, 11, 18, 18, 18, 10, 12,
                      26, 60])

        # ── summary ─────────────────────────────────────────────────────────────────────────
        def q(sql, args=()):
            return con.execute(sql, args).fetchall()

        n = len(issues)
        no_closure = sum(1 for i in issues if not i["closure_signal"])
        lat = sorted(i["first_response_latency_s"] for i in issues
                     if i["first_response_latency_s"] is not None)

        def pctl(p):
            return round(lat[min(int(len(lat) * p), len(lat) - 1)], 1) if lat else None

        summary = [["issues", n]]
        summary.append(["% with NO closure signal",
                        f"{no_closure / n:.1%}" if n else "n/a"])
        summary.append(["first response median (s)", pctl(0.5)])
        summary.append(["first response p90 (s)", pctl(0.9)])
        summary.append(["issues with latency EXCLUDED (not a response)", n - len(lat)])
        summary.append(["cross-channel duplicate pairs",
                        q("SELECT COUNT(*) FROM duplicates WHERE run_id=?", (run_id,))[0][0]])
        summary.append(["informational issues", sum(1 for i in issues if i["informational"])])
        summary.append(["UNMAPPED intents",
                        sum(1 for i in issues if i["intent"] == "UNMAPPED")])
        summary.append(["PII redacted in this file", "yes" if redact_pii else "NO"])
        drafts = q("SELECT COALESCE(SUM(suppressed=0),0), COALESCE(SUM(suppressed=1),0), "
                   "COUNT(*) FROM ticket_drafts WHERE run_id=?", (run_id,))[0]
        summary.append([])
        summary.append(["TICKETS WE WOULD RAISE", drafts[0]])
        summary.append(["held back (informational / duplicate / no identifier)", drafts[1]])
        summary.append(["tickets ACTUALLY created (phase 1 creates none)", 0])
        for r in q("SELECT suppressed_reason, COUNT(*) FROM ticket_drafts WHERE run_id=? "
                   "AND suppressed=1 GROUP BY suppressed_reason ORDER BY 2 DESC", (run_id,)):
            summary.append([f"  {str(r[0]).split(' —')[0]}", r[1]])
        summary.append([])
        summary.append(["issues by intent", ""])
        for r in q("SELECT COALESCE(intent,'(unadjudicated)'), COUNT(*) FROM issues "
                   "WHERE run_id=? GROUP BY 1 ORDER BY 2 DESC", (run_id,)):
            summary.append([f"  {r[0]}", r[1]])
        summary.append([])
        summary.append(["repeat issues per DC code (2+)", ""])
        for r in q("SELECT dc_code, COUNT(*) c FROM issues WHERE run_id=? AND dc_code IS NOT NULL"
                   " GROUP BY dc_code HAVING c > 1 ORDER BY c DESC", (run_id,)):
            summary.append([f"  {r[0]}", r[1]])
        summary.append([])
        summary.append(["gated messages by rule", ""])
        for r in q("SELECT gate_rule, COUNT(*) FROM message_flags WHERE run_id=? AND gated=1 "
                   "GROUP BY gate_rule ORDER BY 2 DESC", (run_id,)):
            summary.append([f"  {r[0]}", r[1]])
        _sheet(wb, "summary", ["metric", "value"], summary, [52, 44])

        # ── qualification ───────────────────────────────────────────────────────────────────
        _sheet(wb, "qualification",
               ["channel", "channel_id", "in_scope", "total", "substantive_top_level",
                "identifier_rate", "weather_share", "replies_p50", "replies_p90",
                "sampled_issue_share", "threshold", "reason"],
               [[r["channel_name"], r["channel_id"], "yes" if r["in_scope"] else "NO",
                 r["total_records"], r["substantive_toplevel"], r["identifier_rate"],
                 r["weather_share"], r["reply_count_p50"], r["reply_count_p90"],
                 r["sampled_issue_share"], r["threshold"], r["reason"]]
                for r in q("SELECT * FROM channel_qualification WHERE run_id=? "
                           "ORDER BY in_scope DESC, identifier_rate DESC", (run_id,))],
               [22, 14, 9, 8, 12, 12, 12, 10, 10, 16, 10, 95])

        # ── gated: what the filter actually caught ──────────────────────────────────────────
        _sheet(wb, "gated",
               ["channel", "message_id", "rule", "text"],
               [[r["channel_name"], r["message_id"], r["gate_rule"],
                 (redact.mask_text(r["text"]) if redact_pii else r["text"])[:200]]
                for r in q("SELECT m.channel_name, m.message_id, f.gate_rule, m.text "
                           "FROM message_flags f JOIN messages m USING(channel_id, message_id) "
                           "WHERE f.run_id=? AND f.gated=1 ORDER BY f.gate_rule", (run_id,))],
               [20, 20, 20, 90])

        # ── informational, with the borderlines called out ──────────────────────────────────
        _sheet(wb, "informational",
               ["channel", "message_id", "rule", "borderline", "text"],
               [[r["channel_name"], r["message_id"], r["informational_rule"],
                 "BORDERLINE" if r["informational_borderline"] else "",
                 (redact.mask_text(r["text"]) if redact_pii else r["text"])[:200]]
                for r in q("SELECT m.channel_name, m.message_id, f.informational_rule, "
                           "f.informational_borderline, m.text FROM message_flags f "
                           "JOIN messages m USING(channel_id, message_id) "
                           "WHERE f.run_id=? AND f.informational=1 "
                           "ORDER BY f.informational_borderline DESC", (run_id,))],
               [20, 20, 30, 12, 90])

        # ── tickets: THE PRODUCT, and what was held back ────────────────────────────────────
        _sheet(wb, "tickets",
               ["would_raise", "idempotency_key", "title", "dc_code", "intent", "raiser",
                "reply_count", "first_response_s", "source_permalink", "held_back_because",
                "sink", "dry_run_ref"],
               [["YES" if not r["suppressed"] else "no", r["idempotency_key"][:16],
                 r["title"], r["dc_code"], r["intent"] or "(unadjudicated)",
                 (redact.mask_text(r["raiser"] or "") if redact_pii else r["raiser"]),
                 r["reply_count"], r["first_response_latency_s"], r["source_permalink"],
                 r["suppressed_reason"], r["sink"], r["external_ref"]]
                for r in q("SELECT * FROM ticket_drafts WHERE run_id=? "
                           "ORDER BY suppressed, title", (run_id,))],
               [12, 20, 62, 9, 22, 20, 11, 15, 46, 78, 10, 20])

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(out_path)
        con.execute(
            "INSERT OR REPLACE INTO stage_runs (run_id, stage, started_at, finished_at, "
            "rows_in, rows_out, llm_calls, cost_usd, config_hash, status) "
            "VALUES (?,?,?,?,?,?,0,0.0,?,'ok')",
            (run_id, "report", datetime.now(timezone.utc).isoformat(timespec="seconds"),
             datetime.now(timezone.utc).isoformat(timespec="seconds"), n, n,
             f"redact={redact_pii}"))
        con.commit()
        return {"path": str(out_path), "issues": n, "redacted": redact_pii,
                "sheets": wb.sheetnames}
    finally:
        if own:
            con.close()
