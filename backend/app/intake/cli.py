"""The intake CLI. One subcommand per stage, plus run-all.

    python -m app.intake.cli run-all --raw data/intake/fixtures
    python -m app.intake.cli extract --run-id r1        # re-run one stage, zero API calls
    python -m app.intake.cli evaluate --run-id r1

── THIS IS THE REPO'S FIRST MULTI-SUBCOMMAND CLI, DELIBERATELY ───────────────────────────────
Everything in backend/scripts/ is one flat argparse parser per file, and `check_all.py`
dispatches a hardcoded list by subprocess. That convention works for one-verb scripts and does
not fit ten stages that share a store, a run id and a config set. So this uses
`add_subparsers` — stdlib argparse, no typer, no click, matching the dependency posture
everywhere else.

── EVERY STAGE IS INDEPENDENTLY RE-RUNNABLE ──────────────────────────────────────────────────
Each subcommand takes a --run-id and rewrites only its own rows. That is what makes "change one
regex and re-run from stage 4" true, and why `llm_cache` is not scoped to a run: stage 6 replays
from cache and a second pass costs nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.intake import (  # noqa: E402
    classify, dedupe, emit, evaluate, evidence, extract, group, loadstage, noise, qualify,
    register, report, slack_source, store,
)

DEFAULT_RAW = BACKEND / "data" / "intake" / "raw"


def _show(name: str, payload) -> None:
    print(f"\n── {name} {'─' * max(0, 74 - len(name))}")
    print(json.dumps(payload, indent=2, default=str))


def _run_id(args) -> str:
    return args.run_id or datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S")


def cmd_load(args) -> int:
    _show("load", loadstage.load(args.raw, run_id=_run_id(args),
                                 skip_validate=args.skip_validate))
    return 0


def cmd_qualify(args) -> int:
    # `sampler` is the one LLM call outside stage 6. Not wired yet: with none supplied the gate
    # reports every deterministic metric and leaves sampled_issue_share None, so a channel is
    # never silently qualified on a missing number.
    _show("qualify", qualify.run(_run_id(args)))
    return 0


def cmd_gate(args) -> int:
    _show("gate", noise.run(_run_id(args)))
    return 0


def cmd_extract(args) -> int:
    _show("extract", extract.run(_run_id(args)))
    return 0


def cmd_evidence(args) -> int:
    """Positive-evidence scoring — the tier that decides whether a message is an issue AT ALL."""
    _show("evidence", evidence.run(_run_id(args)))
    return 0


def cmd_group(args) -> int:
    _show("group", group.run(_run_id(args), join_weak=args.join_weak))
    return 0


def cmd_register(args) -> int:
    _show("register", register.run(_run_id(args)))
    return 0


def cmd_classify(args) -> int:
    """Assign a disposition to each issue by nearest labelled exemplar, or NOVEL."""
    _show("classify", classify.run(_run_id(args)))
    return 0


def cmd_dedupe(args) -> int:
    _show("dedupe", dedupe.run(_run_id(args)))
    return 0


def cmd_pull(args) -> int:
    """Read a channel live into day-partitioned NDJSON. READ ONLY — the token has no write
    scope, so there is no code path from here back into Slack."""
    reader = slack_source.SlackReader()
    recs = reader.fetch(args.channel, oldest=args.oldest)
    written = slack_source.write_ndjson(recs, args.raw)
    _show("pull", {"channel": args.channel, "records": len(recs), "files": written,
                   "out": str(args.raw),
                   "_next": f"python -m app.intake.cli run-all --raw {args.raw}"})
    return 0


def cmd_explain(args) -> int:
    """THE OUTPUT LAYER: what the pipeline decided about every message, and why.

    One line per message with the rule that fired at each stage. This is the artefact that
    makes a wrong answer diagnosable instead of arguable — "why did this land here" is
    answerable for every row, including the ones nothing matched.
    """
    rid = _run_id(args)
    con = store.connect()
    try:
        rows = con.execute(
            "SELECT m.channel_id, m.message_id, m.text, m.subtype, m.has_media, m.thread_ref, "
            "       f.gated, f.gate_rule, f.informational, f.informational_rule, "
            "       f.informational_borderline, a.issue_id, a.rule AS grp_rule, a.confidence, "
            "       e.decision AS ev_decision, e.score AS ev_score, e.reasons AS ev_reasons "
            "FROM messages m "
            "LEFT JOIN message_flags f ON f.run_id=? AND f.channel_id=m.channel_id "
            "     AND f.message_id=m.message_id "
            "LEFT JOIN assignments a ON a.run_id=? AND a.channel_id=m.channel_id "
            "     AND a.message_id=m.message_id "
            "LEFT JOIN evidence e ON e.run_id=? AND e.channel_id=m.channel_id "
            "     AND e.message_id=m.message_id "
            "ORDER BY m.ts_epoch", (rid, rid, rid)).fetchall()
        if not rows:
            print(f"no messages for run_id {rid!r}", file=sys.stderr)
            return 2

        drafts = {r["issue_id"]: r for r in con.execute(
            "SELECT issue_id, suppressed, suppressed_reason, title FROM ticket_drafts "
            "WHERE run_id=?", (rid,))}

        print(f"\n{'=' * 100}\nWHAT THE PIPELINE DID  —  run_id {rid}\n{'=' * 100}")
        for r in rows:
            ents = con.execute(
                "SELECT kind, value, tier FROM entities WHERE run_id=? AND channel_id=? "
                "AND message_id=? ORDER BY kind, value",
                (rid, r["channel_id"], r["message_id"])).fetchall()
            txt = " ".join((r["text"] or "").split())[:72] or "(empty text)"
            if r["has_media"]:
                txt += "  [+file]"
            print(f"\n  {txt}")

            if r["gated"]:
                print(f"     GATED       {r['gate_rule']}   -> not an issue")
                continue
            if r["gate_rule"]:                      # the kept-on-purpose case
                print(f"     kept        {r['gate_rule']}")
            if r["ev_decision"] in ("not_an_issue", "orphan", "weak"):
                label = {"not_an_issue": "NOT AN ISSUE", "orphan": "ORPHAN",
                         "weak": "WEAK"}[r["ev_decision"]]
                print(f"     {label:<11} score={r['ev_score']}  {r['ev_reasons']}")
                if r["ev_decision"] == "not_an_issue":
                    continue
            if r["informational"]:
                mark = " BORDERLINE" if r["informational_borderline"] else ""
                print(f"     INFO        {r['informational_rule']}{mark}   -> flagged, not a ticket")
            if ents:
                print("     entities    " + ", ".join(
                    f"{e['kind']}={e['value']}" + (f"(tier {e['tier']})" if e["tier"] else "")
                    for e in ents))
            else:
                print("     entities    none")
            if r["issue_id"]:
                short = r["issue_id"].rsplit("-", 1)[-1]
                print(f"     issue       {short}  via {r['grp_rule']} (conf {r['confidence']})")
                d = drafts.get(r["issue_id"])
                if d and r["grp_rule"] == "new_issue":
                    if d["suppressed"]:
                        print(f"     TICKET      held back — {d['suppressed_reason'][:64]}")
                    else:
                        print(f"     TICKET      WOULD RAISE — {d['title'][:64]}")
            else:
                print(f"     issue       unassigned ({r['grp_rule']}) -> stage 6")
        print(f"\n{'=' * 100}")
        return 0
    finally:
        con.close()


def cmd_emit(args) -> int:
    """Draft a ticket per issue. Creates nothing: the only sink in phase 1 is the dry run."""
    _show("emit", emit.run(_run_id(args), require_identifier=args.require_identifier))
    return 0


def cmd_report(args) -> int:
    _show("report", report.run(_run_id(args), args.out, redact_pii=not args.no_redact))
    return 0


def cmd_evaluate(args) -> int:
    r = evaluate.run(_run_id(args))
    if r.get("CAVEAT"):
        print(f"\n!! {r['CAVEAT']}\n")
    _show("evaluate", r)
    return 0


def cmd_adjudicate(args) -> int:
    print(
        "adjudicate is NOT IMPLEMENTED.\n\n"
        "It is the only stage that calls a model, and it is deliberately last: `evaluate`\n"
        "reports grouping accuracy with stage 6 disabled, and if that number is good enough\n"
        "this stage is optional. Run:\n\n"
        "    python -m app.intake.cli evaluate --run-id <id>\n\n"
        "and read THE_NUMBER_grouping_without_stage_6 before funding it.\n\n"
        "What it still needs: the on-disk prompt cache, the run-scoped spend guard with its\n"
        "own ledger (NOT llm_spend.json — an intake backfill would eat the deployment's $45\n"
        "chat budget), and a haiku tier in config/models.yaml.",
        file=sys.stderr)
    return 2


def cmd_run_all(args) -> int:
    rid = _run_id(args)
    print(f"run_id: {rid}   store: {store.db_path()}")
    _show("1 load", loadstage.load(args.raw, run_id=rid, skip_validate=args.skip_validate))
    _show("3 gate", noise.run(rid))
    _show("4 extract", extract.run(rid))
    # Evidence runs AFTER extract because identifiers are its strongest signal, and BEFORE
    # grouping because it decides what is allowed to anchor an issue.
    _show("4b evidence", evidence.run(rid))
    _show("2 qualify", qualify.run(rid))
    _show("5 group", group.run(rid, join_weak=args.join_weak))
    _show("7 register", register.run(rid))
    # After register (it labels ISSUES, not messages) and before emit (the ticket carries it).
    _show("7b classify", classify.run(rid))
    _show("8 dedupe", dedupe.run(rid))
    _show("10 emit", emit.run(rid, require_identifier=args.require_identifier))
    if args.out:
        _show("9 report", report.run(rid, args.out, redact_pii=not args.no_redact))
    cmd_explain(argparse.Namespace(run_id=rid))
    r = evaluate.run(rid)
    if r.get("CAVEAT"):
        print(f"\n!! {r['CAVEAT']}\n")
    _show("evaluate", r)
    print(f"\nrun_id {rid} — stage 6 (adjudicate) was NOT run, so zero API calls were made, "
          f"and no ticket was created: the only sink in phase 1 is the dry run.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="intake", description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-id", help="reuse a run id to re-run one stage over existing rows")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        s = sub.add_parser(name, help=help_)
        s.set_defaults(fn=fn)
        return s

    for name, fn, h, raw in [
            ("pull", cmd_pull, "read a Slack channel LIVE into NDJSON (read-only)", True),
            ("explain", cmd_explain, "what the pipeline decided about every message, and why",
             False),
            ("load", cmd_load, "read NDJSON, gate it on tools/validate.js, write SQLite", True),
            ("qualify", cmd_qualify, "score every channel; exclude with the number attached",
             False),
            ("gate", cmd_gate, "noise gate + informational classifier", False),
            ("extract", cmd_extract, "entities, DC codes tier A and tier B", False),
            ("evidence", cmd_evidence,
             "is this a partner/ops issue at all? (the play-arena gate)", False),
            ("group", cmd_group, "assign messages to issues", False),
            ("adjudicate", cmd_adjudicate, "stage 6 (Claude) — not implemented", False),
            ("register", cmd_register, "one row per issue", False),
            ("classify", cmd_classify,
             "disposition by nearest labelled exemplar, or NOVEL", False),
            ("dedupe", cmd_dedupe, "cross-channel duplicates", False),
            ("emit", cmd_emit,
             "draft a ticket per issue — creates NOTHING, dry-run sink only", False),
            ("report", cmd_report, "write the xlsx", False),
            ("evaluate", cmd_evaluate, "score against golden labels", False),
            ("run-all", cmd_run_all, "every implemented stage, in order", True)]:
        s = add(name, fn, h)
        # Also on every subparser, so `run-all --run-id demo` works. On the top level alone it
        # would have to PRECEDE the subcommand, which reads like a typo when it fails.
        s.add_argument("--run-id", help="reuse a run id to re-run one stage over existing rows")
        if raw:
            s.add_argument("--raw", default=str(DEFAULT_RAW),
                           help="day-partitioned NDJSON directory (default: data/intake/raw)")
            s.add_argument("--skip-validate", action="store_true",
                           help="skip tools/validate.js — only when it was run elsewhere")
        if name == "pull":
            s.add_argument("--channel", required=True, help="channel id, e.g. C09JY7YLB3L")
            s.add_argument("--oldest", default=None,
                           help="unix ts to read from. A backfill and a poll are the same call "
                                "with a different cursor")
        if name in ("emit", "run-all"):
            s.add_argument("--require-identifier", action="store_true",
                           help="hold back issues with no DC code, mobile, waybill or ticket "
                                "id — nobody outside the conversation can act on those. Off by "
                                "default: whether it is true depends on how the desk works")
        if name in ("group", "run-all"):
            s.add_argument("--join-weak", action="store_true",
                           help="also join on DC code. Off by default: a DC identifies a "
                                "PLACE, so this merges every issue a hub raised in the window")
        if name in ("report", "run-all"):
            s.add_argument("--out", default=None if name == "run-all" else "intake-report.xlsx",
                           help="xlsx output path")
            s.add_argument("--no-redact", action="store_true",
                           help="do NOT mask mobiles/emails/pilot ids. Default is to mask.")
    return p


def main(argv=None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    if getattr(args, "run_id", None) is None:
        args.run_id = None
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
