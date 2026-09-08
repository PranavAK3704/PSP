"""The store's schema is a contract too — these are the properties later stages rely on."""
from __future__ import annotations

import pytest

from app.intake import store


def test_schema_applies_and_is_idempotent(intake_db):
    c1 = store.connect()
    tables = {r[0] for r in c1.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    c1.close()

    c2 = store.connect()                       # second connect must change nothing
    tables2 = {r[0] for r in c2.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert c2.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 1
    c2.close()

    assert tables == tables2
    assert {"messages", "entities", "assignments", "issues", "duplicates",
            "llm_cache", "adjudications", "stage_runs"} <= tables


def test_messages_primary_key_is_composite(con):
    """(channel_id, message_id) — NOT message_id alone. Cross-posting is confirmed real: the
    same Slack ts can legitimately appear in two channels."""
    pk = [r[1] for r in con.execute("PRAGMA table_info(messages)") if r[5]]
    assert pk == ["channel_id", "message_id"]


def test_same_message_id_in_two_channels_coexists(con):
    """The regression the composite key exists to prevent."""
    row = ("{ch}", "1788482771.760339", "1.1", "s", "T0", "n", 1788482771.760339,
           "2026-09-04T06:16:11+05:30", "U1", "n", None, 0, "t", None, None, 1, 0,
           "[]", 0, "[]", "[]", 0, "[]", None, None, None, "f", "now")
    cols = ("channel_id, message_id, schema_version, source, workspace_id, channel_name, "
            "ts_epoch, ts_iso, author_id, author_name, author_email, author_is_bot, text, "
            "subtype, thread_ref, is_thread_parent, reply_count, mentions_json, "
            "channel_mention, subteam_mentions_json, attachments_json, has_media, "
            "reactions_json, permalink, edited_ts, fetched_at, src_file, loaded_at")
    q = f"INSERT INTO messages ({cols}) VALUES ({', '.join('?' * 28)})"
    for ch in ("C09JY7YLB3L", "C08T6NLL77H"):
        con.execute(q, (ch,) + row[1:])
    con.commit()
    assert con.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2

    with pytest.raises(Exception):             # but the same key twice in ONE channel does not
        con.execute(q, ("C09JY7YLB3L",) + row[1:])


def test_reset_stage_clears_only_that_stage_and_run(con):
    for run in ("r1", "r2"):
        con.execute("INSERT INTO entities (run_id, channel_id, message_id, kind, value) "
                    "VALUES (?,?,?,?,?)", (run, "C1", "1.1", "dc_code", "NQS"))
        con.execute("INSERT INTO assignments (run_id, channel_id, message_id, issue_id) "
                    "VALUES (?,?,?,?)", (run, "C1", "1.1", "I1"))
    con.commit()

    store.reset_stage(con, "extract", "r1")

    assert con.execute("SELECT COUNT(*) FROM entities WHERE run_id='r1'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM entities WHERE run_id='r2'").fetchone()[0] == 1
    # stage 5 is untouched: re-running extract must not silently discard grouping for r1
    assert con.execute("SELECT COUNT(*) FROM assignments WHERE run_id='r1'").fetchone()[0] == 1


def test_llm_cache_is_not_run_scoped(con):
    """This is what makes a re-run cost nothing — the cache outlives the run that filled it."""
    cols = [r[1] for r in con.execute("PRAGMA table_info(llm_cache)")]
    assert "run_id" not in cols
    assert cols[0] == "cache_key"


def test_reset_stage_rejects_an_unknown_stage(con):
    with pytest.raises(KeyError):
        store.reset_stage(con, "not_a_stage", "r1")
