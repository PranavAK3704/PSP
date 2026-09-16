# Adding a surface

Slack is one surface. The goal is to listen wherever partners actually talk — WhatsApp, email,
and whatever comes next. This is what that costs now.

## The short version

1. Add the name to `SYSTEMS` in [sources.py](../backend/app/intake/sources.py).
2. Add its format rules to `SURFACES` in [validate.js](../tools/validate.js).
3. Teach `strip_markup` its syntax, if it has any.
4. Write a reader with `fetch()` and `container_name()`, and call `sources.register()`.

Nothing else changes. No stage knows which surface a message came from.

## What schema v2 actually changed

v1.1 did not describe a message — it described a **Slack** message. Three of its checks were
pure Slack and would have rejected a perfectly valid WhatsApp record:

| Check | Why it was Slack-only |
|---|---|
| `message_id` matches `^\d{10}\.\d{6}$` | That is a Slack `ts`. A WhatsApp id is `wamid.…`; an email id is an RFC 5322 `Message-ID`. |
| `ts_epoch` equals `parseFloat(message_id)` | Only true where the id encodes the send time. Slack's does; the other two don't. |
| `permalink` is on `slack.com` and its tail matches the id | WhatsApp has no per-message web link at all. |

Those checks are not incidental — each one caught a real bug (a float-cast primary key, a
`Math.floor()` truncation, a permalink pointing at the wrong message). So v2 **keeps every one
of them for Slack** and dispatches on a new required field, `source_system`, rather than
weakening them for everybody. Add a surface without giving it rules and its records are rejected
with a message saying exactly that, instead of being measured against Slack's timestamp format.

`source_system` is also **half the idempotency key** — `sha256(source_system, channel_id,
message_id)` — so a WhatsApp message and a Slack message that happen to share a container id and
a message id are still two different tickets. That is why a v2 record must declare it and cannot
default: an unlabelled WhatsApp record would mint a Slack-shaped key and the retry guarantee
would quietly stop holding.

v1 and v1.1 records still validate untouched. They predate the field and only ever carried
Slack, so that is what they are loaded as.

## Why the fields are still called `channel_id`

Because renaming it to `container_id` means touching every SQL statement in every stage, the
golden-labels CSV, the fixtures, the report writer and the validator — to gain nothing a comment
cannot give. The portability problem was never the word "channel"; it was the hard-coded Slack
*formats* above.

**`channel_id` means the container**: a Slack channel, a WhatsApp group, an email thread. If the
name grates later it is a mechanical rename, and it can be done when something is actually
gained by it.

## What a new surface has to get right

Three things, in the order of how badly they break:

1. **`message_id` must be stable and unique within the container.** It is half the primary key
   and the whole idempotency key, so an id that changes between fetches creates duplicate
   tickets for one message — precisely the failure this project exists to prevent. Email's
   `Message-ID` header qualifies. A row number does not.
2. **`ts_epoch` must be the real send time.** Recurrence windows and first-response latency are
   computed from it. Where the id carries no timestamp, this field is the *only* record of when
   the message was sent, so the validator requires it to be a positive epoch.
3. **`text` must be the partner's words, markup and all.** Don't prettify — `strip_markup()`
   handles that where it is needed, and stages that want the raw form still have it.

Everything else degrades gracefully: no threads means every message is top-level, no reactions
means an empty list, no permalink means `null`.

## Markup is not cosmetic

The noise gate, the evidence gate and the ticket title all read the message text. Leaving
`<@U07AB>` in makes a one-word message look substantial; leaving an HTML email unstripped makes
every message look like a wall of tags.

Email needs one thing the others don't: **the quoted history has to come off before the tags
do.** A reply that keeps the quoted original reads as a fresh issue containing someone else's
problem, and the `> ` markers that identify it are lost once a `<blockquote>` is stripped.

WhatsApp deliberately keeps its `*bold*` and `_italic_` marks. They carry no metadata, and
removing the characters would corrupt identifiers that legitimately contain them — `DC_NQS_01`
is a real shape.

## The one thing the protocol will not let you add

There is no `send`, no `reply`, no `react`. Read-only is part of the contract, not an accident
of the Slack implementation — acknowledging a raiser is a separate decision that needs its own
approval on each surface it touches. Keeping it out of the protocol means a new surface cannot
arrive with a write path quietly attached.

## Status

| Surface | Reader | Validator rules | Markup |
|---|---|---|---|
| Slack | ✅ `slack_source.py` | ✅ | ✅ |
| WhatsApp | ❌ not written | ✅ `wamid.…` | ✅ |
| Email | ❌ not written | ✅ RFC 5322 | ✅ quoted-history + HTML |

The readers are the remaining work, and each is roughly a day. WhatsApp needs a Business API
account and a webhook endpoint — unlike Slack, there is no polling equivalent, so it needs the
queue and a public callback URL before it can run at all.
