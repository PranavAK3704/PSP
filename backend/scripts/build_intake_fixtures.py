"""Generate the synthetic intake fixtures, conformant to schema v1.1 by construction.

    python scripts/build_intake_fixtures.py

── WHY GENERATED RATHER THAN HAND-WRITTEN ────────────────────────────────────────────────────
`tools/validate.js` cross-checks four things that are easy to get subtly wrong by hand and that
fail loudly only in aggregate: `ts_epoch` must equal `float(message_id)` exactly, `ts_iso` must
be the IST rendering of `message_id` to the second, the permalink tail must be `message_id`
with the dot removed, and `mentions[]` must agree exactly with the `<@U…>` markup in `text`.
Deriving all four from one timestamp makes a malformed fixture impossible to write.

── EVERYTHING HERE IS INVENTED ───────────────────────────────────────────────────────────────
No real message, name, email or number. Mobiles start 99000 by convention so a real 10-digit
number can never be mistaken for one of these. The DC codes ARE the real ones observed in the
channels, because the point of the fixture is to assert the exact token set they produce.

── WHAT EACH DAY COVERS ──────────────────────────────────────────────────────────────────────
2026-08-28  The Tier A measurement: 18 top-level messages, 7 of them code-bearing, yielding
            exactly {NQS, IQU, UB1, L9D, CKH, RW3, PJ2, PJR, J93, MFC} and zero false
            positives. The 11 non-bearing messages are the precision test — they include the
            "DC landing time" (?i) trap, "DC CODE IS MISSING", "342 Tids", ops acronyms, and
            the festival/rain message that is deliberately borderline for the weather gate.
2026-09-04  Already committed: populated reactions, a non-null edited_ts, and an empty-text
            message carrying only a screenshot.
2026-09-05  The cross-channel duplicate. The same author posts the same MX1 captain-panel
            issue in BOTH channels 47 seconds apart, and the threads fork -- 4 replies on one
            side, 2 on the other. Per-channel dedupe makes two tickets out of this on day one,
            and single-channel closure detection leaves the other open forever.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "intake" / "fixtures"

IST = timezone(timedelta(hours=5, minutes=30))
FF = ("C09JY7YLB3L", "valmo-firefighters")
AMS = ("C08T6NLL77H", "valmo-lm-ams")
WS = "T0S2UJU8H"

USERS = {
    "U09HH8QKZ43": ("Example Raiser", "example.raiser@meesho.com"),
    "U076430KSP4": ("Example Responder", "example.responder@meesho.com"),
    "U03H9UXLGES": ("Example Hub Lead", "example.hublead@meesho.com"),
    "U0BAM1ENFV0": ("Example Approver", "example.approver@meesho.com"),
    "U0C1AAAAAA1": ("Example Ops", "example.ops@meesho.com"),
}


def _rec(ch, ts, author, text, *, thread_ref=None, is_parent=False, reply_count=0,
         subtype=None, attachments=None, reactions=None, edited_ts=None):
    cid, cname = ch
    mid = f"{ts:.6f}"
    dt = datetime.fromtimestamp(ts, IST)
    mentions = []
    for tok in text.split("<@"):
        if tok and tok[0] in "UW":
            uid = tok.split(">")[0].split("|")[0]
            if uid not in mentions:
                mentions.append(uid)
    subteams = [t.split("^")[1].split("|")[0].split(">")[0]
                for t in text.split("<!subteam") if "^" in t]
    atts = attachments or []
    return {
        "schema_version": "1.1",
        "source": "slack:conversations.history+replies",
        "workspace_id": WS,
        "channel_id": cid,
        "channel_name": cname,
        "message_id": mid,
        "ts_epoch": float(mid),
        "ts_iso": dt.strftime("%Y-%m-%dT%H:%M:%S") + "+05:30",
        "author_id": author,
        "author_name": USERS[author][0],
        "author_email": USERS[author][1],
        "author_is_bot": False,
        "text": text,
        "subtype": subtype,
        "thread_ref": thread_ref,
        "is_thread_parent": is_parent,
        "reply_count": reply_count,
        "mentions": mentions,
        "channel_mention": any(m in text for m in ("<!channel>", "<!here>", "<!everyone>")),
        "subteam_mentions": subteams,
        "attachments": atts,
        "has_media": bool(atts),
        "reactions": reactions or [],
        "permalink": f"https://meesho.slack.com/archives/{cid}/p{mid.replace('.', '')}",
        "edited_ts": edited_ts,
        "fetched_at": "2026-09-06T18:30:00+05:30",
    }


def _img(fid, name):
    return {"id": fid, "name": name, "title": None, "mimetype": "image/jpeg",
            "filetype": "jpg", "size": 118322, "url_private": None}


# ── 2026-08-28: the Tier A measurement ──────────────────────────────────────────────────────
# 18 top-level messages. CODE-BEARING (7) and their expected token sets, then 11 that must
# yield NOTHING -- the precision half, which is the half that catches a bad regex.
BEARING = [
    ("DC Code: NQS. Daily closure not happening since morning, please check.", {"NQS"}),
    ("The *IQU DC landing time is delayed again, third day running.", {"IQU"}),
    ("DC _UB1_ vehicle held at check post, need approval to release.", {"UB1"}),
    ("hub L9D and J93 both showing load not received in the panel.", {"L9D", "J93"}),
    ("DC Codes:\nCKH\nRW3\nline haul delayed, no ETA shared with hubs.", {"CKH", "RW3"}),
    ("LMDC PJ2/PJR negative payout reflecting for October cycle.", {"PJ2", "PJR"}),
    ("FMH MFC invoices not generating for last two cycles.", {"MFC"}),
]
CLEAN = [
    "DC landing time is late again today.",                    # THE (?i) trap -> would be LAN
    "342 Tids are coming in Hardstop loss, please advise.",     # only the registry rejects 342
    "DC CODE IS MISSING in the sheet shared yesterday.",        # would yield CODE
    "TAT breach on RTO shipments, PFB the summary.",            # ops acronyms
    "FYI - RVP pendency is high, DRS not closing on time.",     # RVP is a real hub AND acronym
    "Heavy rainfall since 4 AM, most FE unable to start deliveries. Load will be delayed.",
    "Due to festival manpower absenteeism today only 40% FE reported, and rain since morning "
    "is adding to it. Expect delivery percentage impact.",      # BORDERLINE for the weather gate
    "Payout not reflecting for one of our field executives, raised 3 tickets already.",
    "Captain panel login failing with OTP not received error.",
    "Please share the updated pilot onboarding SOP.",
    "noted, thanks",                                            # noise-gate case
]


def build_0828():
    base = datetime(2026, 8, 28, 9, 0, 0, tzinfo=IST).timestamp()
    recs, expected = [], {}
    for i, (text, codes) in enumerate(BEARING):
        ts = base + i * 137 + 0.100000 * (i + 1)
        r = _rec(AMS, ts, "U09HH8QKZ43", text, is_parent=True, reply_count=1)
        recs.append(r)
        expected[r["message_id"]] = sorted(codes)
        recs.append(_rec(AMS, ts + 600, "U076430KSP4", "Checking with the hub, will update.",
                         thread_ref=r["message_id"]))
    # channel_join / channel_leave. The corpus needs them for two reasons: validate.js WARNS
    # when a corpus has none (an export with joins filtered upstream is not "everything"), and
    # the stage 3 noise gate drops by subtype, which cannot be tested without them. They are
    # 3-20% of a real day depending on the day -- NOT the 40% originally assumed.
    for k, (uid, sub) in enumerate([("U0C1AAAAAA1", "channel_join"),
                                    ("U03H9UXLGES", "channel_join"),
                                    ("U0BAM1ENFV0", "channel_leave")]):
        ts = base + 30 + k * 17 + 0.900000
        recs.append(_rec(AMS, ts, uid, f"<@{uid}> has joined the channel"
                         if sub == "channel_join" else f"<@{uid}> has left the channel",
                         subtype=sub))

    for j, text in enumerate(CLEAN):
        ts = base + 3600 + j * 211 + 0.200000 * (j + 1)
        atts = [_img(f"F0FIX{j:05d}", f"screenshot-{j}.jpeg")] if j in (5, 6) else None
        r = _rec(AMS, ts, "U03H9UXLGES", text, attachments=atts)
        recs.append(r)
        expected[r["message_id"]] = []
    return recs, expected


# ── 2026-09-05: the cross-channel duplicate ─────────────────────────────────────────────────
MX1_TEXT = ("MX1 captain panel not working since morning, none of the captains can login. "
            "Escalating here as well.")


def build_0905():
    t0 = datetime(2026, 9, 5, 10, 12, 3, tzinfo=IST).timestamp()
    recs = []
    a = _rec(FF, t0 + 0.400000, "U09HH8QKZ43", MX1_TEXT, is_parent=True, reply_count=4)
    b = _rec(AMS, t0 + 47.510000, "U09HH8QKZ43", MX1_TEXT + " cc ops", is_parent=True,
             reply_count=2)
    recs += [a, b]
    for k, txt in enumerate(["Looking into it.", "Is it all hubs or only MX1?",
                             "Only MX1 as of now.", "Panel is back up, please confirm."]):
        recs.append(_rec(FF, t0 + 300 + k * 90 + 0.10 * (k + 1),
                         "U076430KSP4" if k % 2 == 0 else "U09HH8QKZ43", txt,
                         thread_ref=a["message_id"]))
    for k, txt in enumerate(["Raised with tech.", "any update ??"]):
        recs.append(_rec(AMS, t0 + 400 + k * 3600 + 0.30 * (k + 1),
                         "U0C1AAAAAA1" if k == 0 else "U09HH8QKZ43", txt,
                         thread_ref=b["message_id"]))
    return recs, {a["message_id"]: ["MX1"], b["message_id"]: ["MX1"],
                  "_cross_channel_duplicate": [a["message_id"], b["message_id"]],
                  "_seconds_apart": round(b["ts_epoch"] - a["ts_epoch"], 2)}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)

    r0828, e0828 = build_0828()
    (OUT / "2026-08-28.ndjson").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in r0828) + "\n", encoding="utf-8")

    r0905, e0905 = build_0905()
    (OUT / "2026-09-05.ndjson").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in r0905) + "\n", encoding="utf-8")

    # SUBSTANTIVE top-level: excludes channel_join/channel_leave. The brief's measurement
    # is "7 of 18 top-level", and that 18 is the substantive count, not the raw one.
    top = [r for r in r0828 if r["thread_ref"] is None and r["subtype"] is None]
    (OUT / "expected_tier_a.json").write_text(json.dumps({
        "_what": "message_id -> the EXACT set of DC codes Tier A must extract. Token sets, "
                 "never counts: every regex error in this project produced a plausible count.",
        "_measured": "7 of 18 top-level messages are code-bearing, yielding 10 distinct codes "
                     "with zero false positives.",
        "_expected_union": sorted({c for v in e0828.values() for c in v}),
        "_substantive_top_level": len(top),
        "_joins_excluded": sum(1 for r in r0828 if r["subtype"]),
        "_code_bearing": sum(1 for v in e0828.values() if v),
        "by_message": e0828,
    }, indent=2) + "\n", encoding="utf-8")

    (OUT / "expected_cross_post.json").write_text(json.dumps({
        "_what": "The confirmed cross-channel duplicate. Same author, near-identical text, "
                 "posted in BOTH in-scope channels seconds apart, threads forked.",
        "_why": "Per-channel dedupe creates two tickets on day one, and closure detection that "
                "looks at one channel leaves the other open forever.",
        **e0905,
    }, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {len(r0828)} records to 2026-08-28.ndjson "
          f"({len(top)} top-level, {sum(1 for v in e0828.values() if v)} code-bearing)")
    print(f"wrote {len(r0905)} records to 2026-09-05.ndjson "
          f"(cross-post {e0905['_seconds_apart']}s apart)")
    print(f"expected union: {sorted({c for v in e0828.values() for c in v})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
