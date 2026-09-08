# Field map — Slack API → schema v1.1

26 keys: v1's 25, plus `has_media`. Emit exactly these, in this order. Nothing
else. `conform()` throwing on unexpected keys is correct — keep that.

## The three you had to guess

| Field | Your guess | Actual | Why |
|---|---|---|---|
| `ts_iso` | UTC with `Z` | **IST offset, `+05:30`** | The validated 467-record export uses `+05:30` throughout and my validator checks it against the IST-derived stamp. UTC `Z` fails. |
| `thread_ref` | parent's ts on parent **and** replies | **`null` on the parent**, parent's ts on replies only | This is a deliberate normalisation away from Slack's raw semantics (where a parent carries `thread_ts == ts`). My grouping stage uses `thread_ref == null` to mean "this is a top-level message". Your version would make every parent look like a reply. |
| `channel_mention` | boolean | **boolean** ✓ | Correct. True if any of `<!channel>`, `<!here>`, `<!everyone>` appears. |

## Key count

You emitted 27, not 25. The deltas:

**Drop these four** — not in the contract:
- `ts` (duplicates `message_id`; keep it as an internal variable, don't emit)
- `date_ist` (I derive it from `ts_iso`)
- `datetime_ist` (same)
- `parent_ts`, `is_reply`, `reply_users_count`, `latest_reply`, `edited_by`, `bot_id` if any of those survived

**Add these two** — you're missing them:
- `permalink`
- `fetched_at`

## Full mapping

| v1.1 key | Source | Notes |
|---|---|---|
| `schema_version` | literal | `"1.1"` |
| `source` | literal | free text describing the route, e.g. `"slack:conversations.history+replies"` |
| `workspace_id` | `auth.test` → `team_id` | |
| `channel_id` | `--channel` | |
| `channel_name` | `conversations.info` → `channel.name` | |
| `message_id` | `msg.ts` | **string, verbatim.** Primary key. `(channel_id, message_id)` is the composite PK on my side. |
| `ts_epoch` | `Number(msg.ts)` | full precision, never floored |
| `ts_iso` | derived | ISO 8601, **`+05:30`**, seconds precision, e.g. `2026-09-04T14:17:56+05:30` |
| `author_id` | `msg.user \|\| msg.bot_id` | |
| `author_name` | `users.list` → `profile.real_name \|\| profile.display_name` | null acceptable |
| `author_email` | `users.list` → `profile.email` | needs `users:read.email`. null acceptable |
| `author_is_bot` | `users.list` → `is_bot`, or `msg.bot_id`, or `subtype === "bot_message"` | boolean |
| `text` | `msg.text` | **markup verbatim.** Empty string, never null. |
| `subtype` | `msg.subtype \|\| null` | carried through unmodified, joins included |
| `thread_ref` | `msg.thread_ts === msg.ts ? null : (msg.thread_ts \|\| null)` | **null on parents** |
| `is_thread_parent` | `Boolean(msg.thread_ts && msg.thread_ts === msg.ts)` | |
| `reply_count` | `msg.reply_count \|\| 0` | |
| `mentions` | parsed from `text` | array of user IDs, deduped, order of appearance |
| `channel_mention` | parsed from `text` | boolean |
| `subteam_mentions` | parsed from `text` | array of subteam IDs |
| `attachments` | `msg.files` | `[{id, name, title, mimetype, filetype, size, url_private}]`. Never `[]` when files exist — keep your thrown assertion. |
| `has_media` | `attachments.length > 0` | v1.1 addition |
| `reactions` | `msg.reactions \|\| []` | |
| `permalink` | **constructed offline** | see below |
| `edited_ts` | `msg.edited?.ts \|\| null` | |
| `fetched_at` | now | ISO 8601 with `+05:30`, same format as `ts_iso` |

## permalink — construct it, don't leave it null

You were right that `chat.getPermalink` per record is unaffordable. But it's
deterministic, so build it and skip the call:

```js
// auth.test returns url: "https://meesho.slack.com/"
const host = new URL(auth.url).host;                 // meesho.slack.com
const permalink = `https://${host}/archives/${channelId}/p${msg.ts.replace('.', '')}`;
```

Verified against the real export: ts `1788511676.054669` →
`https://meesho.slack.com/archives/C09JY7YLB3L/p1788511676054669`. `validate.js`
checks the tail against `message_id`, so a mistake here fails loudly.

`null` is accepted by the validator, but populate it — my register links back to
the source message and I'd rather not reconstruct it in two places.

## Scopes for the token ask

Your `users:read.email` catch is right and I've added it. Full list:

- `groups:history` — `conversations.history` + `conversations.replies` on private channels
- `users:read` — `author_name`
- `users:read.email` — `author_email` (separate scope)

User token (`xoxp`), held by a channel member, internal customer-built app in
the Meesho workspace. Bot token will not work for thread replies.
