"""The `Source` protocol — what a surface must provide to feed this pipeline.

Slack is one surface. The stated target is to listen on every surface partners actually use:
WhatsApp, email, and whatever comes next. This is the seam that makes a second one an
implementation rather than a rewrite.

    class WhatsAppSource:
        system = "whatsapp"
        def fetch(self, container_id, *, oldest=None): -> list[dict]   # schema v2 records
        def container_name(self, container_id): -> str | None

Register it, and `run-all` works unchanged.

── THE FIELD NAMES STAY SLACK-FLAVOURED, DELIBERATELY ────────────────────────────────────────
`channel_id` is not renamed to `container_id` in the schema or the database, and that is a
choice worth defending because it looks like sloppiness.

Renaming it means touching every SQL statement in every stage, the golden labels CSV, the
fixtures, the report writer and the validator, to gain nothing a comment cannot give. The
portability problem was never the WORD "channel" — it is the hard-coded Slack FORMATS: a
message_id that must look like `1788511676.054669`, a permalink that must be on slack.com, and
a ts_epoch required to equal `parseFloat(message_id)`. Those are what actually reject a
WhatsApp record, and those are what v2 fixes.

So: `channel_id` means THE CONTAINER — a Slack channel, a WhatsApp group, an email thread. One
comment, no refactor, no risk. If the name grates later it is a mechanical rename, and it can be
done when something is gained by it.

── WHAT A NEW SURFACE ACTUALLY HAS TO GET RIGHT ──────────────────────────────────────────────
Three things, in order of how badly they break if wrong:

1. `message_id` must be STABLE and unique within the container. It is half the primary key and
   the whole idempotency key — `sha256(source_system, channel_id, message_id)` — so an id that
   changes between fetches creates duplicate tickets for one message, which is precisely the
   failure this project exists to prevent. Email's `Message-ID` header qualifies. A row number
   does not.
2. `ts_epoch` must be the real send time. Recurrence windows and first-response latency are
   computed from it.
3. `text` must be the partner's words, markup and all. Do not prettify — `strip_markup` for the
   surface handles that at the point of use, and a stage that needs the raw form still has it.

Everything else degrades gracefully: no threads means every message is top-level, no reactions
means an empty list, no permalink means None.
"""
from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

#: Surfaces this pipeline knows how to validate and normalise. Adding a name here is not enough
#: to make it work — `tools/validate.js` needs the matching format rules, or records will be
#: rejected at the gate with a confusing message about Slack timestamps.
SYSTEMS = ("slack", "whatsapp", "email")


class SourceError(RuntimeError):
    pass


@runtime_checkable
class Source(Protocol):
    """A read-only reader for one surface.

    READ-ONLY IS PART OF THE CONTRACT, not an accident of the Slack implementation. There is no
    `send`, no `reply`, no `react`. Acknowledging a raiser is a separate decision that needs its
    own approval on every surface it touches, and keeping it out of this protocol means a new
    surface cannot quietly arrive with a write path attached.
    """

    #: One of SYSTEMS. Goes into every record and into the idempotency key.
    system: str

    def fetch(self, container_id: str, *, oldest: str | None = None) -> list[dict]:
        """Records in schema v2, newest-inclusive, for one container."""
        ...

    def container_name(self, container_id: str) -> str | None:
        """Human-readable name, or None. Used in reports, never as a key."""
        ...


# ── markup ────────────────────────────────────────────────────────────────────────────────────
# Each surface wraps the partner's words in its own syntax. Stripping it is not cosmetic: the
# noise gate, the evidence gate and the ticket title all read this text, and leaving `<@U07AB>`
# in makes a one-word message look substantial while leaving an HTML email looking like a wall
# of tags.

_SLACK_MARKUP = re.compile(
    r"<@[UW][A-Z0-9]+(?:\|[^>]*)?>"        # user mention
    r"|<!subteam\^[A-Z0-9]+(?:\|[^>]*)?>"  # group mention
    r"|<!(?:channel|here|everyone)>"       # broadcast
    r"|<[^>]+>")                           # links and anything else angle-wrapped

_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_ENTITY = re.compile(r"&(nbsp|amp|lt|gt|quot|#39|apos);")
_ENTITIES = {"nbsp": " ", "amp": "&", "lt": "<", "gt": ">", "quot": '"', "#39": "'",
             "apos": "'"}
#: Quoted history in a reply. Everything from here down is someone ELSE's earlier message, and
#: treating it as this partner's words makes every reply look like a fresh issue.
_EMAIL_QUOTE = re.compile(
    r"^\s*(?:>.*$|On .{0,120}\bwrote:\s*$|-{2,}\s*Original Message\s*-{2,}\s*$"
    r"|_{5,}\s*$|From:\s.*$)", re.M)


def strip_markup(text: str, system: str = "slack") -> str:
    """Plain text as a person would read it, for the surface it came from.

    Unknown surfaces fall through to a light clean rather than raising: a new surface that has
    not taught this function its syntax should produce slightly noisy text, not a crash in the
    noise gate.
    """
    t = text or ""
    if system == "slack":
        t = _SLACK_MARKUP.sub(" ", t)
    elif system == "email":
        # Order matters — cut the quoted history off BEFORE stripping tags, or a quoted block
        # wrapped in <blockquote> loses the marker that identifies it.
        t = _EMAIL_QUOTE.split(t)[0]
        t = _HTML_TAG.sub(" ", t)
        t = _HTML_ENTITY.sub(lambda m: _ENTITIES.get(m.group(1), " "), t)
    elif system == "whatsapp":
        # WhatsApp formatting marks are inline and carry no metadata: *bold*, _italic_,
        # ~strike~, ```mono```. Removing the marks would corrupt identifiers that legitimately
        # contain them, so only the fenced block markers go.
        t = t.replace("```", " ")
    return re.sub(r"\s+", " ", t).strip()


# ── registry ──────────────────────────────────────────────────────────────────────────────────

_REGISTRY: dict[str, type] = {}


def register(system: str, factory) -> None:
    if system not in SYSTEMS:
        raise SourceError(
            f"{system!r} is not in SYSTEMS. Add it there AND give tools/validate.js its format "
            f"rules first — otherwise every record it produces is rejected at the gate with a "
            f"message about Slack timestamps, which is a bad afternoon.")
    _REGISTRY[system] = factory


def build(system: str, **kw):
    """Instantiate a registered source. Unknown names fail loudly rather than defaulting to
    Slack, which would silently read the wrong surface."""
    if system not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "none registered"
        raise SourceError(f"no source registered for {system!r} (have: {known})")
    return _REGISTRY[system](**kw)


def available() -> list[str]:
    return sorted(_REGISTRY)
