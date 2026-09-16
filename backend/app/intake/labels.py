"""The human label store — the only data in this pipeline a machine cannot regenerate.

Everything else here is derived: the corpus rebuilds from `kapture_audits.json` in seconds, the
exemplar index rebuilds from the corpus, the registry rebuilds from the DC export. Delete any of
them and a script puts them back.

A human confirmation is not like that. It cost someone's attention, and nothing can recreate it.
So it gets its own store, outside every generated file.

── WHY NOT JUST WRITE INTO exemplars.json ────────────────────────────────────────────────────
Because `scripts/build_exemplars.py` OVERWRITES that file. A human label written there would
survive exactly until the next rebuild and then vanish silently — the worst kind of data loss,
since nothing errors and the classifier just quietly gets worse. Confirmations live here and are
MERGED IN at build time instead.

── WHY APPEND-ONLY ───────────────────────────────────────────────────────────────────────────
Corrections matter as much as labels. If someone marks a message `cod_shortfall` and a week
later changes it to `payment_not_received`, both lines stay and the later one wins on read. That
gives a free audit trail of who changed what — and if two people disagree, the disagreement is
visible rather than overwritten. An append is also atomic enough that a crash mid-write costs
one line, not the file.

── THE ID IS THE TEXT, NOT THE MESSAGE ───────────────────────────────────────────────────────
Keyed on a hash of the normalised text, deliberately using the SAME scheme as
`scripts/build_corpus_from_audits.py` (sha256 of whitespace-collapsed text, first 16 hex). So
confirming a message that happens to match a corpus row PROMOTES that row from silver to gold.
The two stores agree on identity for free.

The consequence to know: the same wording confirmed twice is one label, not two. That is what
you want for exemplars — an exemplar is a piece of language, not an incident. Recurrence of an
INCIDENT is counted separately, in `ticket_drafts.occurrence_count`.

── NOT-AN-ISSUE IS RECORDED, NOT YET FED BACK ────────────────────────────────────────────────
"Not an issue" confirmations are stored and reported, but nothing consumes them yet: the noise
gate in `evidence.py` is rule-based, so a negative example has no slot to go into the way a
positive one becomes an exemplar. They are being collected because they are the scarce thing —
real off-topic partner messages, of which the corpus has four. Wiring them into the gate is a
separate change and is listed as such in `docs/intake-pending.md`. Claiming otherwise would
overstate what this does.

── PII ───────────────────────────────────────────────────────────────────────────────────────
A confirmation carries the partner's own message text, so this file inherits the live-pull rule
and is gitignored. That has a real cost — the labels live on one machine and are not backed up
by the repo — and `export_confirmations()` exists so they can be moved deliberately rather than
by a commit that would leak the text.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]

#: $INTAKE_LABELS redirects the store, the same way $INTAKE_DB redirects the database.
#: `scripts/_contain.py` symlinks directories, so a containment run that does not redirect this
#: writes real labels into the real file — and the labels are the one thing here that cannot be
#: regenerated, which makes a stray write worse than a stray row in a rebuildable table.
STORE = Path(os.environ.get("INTAKE_LABELS")
             or _BACKEND / "data" / "intake" / "labels" / "confirmations.jsonl")

#: The two things a human can say about a NOVEL item. Anything else is rejected rather than
#: stored — an unrecognised decision would sit in the file looking authoritative and be silently
#: skipped by every reader.
DECISIONS = ("label", "not_an_issue")

NOT_AN_ISSUE = "__not_an_issue__"


class LabelError(ValueError):
    pass


def text_id(text: str) -> str:
    """Stable id for a piece of language.

    MUST stay identical to `scripts/build_corpus_from_audits.py`'s id scheme — that shared
    definition is what lets a confirmation promote a corpus row from silver to gold. If one side
    changes, the promotion stops happening and nothing fails loudly, so treat this as a contract.
    """
    return hashlib.sha256(re.sub(r"\s+", " ", text or "").strip().encode()).hexdigest()[:16]


def confirm(text: str, decision: str, *, disposition: str | None = None,
            confirmed_by: str, source_id: str | None = None, permalink: str | None = None,
            note: str | None = None, path: Path | None = None) -> dict:
    """Record one human judgement. Returns the stored row.

    Raises rather than guessing: a label with no disposition, or a disposition attached to a
    not-an-issue, is a caller bug and should surface at the call site instead of becoming a row
    that quietly means nothing.
    """
    path = Path(path) if path else STORE
    text = (text or "").strip()
    if not text:
        raise LabelError("cannot confirm an empty message")
    if decision not in DECISIONS:
        raise LabelError(f"decision must be one of {DECISIONS}, got {decision!r}")
    if decision == "label":
        if not (disposition or "").strip():
            raise LabelError("a 'label' confirmation needs a disposition")
        disposition = disposition.strip()
        if disposition == "NOVEL":
            raise LabelError("NOVEL is what the classifier says when it does not know — it is "
                             "not a label a human can assign. Name the class or mark it "
                             "not_an_issue.")
    else:
        if disposition:
            raise LabelError("a 'not_an_issue' confirmation cannot also carry a disposition")
        disposition = NOT_AN_ISSUE

    if not (confirmed_by or "").strip():
        raise LabelError("confirmed_by is required — an unattributed label cannot be questioned")

    row = {
        "id": text_id(text),
        "text": text,
        "decision": decision,
        "disposition": disposition,
        "label_provenance": "gold",       # a human said it; this is what outranks silver
        "confirmed_by": confirmed_by.strip(),
        "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_id": source_id,
        "permalink": permalink,
        "note": (note or "").strip() or None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def load(path: Path | None = None) -> dict[str, dict]:
    """Resolved confirmations, id -> latest row. Later lines win, so a correction supersedes.

    A malformed line is SKIPPED rather than fatal. This file is the one thing here that cannot
    be regenerated, so a single bad line must not make the other labels unreadable.
    """
    path = Path(path) if path else STORE
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("id") and row.get("decision") in DECISIONS:
            out[row["id"]] = row
    return out


def labelled(path: Path | None = None) -> list[dict]:
    """Confirmations that name a disposition — the ones that become gold exemplars."""
    return [r for r in load(path).values() if r["decision"] == "label"]


def not_an_issue(path: Path | None = None) -> list[dict]:
    """Confirmed noise. Not consumed yet — see the module docstring."""
    return [r for r in load(path).values() if r["decision"] == "not_an_issue"]


def stats(path: Path | None = None) -> dict:
    rows = load(path)
    lab = [r for r in rows.values() if r["decision"] == "label"]
    by: dict[str, int] = {}
    for r in lab:
        by[r["disposition"]] = by.get(r["disposition"], 0) + 1
    return {
        "confirmations": len(rows),
        "labelled": len(lab),
        "not_an_issue": len(rows) - len(lab),
        "dispositions": len(by),
        "by_disposition": dict(sorted(by.items(), key=lambda kv: -kv[1])),
        "confirmers": sorted({r["confirmed_by"] for r in rows.values()}),
    }


def export_confirmations(dest: Path, *, include_text: bool = True,
                         path: Path | None = None) -> int:
    """Write resolved confirmations to `dest` so they can be moved off this machine.

    `include_text=False` drops the message body and keeps the id, so a label set can be shared
    or backed up where the partner's words must not go. Such an export cannot rebuild exemplars
    — the text IS the exemplar — but it does preserve the promotion of any corpus row whose id
    matches, which is the part that would otherwise be lost forever.
    """
    rows = list(load(path).values())
    if not include_text:
        rows = [{k: v for k, v in r.items() if k not in ("text", "permalink", "source_id")}
                for r in rows]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(
        {"_what": "Human label confirmations from the intake NOVEL queue.",
         "_text_included": include_text,
         "_count": len(rows), "confirmations": rows}, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return len(rows)
