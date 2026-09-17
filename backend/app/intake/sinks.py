"""Ticket sinks — the destinations a draft can actually be created in.

`TicketSink` lives in emit.py and is the whole contract: `create(draft) -> external_ref`.
Everything here implements it. Nothing above this file knows which one is in use, which is what
lets the pipeline outlive any particular ticketing system.

── WHY GOOGLE SHEETS IS A REASONABLE FIRST REAL SINK ─────────────────────────────────────────
Not a toy. It makes tickets EXIST, which is the thing that has been missing: until something is
really created, acknowledging a raiser is a promise the system cannot keep, and the whole
seven-canned-replies failure this project exists to fix starts with promises nobody honoured.

A sheet also puts the register in front of ops without a deployment, a login, or a migration,
and the swap to a real backend later is one class.

── THE ONE THING THAT MUST BE RIGHT ──────────────────────────────────────────────────────────
Idempotency is enforced on the SHEET side, not here. `docs/appscript/Code.gs` looks the key up
before appending and returns the original row if it is already there. A sink that trusts the
caller not to retry is not idempotent — it is lucky. Retries are normal: timeouts, re-runs, two
operators. This client can and does re-POST, and the contract is that doing so is harmless.

── IT STILL CANNOT WRITE TO SLACK ────────────────────────────────────────────────────────────
Creating a ticket and telling the raiser about it are separate decisions. This does the first.
The second needs a Slack write scope, a reinstall and an approval — and should not happen until
the first is real, which is exactly what this makes true.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import requests

_BACKEND = Path(__file__).resolve().parents[2]
URL_FILE = _BACKEND / "data" / "appscript_url.txt"
#: Shared secret for POST. Without it the deployment URL alone would be enough to create
#: tickets — and the URL has to be shareable, because the status page lives at the same origin.
SECRET_FILE = _BACKEND / "data" / "appscript_secret.txt"


class SinkError(RuntimeError):
    pass


def read_url(path: Path = URL_FILE) -> str:
    if not path.exists():
        raise SinkError(
            f"{path} not found. Deploy docs/appscript/Code.gs as a Web App, then:\n"
            f"  printf '%s' '<exec url>' > {path} && chmod 600 {path}\n"
            f"The URL is a write endpoint — treat it as a credential.")
    u = path.read_text().strip()
    if not u.startswith("https://script.google.com/"):
        raise SinkError(f"{path} does not look like an Apps Script exec URL")
    return u


class GoogleSheetTicketSink:
    """Appends one row per ticket. Idempotent because the sheet enforces it."""

    name = "google_sheet"

    def __init__(self, url: str | None = None, *, secret: str | None = None,
                 timeout: float = 30.0, dry_run: bool = False):
        self.url = url or read_url()
        self.secret = secret if secret is not None else read_secret()
        self.timeout = timeout
        self.dry_run = dry_run
        self.created = 0
        self.already_existed = 0
        #: idempotency_key -> the status-page URL the raiser can open. Populated as tickets are
        #: created, so the acknowledgement step has somewhere to point without a second lookup.
        self.status_urls: dict[str, str] = {}

    def status_url(self, token: str) -> str:
        sep = "&" if "?" in self.url else "?"
        return f"{self.url}{sep}t={token}"

    def create(self, draft) -> str:
        payload = asdict(draft) if hasattr(draft, "__dataclass_fields__") else dict(draft)
        # The sheet is a human artefact and the description is the raiser's own words; entities
        # go as JSON so a later importer can parse them without re-deriving anything.
        if self.dry_run:
            return f"DRYPOST-{payload['idempotency_key'][:12]}"

        r = requests.post(self.url, data=json.dumps({**payload, "secret": self.secret}),
                          headers={"Content-Type": "application/json"},
                          timeout=self.timeout, allow_redirects=True)
        try:
            body = r.json()
        except ValueError:
            # Apps Script returns an HTML error page when the deployment is misconfigured —
            # usually "Anyone" access not granted, or the script not deployed at all.
            raise SinkError(
                f"Apps Script returned non-JSON (HTTP {r.status_code}). Check the deployment "
                f"is a Web App, executing as you, accessible to your org. First 200 chars: "
                f"{r.text[:200]!r}")
        if not body.get("ok"):
            raise SinkError(f"sheet refused the ticket: {body.get('error')}")

        if body.get("created"):
            self.created += 1
        else:
            # The key was already there. This is the idempotency guarantee working, not an
            # error — a retry must be harmless and must return the ORIGINAL reference.
            self.already_existed += 1
        # The token is what makes an acknowledgement worth sending: a link to THIS ticket that
        # is safe to hand a partner. A repeat create returns the original token, so a retried
        # acknowledgement points at the same page rather than a second one.
        if body.get("token"):
            self.status_urls[payload["idempotency_key"]] = self.status_url(body["token"])
        return body["ref"]


class FileTicketSink:
    """Appends JSON lines to a local file. For testing the wiring without a network call."""

    name = "file"

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Same counters as the sheet sink, so the CLI can report "created" vs "already there"
        # regardless of which sink is in use. A sink that cannot say how many it created is a
        # sink you cannot audit.
        self.created = 0
        self.already_existed = 0
        #: Same shape as the sheet sink's, so the acknowledgement step works identically against
        #: either. The file sink has no real page, so these are clearly-labelled placeholders.
        self.status_urls: dict[str, str] = {}
        self._seen: dict[str, str] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    d = json.loads(line)
                    self._seen[d["idempotency_key"]] = d["ref"]

    def create(self, draft) -> str:
        payload = asdict(draft) if hasattr(draft, "__dataclass_fields__") else dict(draft)
        key = payload["idempotency_key"]
        self.status_urls[key] = f"file://ticket/{key[:16]}"
        if key in self._seen:
            self.already_existed += 1
            return self._seen[key]          # same guarantee, enforced locally
        self.created += 1
        ref = f"FILE-{len(self._seen) + 1}"
        self._seen[key] = ref
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**payload, "ref": ref}, ensure_ascii=False) + "\n")
        return ref


def read_secret(path: Path = SECRET_FILE) -> str:
    """The shared secret, or "" when not configured.

    Deliberately NOT fatal when absent: the sheet answers `server_not_configured` with a message
    naming the fix, which is a better error than one raised here before anything was attempted.
    """
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


def build(name: str, **kw):
    """Resolve a sink by name. Unknown names fail loudly — a typo must never silently fall back
    to the dry run and leave someone believing tickets were created."""
    if name in ("dry", "dry_run"):
        from .emit import DryRunTicketSink
        return DryRunTicketSink()
    if name in ("sheet", "google_sheet", "appscript"):
        return GoogleSheetTicketSink(**kw)
    if name == "file":
        return FileTicketSink(kw.get("path") or (_BACKEND / "data" / "intake" / "tickets.jsonl"))
    raise SinkError(f"unknown sink {name!r} — one of: dry, sheet, file")
