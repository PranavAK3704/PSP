"""Read a Slack channel live and emit schema v2 NDJSON. READ ONLY.

── WHY THIS WRITES NDJSON INSTEAD OF GOING STRAIGHT TO SQLITE ────────────────────────────────
The live path and the export path converge on the SAME artefact, so everything downstream is
identical and `tools/validate.js` still gates both. A live read that bypassed the contract gate
would be a second, unvalidated way into the store — and the export exists precisely because
nobody trusted the first one.

It also means the backfill and the real-time path are the same code: `conversations.history`
with an `oldest` cursor is a backfill when the cursor is 90 days ago and a poll when it is 60
seconds ago.

── IT CANNOT WRITE TO SLACK ──────────────────────────────────────────────────────────────────
Not by discipline — by token. The app is installed with eight scopes, all of them `:history`
or `:read`, and no `chat:write` of any kind, so there is no write API reachable with this
credential. Verified against `auth.test`'s `X-OAuth-Scopes` header.

── THE FIELD MAPPING IS docs/field-map.md, EXACTLY ───────────────────────────────────────────
Three of these are counter-intuitive and all three are checked by the validator:

  · `thread_ref` is NULL on a parent, and the parent's ts on replies. Slack's raw semantics put
    `thread_ts == ts` on the parent; that is normalised away here, because grouping uses
    `thread_ref is None` to mean "top-level".
  · `ts_iso` is IST `+05:30`, never UTC `Z`, and must agree with `message_id` to the second.
  · `permalink` is CONSTRUCTED, not fetched. `chat.getPermalink` per record is unaffordable and
    the form is deterministic.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from . import sources

API = "https://slack.com/api"
IST = timezone(timedelta(hours=5, minutes=30))
_BACKEND = Path(__file__).resolve().parents[2]

_MENTION = re.compile(r"<@([UW][A-Z0-9]+)(?:\|[^>]*)?>")
_SUBTEAM = re.compile(r"<!subteam\^([A-Z0-9]+)(?:\|[^>]*)?>")
_CHANNEL_MENTION = re.compile(r"<!(?:channel|here|everyone)>")


class SlackError(RuntimeError):
    pass


def read_token(name: str = "slack_bot_token.txt") -> str:
    p = _BACKEND / "data" / name
    if not p.exists():
        raise SlackError(f"{p} not found — see config/slack/app-manifest.yaml for setup")
    return p.read_text().strip()


class SlackReader:
    """A thin, read-only client. Every method here is a GET; there is no send().

    Implements `sources.Source`. `container_name` is the protocol's name for what Slack calls a
    channel — see sources.py for why the schema keeps the Slack-flavoured field names.
    """

    #: Goes into every record and into the idempotency key, so it must never change for Slack.
    system = "slack"

    def __init__(self, token: str | None = None, *, pause: float = 1.2):
        self.token = token or read_token()
        self.pause = pause                     # Slack tier-3 methods allow ~50/min
        self._users: dict[str, dict] = {}
        self._host: str | None = None

    def _get(self, method: str, **params) -> dict:
        r = requests.get(f"{API}/{method}", params=params,
                         headers={"Authorization": f"Bearer {self.token}"}, timeout=30)
        d = r.json()
        if not d.get("ok"):
            err = d.get("error", "unknown")
            hint = {
                "not_in_channel": "invite the bot: /invite @<app> in that channel",
                "channel_not_found": "a PRIVATE channel is invisible until the bot is invited",
                "missing_scope": f"needs {d.get('needed')}, has {d.get('provided')}",
            }.get(err, "")
            raise SlackError(f"{method}: {err}" + (f" — {hint}" if hint else ""))
        time.sleep(self.pause)
        return d

    # ── lookups, cached ─────────────────────────────────────────────────────────────────────
    def host(self) -> str:
        if self._host is None:
            self._host = self._get("auth.test")["url"].split("//")[1].strip("/")
        return self._host

    def channel_name(self, channel_id: str) -> str | None:
        return self._get("conversations.info", channel=channel_id)["channel"].get("name")

    #: The protocol's spelling. Same call — a second surface says `container`, Slack says
    #: `channel`, and callers that already say `channel_name` keep working.
    def container_name(self, container_id: str) -> str | None:
        return self.channel_name(container_id)

    def user(self, uid: str | None) -> dict:
        if not uid:
            return {}
        if uid not in self._users:
            try:
                self._users[uid] = self._get("users.info", user=uid).get("user") or {}
            except SlackError:
                self._users[uid] = {}          # deleted user, or a bot_id — name stays null
        return self._users[uid]

    # ── the mapping ─────────────────────────────────────────────────────────────────────────
    def to_record(self, msg: dict, channel_id: str, channel_name: str | None,
                  workspace_id: str, fetched_at: str) -> dict:
        ts = msg["ts"]                                   # STRING, verbatim. Never float-cast.
        thread_ts = msg.get("thread_ts")
        text = msg.get("text") or ""
        uid = msg.get("user") or msg.get("bot_id")
        u = self.user(msg.get("user"))
        prof = u.get("profile") or {}

        files = []
        for f in (msg.get("files") or []):
            files.append({"id": f.get("id"), "name": f.get("name"), "title": f.get("title"),
                          "mimetype": f.get("mimetype"), "filetype": f.get("filetype"),
                          "size": f.get("size"), "url_private": f.get("url_private")})

        return {
            "schema_version": "2",
            "source": "slack:conversations.history+replies",
            # Half the idempotency key. Declared on every record rather than defaulted, so a
            # second surface cannot mint Slack-shaped keys by omission.
            "source_system": self.system,
            "workspace_id": workspace_id,
            "channel_id": channel_id,
            "channel_name": channel_name,
            "message_id": ts,
            "ts_epoch": float(ts),
            "ts_iso": ist_stamp(float(ts)),
            "author_id": uid,
            "author_name": u.get("real_name") or prof.get("display_name") or None,
            "author_email": prof.get("email"),
            "author_is_bot": bool(u.get("is_bot") or msg.get("bot_id")
                                  or msg.get("subtype") == "bot_message"),
            "text": text,
            "subtype": msg.get("subtype") or None,
            # NULL on the parent — a deliberate normalisation away from Slack's raw semantics
            "thread_ref": None if (thread_ts == ts) else (thread_ts or None),
            "is_thread_parent": bool(thread_ts and thread_ts == ts),
            "reply_count": int(msg.get("reply_count") or 0),
            "mentions": list(dict.fromkeys(_MENTION.findall(text))),
            "channel_mention": bool(_CHANNEL_MENTION.search(text)),
            "subteam_mentions": list(dict.fromkeys(_SUBTEAM.findall(text))),
            "attachments": files,
            "has_media": bool(files),
            "reactions": msg.get("reactions") or [],
            "permalink": f"https://{self.host()}/archives/{channel_id}/p{ts.replace('.', '')}",
            "edited_ts": (msg.get("edited") or {}).get("ts"),
            "fetched_at": fetched_at,
        }

    def fetch(self, channel_id: str, *, oldest: str | None = None,
              limit: int = 200, max_pages: int = 20) -> list[dict]:
        """Top-level messages plus every thread reply, as schema v2 records."""
        workspace_id = self._get("auth.test")["team_id"]
        cname = self.channel_name(channel_id)
        fetched_at = ist_stamp(time.time())

        raw, cursor, pages = [], None, 0
        while pages < max_pages:
            p = {"channel": channel_id, "limit": limit}
            if oldest:
                p["oldest"] = oldest
            if cursor:
                p["cursor"] = cursor
            d = self._get("conversations.history", **p)
            raw.extend(d.get("messages") or [])
            cursor = (d.get("response_metadata") or {}).get("next_cursor")
            pages += 1
            if not cursor:
                break

        # Thread replies. conversations.replies returns the parent first — dropped, since it is
        # already in `raw` and a duplicate would break the composite key's no-op guarantee.
        for m in list(raw):
            if m.get("thread_ts") == m.get("ts") and int(m.get("reply_count") or 0) > 0:
                d = self._get("conversations.replies", channel=channel_id, ts=m["ts"], limit=200)
                raw.extend([r for r in (d.get("messages") or []) if r.get("ts") != m["ts"]])

        recs = [self.to_record(m, channel_id, cname, workspace_id, fetched_at) for m in raw]
        recs.sort(key=lambda r: r["ts_epoch"])
        return recs


def ist_stamp(epoch: float) -> str:
    """IST +05:30 to the second — derived the same way tools/validate.js derives it, so the
    two can never disagree."""
    return (datetime.fromtimestamp(epoch + 19800, timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S") + "+05:30")


def write_ndjson(records: list[dict], out_dir: str | Path) -> dict[str, int]:
    """One file per IST day, which is what validate.js's day-placement check expects."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    buckets: dict[str, list[dict]] = {}
    for r in records:
        buckets.setdefault(r["ts_iso"][:10], []).append(r)
    for day, rows in buckets.items():
        rows.sort(key=lambda r: r["ts_epoch"])
        (out_dir / f"{day}.ndjson").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return {d: len(v) for d, v in sorted(buckets.items())}


# Registered at import so `sources.build("slack")` works without the caller knowing this module
# exists. A second surface adds one line like this and changes nothing else.
sources.register("slack", SlackReader)
