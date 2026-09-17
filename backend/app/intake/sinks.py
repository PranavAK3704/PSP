"""Ticket sinks — the destinations a draft can actually be created in.

`TicketSink` lives in emit.py and is the whole contract: `create(draft) -> external_ref`.
Everything here implements it. Nothing above this file knows which one is in use, which is what
lets the pipeline outlive any particular ticketing system.

── WHY PSP AND NOT A SPREADSHEET ─────────────────────────────────────────────────────────────
Two Google-backed sinks were built and both are gone, because every one of them failed on the
same thing: an auth system we do not control. An Apps Script Web App restricted to the domain
cannot be called by a script at all, and the Sheets API needs a GCP project nobody here has
rights on. Neither was fixable from a laptop.

PSP's auth IS ours. Agents already log into it, tokens are ours to issue, Turso makes the data
durable, and the register sits next to the work rather than in a separate tab nobody opens. The
detour was not wasted — the idempotency contract and the per-ticket status token came out of it
and carry straight over — but the destination was wrong from the start.

── THE ONE THING THAT MUST BE RIGHT ──────────────────────────────────────────────────────────
Creating the same ticket twice must be impossible. The key is derived from the SOURCE MESSAGE —
sha256(source_system, channel_id, message_id) — so the same Slack message yields the same key on
every run from every machine, and the server rejects the duplicate rather than trusting the
client not to retry. A sink that is merely never retried is not idempotent, it is lucky.

── IT STILL CANNOT WRITE TO SLACK ────────────────────────────────────────────────────────────
Creating a ticket and telling the raiser about it are separate decisions. This does the first.
The second lives in notify.py and needs its own approval per surface.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import requests

_BACKEND = Path(__file__).resolve().parents[2]
#: Where PSP lives, and the credentials to reach it with. Mode 600, gitignored by data/*.txt.
PSP_URL_FILE = _BACKEND / "data" / "psp_url.txt"
#: email:password for a dedicated PSP account with the `agent` role. NOT a pasted token —
#: PSP's session tokens expire after 12 hours, so a token in a file means the pipeline works
#: today and fails silently tomorrow night. The sink logs in and re-logs in on its own.
PSP_LOGIN_FILE = _BACKEND / "data" / "psp_login.txt"


class SinkError(RuntimeError):
    pass


def _read(path: Path, what: str) -> str:
    if not path.exists():
        raise SinkError(f"{path} not found — {what}")
    v = path.read_text(encoding="utf-8").strip()
    if not v:
        raise SinkError(f"{path} is empty — {what}")
    return v


class PspTicketSink:
    """Creates tickets in PSP itself, over its own authenticated API.

    The register then lives where the agents already work, behind the login they already have,
    in the database PSP already makes durable. No third-party auth stands between the pipeline
    and its own output.

    Idempotency is enforced SERVER-SIDE, in the API: it looks the key up and returns the
    existing ticket rather than creating a second one. That is the same contract the spreadsheet
    versions had, and it has to live on the server for the same reason — a client that promises
    not to retry is making a promise it cannot keep across timeouts, re-runs and two operators.
    """

    name = "psp"

    def __init__(self, base_url: str | None = None, *, login: str | None = None,
                 timeout: float = 30.0, dry_run: bool = False):
        self.base_url = (base_url or _read(
            PSP_URL_FILE, "put PSP's base URL there, e.g. https://valmo-psp.onrender.com")
        ).rstrip("/")
        cred = login if login is not None else _read(
            PSP_LOGIN_FILE, "put email:password for a PSP account with the `agent` role there")
        if ":" not in cred:
            raise SinkError(f"{PSP_LOGIN_FILE} must hold email:password")
        self.email, self.password = cred.split(":", 1)
        self.token: str | None = None
        self.timeout = timeout
        self.dry_run = dry_run
        self.created = 0
        self.already_existed = 0
        self.status_urls: dict[str, str] = {}

    def _login(self) -> str:
        r = requests.post(f"{self.base_url}/api/auth/login",
                          json={"email": self.email, "password": self.password},
                          timeout=self.timeout)
        if r.status_code == 401:
            raise SinkError(
                f"PSP rejected these credentials. Check {PSP_LOGIN_FILE}, and that the account "
                f"exists with the `agent` role.")
        try:
            tok = r.json().get("token")
        except ValueError:
            raise SinkError(f"PSP login returned non-JSON (HTTP {r.status_code}): "
                            f"{r.text[:200]!r}")
        if not tok:
            raise SinkError(f"PSP login returned no token (HTTP {r.status_code})")
        self.token = tok
        return tok

    def create(self, draft) -> str:
        payload = asdict(draft) if hasattr(draft, "__dataclass_fields__") else dict(draft)
        key = payload["idempotency_key"]
        if self.dry_run:
            return f"DRYPSP-{key[:12]}"

        def post():
            return requests.post(f"{self.base_url}/api/intake/tickets", json=payload,
                                 headers={"Authorization": f"Bearer {self.token}"},
                                 timeout=self.timeout)

        if self.token is None:
            self._login()
        r = post()
        if r.status_code in (401, 403):
            # The 12h token expired mid-run, or this is a long-lived process. Log in once more
            # and retry; only a second failure is a real one. Re-POSTing is safe because the
            # server deduplicates on the idempotency key.
            self._login()
            r = post()
        if r.status_code in (401, 403):
            raise SinkError(
                f"PSP refused the request after re-authenticating (HTTP {r.status_code}). "
                f"Check the account in {PSP_LOGIN_FILE} still has the `agent` role.")
        try:
            body = r.json()
        except ValueError:
            raise SinkError(f"PSP returned non-JSON (HTTP {r.status_code}): {r.text[:200]!r}")
        if not body.get("ok"):
            raise SinkError(f"PSP refused the ticket: {body.get('error')}")

        if body.get("created"):
            self.created += 1
        else:
            # The key was already there. This is the guarantee working, not an error.
            self.already_existed += 1
        if body.get("status_token"):
            self.status_urls[key] = f"{self.base_url}/t/{body['status_token']}"
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
        # VAL- rather than FILE-: this IS the ticket register when it is the configured sink,
        # and the run summary already names which sink produced it. A reference that changes
        # shape with the backend is a reference nobody can quote.
        ref = f"VAL-{len(self._seen) + 1}"
        self._seen[key] = ref
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**payload, "ref": ref}, ensure_ascii=False) + "\n")
        return ref


def build(name: str, **kw):
    """Resolve a sink by name. Unknown names fail loudly — a typo must never silently fall back
    to the dry run and leave someone believing tickets were created."""
    if name in ("dry", "dry_run"):
        from .emit import DryRunTicketSink
        return DryRunTicketSink()
    if name == "psp":
        return PspTicketSink(**kw)
    if name == "file":
        return FileTicketSink(kw.get("path") or (_BACKEND / "data" / "intake" / "tickets.jsonl"))
    raise SinkError(f"unknown sink {name!r} — one of: dry, file, psp")
