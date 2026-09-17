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
    classify, dedupe, emit, evaluate, evidence, extract, group, labels, loadstage, noise,
    notify, qualify, register, report, rollup, sinks, slack_source, store,
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


def cmd_watch(args) -> int:
    """Pull every listening channel and run the pipeline, on a loop, until you stop it.

    The gap this closes: `pull` reads Slack into files and `run-all` reads those files, so a
    message posted after the last pull is invisible — the register looks broken when in fact
    nothing ran. The live dashboard polls, but it writes to its own store and not to PSP, so it
    does not help either.

    This is the production shape minus Socket Mode: the same poll the backfill uses, on a timer,
    into a real sink. Every stage is idempotent and the ticket key comes from the source
    message, so running it every minute forever creates each ticket exactly once.
    """
    import time as _t
    channels = [c.strip() for spec in args.channel for c in spec.split(",") if c.strip()]
    if not channels:
        raise SystemExit("--channel needs at least one channel id")
    reader = slack_source.SlackReader(pause=0.2)
    sink = sinks.build(args.sink)
    if args.sink not in ("dry", "dry_run"):
        print(f"  !! sink={args.sink} — this CREATES tickets.", file=sys.stderr)
    print(f"  watching {len(channels)} channel(s) every {args.every}s — ctrl-c to stop\n",
          flush=True)

    n = 0
    while True:
        n += 1
        stamp = datetime.now().strftime("%H:%M:%S")
        try:
            got = 0
            for ch in channels:
                recs = reader.fetch(ch, oldest=args.oldest)
                slack_source.write_ndjson(recs, args.raw)
                got += len(recs)
            rid = args.run_id or "watch"
            con = store.connect()
            try:
                loadstage.load(args.raw, run_id=rid, con=con, skip_validate=True)
                for fn in (noise.run, extract.run, evidence.run, qualify.run, group.run,
                           register.run, classify.run, dedupe.run):
                    fn(rid, con=con)
                r = emit.run(rid, con=con, sink=sink,
                             require_identifier=args.require_identifier)
                if hasattr(sink, "report_channels"):
                    sink.report_channels(rollup.channel_rollup(con, rid))
            finally:
                con.close()
            print(f"  [{stamp}] poll {n}: {got} messages, {r.get('issues', 0)} issues, "
                  f"{getattr(sink, 'created', 0)} ticket(s) created so far", flush=True)
        except KeyboardInterrupt:
            raise
        except Exception as e:                                            # noqa: BLE001
            # A transient Slack or PSP failure must not end the watch — that is the one job it
            # has. Report it and try again on the next tick.
            print(f"  [{stamp}] poll {n} FAILED: {type(e).__name__}: {e}",
                  file=sys.stderr, flush=True)
        try:
            _t.sleep(args.every)
        except KeyboardInterrupt:
            print("\n  stopped")
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
    """Draft a ticket per issue, and create it if a real sink is named.

    --sink defaults to `dry`. Creating tickets somewhere real is always an EXPLICIT act: the
    difference between a draft and a ticket is a promise to whoever raised it.
    """
    sink = sinks.build(args.sink)
    if args.sink not in ("dry", "dry_run"):
        print(f"\n  !! sink={args.sink} — this CREATES tickets. Ctrl-C now if unintended.\n",
              file=sys.stderr)
    r = emit.run(_run_id(args), sink=sink,
                 require_identifier=args.require_identifier)
    for attr in ("created", "already_existed"):
        if hasattr(sink, attr):
            r[f"sink_{attr}"] = getattr(sink, attr)
    _show("emit", r)
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


def cmd_labels(args) -> int:
    """What humans have confirmed, and whether the index has caught up yet.

    Worth its own command because confirming in the dashboard does NOT change classification on
    its own — the exemplar index is a built artifact. Someone who confirms twenty items and sees
    no difference should be able to find out why without reading the source.
    """
    if args.export:
        dest = Path(args.export)
        n = labels.export_confirmations(dest, include_text=not args.no_text)
        print(f"wrote {n} confirmation(s) to {dest}"
              + ("  (text omitted)" if args.no_text else ""))
        return 0

    s = labels.stats()
    print(f"\n  store        {labels.STORE}")
    print(f"  confirmed    {s['confirmations']}  "
          f"({s['labelled']} labelled, {s['not_an_issue']} not-an-issue)")
    print(f"  by           {', '.join(s['confirmers']) or '—'}")
    for d, n in s["by_disposition"].items():
        print(f"      {d:<28} {n:>4}")

    # The gap that actually matters: a confirmation only changes behaviour once it is built in.
    m = classify.load_matcher()
    in_index = sum(1 for d in (m.docs if m else []) if d["provenance"] == "gold")
    if s["labelled"] and in_index < s["labelled"]:
        print(f"\n  !! {s['labelled']} labelled but only {in_index} gold exemplars in the index."
              f"\n     Run: python scripts/build_exemplars.py")
    elif s["labelled"]:
        print(f"\n  index is current — {in_index} gold exemplars live")
    else:
        print("\n  nothing confirmed yet — the dashboard's NOVEL queue is where this fills up")
    return 0


