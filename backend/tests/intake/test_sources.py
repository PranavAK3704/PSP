"""The Source protocol and per-surface markup."""
from __future__ import annotations

import pytest

from app.intake import sources


def test_slack_registers_itself():
    from app.intake import slack_source  # noqa: F401  (import is the registration)
    assert "slack" in sources.available()


def test_the_slack_reader_satisfies_the_protocol():
    from app.intake.slack_source import SlackReader
    assert SlackReader.system == "slack"
    for m in ("fetch", "container_name"):
        assert callable(getattr(SlackReader, m))


def test_an_unregistered_surface_fails_loudly():
    """Defaulting to Slack would silently read the wrong surface."""
    with pytest.raises(sources.SourceError, match="no source registered"):
        sources.build("whatsapp")


def test_a_surface_outside_SYSTEMS_cannot_register():
    with pytest.raises(sources.SourceError, match="not in SYSTEMS"):
        sources.register("telegram", object)


def test_the_protocol_has_no_write_method():
    """Read-only is part of the contract, not an accident of the Slack implementation — a new
    surface must not be able to arrive with a send path attached."""
    assert not {"send", "reply", "post", "react"} & set(dir(sources.Source))


# ── markup ───────────────────────────────────────────────────────────────────────────────────

def test_slack_markup_is_stripped():
    out = sources.strip_markup("<@U07AB> please check <!channel> <https://x|link>", "slack")
    assert "U07AB" not in out and "!channel" not in out
    assert "please check" in out


def test_an_email_reply_drops_the_quoted_history():
    """Treating someone else's quoted message as this partner's words makes every reply look
    like a fresh issue."""
    body = ("Payment nahi aaya for DC NQS.\n\n"
            "On Mon, 1 Sep 2026 at 10:04, Support <s@x.com> wrote:\n"
            "> Please share the waybill number\n")
    out = sources.strip_markup(body, "email")
    assert "Payment nahi aaya" in out
    assert "waybill" not in out and "wrote" not in out


def test_email_html_becomes_readable_text():
    out = sources.strip_markup("<p>paisa <b>nahi</b> aaya</p>&nbsp;&amp; stuck", "email")
    assert out == "paisa nahi aaya & stuck"


def test_whatsapp_formatting_does_not_corrupt_identifiers():
    """*bold* and _italic_ marks are inline and carry no metadata. Stripping the characters
    would eat parts of identifiers that legitimately contain them."""
    out = sources.strip_markup("*URGENT* DC_NQS_01 payment", "whatsapp")
    assert "DC_NQS_01" in out


def test_an_unknown_surface_degrades_rather_than_raising():
    """A surface that has not taught this its syntax should produce slightly noisy text, not a
    crash inside the noise gate."""
    assert sources.strip_markup("  hello   world ", "telegram") == "hello world"