def _notifier(args):
    """Build the acknowledgement channel, or None. Default is OFF — telling real people
    something is not a thing that should happen because a flag was forgotten."""
    if args.notify in (None, "off"):
        return None
    if args.notify not in ("email", "email-dry"):
        raise SystemExit(f"--notify must be one of: off, email-dry, email (got {args.notify!r})")
    return notify.EmailNotifier(
        dry_run=(args.notify == "email-dry"),
        redirect_to=args.notify_to,
        allow_domains=tuple(d for d in (args.notify_allow or "").split(",") if d))


def cmd_notify(args) -> int:
    n = _notifier(args)
    if n is None:
        print("--notify is off. Use --notify email-dry to see what would be sent.",
              file=sys.stderr)
        return 2
    _show("notify", notify.run(_run_id(args), n, max_sends=args.notify_max))
    return 0


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
    _sink = sinks.build(args.sink)
    if args.sink not in ("dry", "dry_run"):
        print(f"\n  !! sink={args.sink} — this CREATES tickets.\n", file=sys.stderr)
    _show("10 emit", emit.run(rid, sink=_sink,
                              require_identifier=args.require_identifier))
    if args.out:
        _show("9 report", report.run(rid, args.out, redact_pii=not args.no_redact))
    cmd_explain(argparse.Namespace(run_id=rid))
    r = evaluate.run(rid)
    if r.get("CAVEAT"):
        print(f"\n!! {r['CAVEAT']}\n")
    _show("evaluate", r)
    # The listening-channel panel. Reported after emit so ticket counts are final, and only
    # when the sink can carry it — the local file sink has nowhere to put this.
    if hasattr(_sink, "report_channels"):
        _con = store.connect()
        try:
            _rows = rollup.channel_rollup(_con, rid)
        finally:
            _con.close()
        n = _sink.report_channels(_rows)
        print(f"\n  listening channels reported: {n}/{len(_rows)}")

    # Acknowledgement runs LAST and reads the sink's status links, because a message with
    # nowhere to look is exactly what was rejected in the first place.
    _n = _notifier(args)
    if _n is not None:
        if not _n.dry_run and not _n.redirect_to:
            print("\n  !! acknowledgements will be sent to REAL raisers.\n", file=sys.stderr)
        _show("11 notify", notify.run(rid, _n,
                                      status_urls=getattr(_sink, "status_urls", {}),
                                      max_sends=args.notify_max))
    made = getattr(_sink, "created", 0)
    print(f"\nrun_id {rid} — stage 6 (adjudicate) was NOT run, so zero API calls were made. "
          + (f"Sink={args.sink}: {made} ticket(s) CREATED."
             if args.sink not in ("dry", "dry_run")
             else "No ticket was created — sink=dry."))
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
            ("watch", cmd_watch,
             "pull + run the pipeline on a loop, so new messages arrive by themselves", True),
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
            ("notify", cmd_notify,
             "tell each raiser their ticket exists, with a link to its status", False),
            ("labels", cmd_labels,
             "what humans confirmed, and whether the index has caught up", False),
            ("run-all", cmd_run_all, "every implemented stage, in order", True)]:
        s = add(name, fn, h)
        # Also on every subparser, so `run-all --run-id demo` works. On the top level alone it
        # would have to PRECEDE the subcommand, which reads like a typo when it fails.
        s.add_argument("--run-id", help="reuse a run id to re-run one stage over existing rows")
        if name == "watch":
            s.add_argument("--channel", required=True, action="append",
                           help="channel id; repeat or comma-separate for several")
            s.add_argument("--every", type=float, default=60.0,
                           help="seconds between polls (default 60)")
            s.add_argument("--oldest", default=None)
        if name in ("notify", "run-all", "watch"):
            s.add_argument("--notify", default="off",
                           choices=["off", "email-dry", "email"],
                           help="off (default) sends nothing. email-dry renders and records "
                                "nothing. email actually sends.")
            s.add_argument("--notify-to",
                           help="REDIRECT every acknowledgement to this one address instead of "
                                "the real raisers. Use it for any demo against a live channel — "
                                "without it you mail actual partners.")
            s.add_argument("--notify-allow", default="",
                           help="comma-separated domains that may receive real mail. Empty and "
                                "with no --notify-to means nobody: this fails closed.")
            s.add_argument("--notify-max", type=int, default=20,
                           help="hard cap on sends per run (default 20)")
        if name == "labels":
            s.add_argument("--export", help="write confirmations to a file so they can leave "
                                            "this machine (they are gitignored)")
            s.add_argument("--no-text", action="store_true",
                           help="omit the partner wording from the export")
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
        if name in ("emit", "run-all", "watch"):
            s.add_argument("--sink", default="dry",
                           help="dry (default, creates nothing) | psp (PSP's own register) | "
                                "file. Anything but `dry` CREATES tickets.")
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
