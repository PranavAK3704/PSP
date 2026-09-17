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
#: The spreadsheet id for the Sheets-API path — the token between /d/ and /edit in the sheet URL.
SHEET_ID_FILE = _BACKEND / "data" / "sheet_id.txt"


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


class GoogleSheetsApiTicketSink:
    """Writes tickets straight to a Google Sheet through the Sheets API.

    ── WHY THIS EXISTS ALONGSIDE THE APPS SCRIPT SINK ────────────────────────────────────────
    An Apps Script Web App restricted to an organisation cannot be called by a script: the
    pipeline has no Google session, so its POST receives a sign-in page rather than JSON. Many
    Workspace tenants disable the "Anyone" setting that would fix that — and even where it is
    allowed, it means standing up a publicly writable endpoint.

    This path avoids the question entirely. There is NO web endpoint. The pipeline authenticates
    as a real Google identity and writes to the sheet directly, so nothing is exposed to the
    internet and no shared secret has to be managed.

        gcloud auth application-default login \
            --scopes=https://www.googleapis.com/auth/spreadsheets,\
        https://www.googleapis.com/auth/cloud-platform

    ── WHAT IT GIVES UP ──────────────────────────────────────────────────────────────────────
    Idempotency moves from the sheet to the client. Apps Script could take a `LockService` lock
    and make the check-then-append atomic; the Sheets API cannot. With one pipeline process that
    is equivalent, because the key is derived from the source message and the check still
    happens on every create. With two processes writing at once there is a genuine race, and the
    honest mitigation is not to run two — or to go back to Apps Script for that reason.

    It also has no status page: the Apps Script deployment serves that. A sheet written this way
    is an internal register, and the partner-facing link needs somewhere else to live.
    """

    name = "google_sheets_api"
    SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
    TAB = "tickets"
    HEADERS = ("idempotency_key", "created_at", "source_system", "source_id", "permalink",
               "title", "disposition", "dc_code", "raiser", "occurrence_count",
               "first_raised_at", "last_raised_at", "reply_count", "first_response_s",
               "state", "flags", "entities", "description", "public_token", "status_note",
               "updated_at")

    def __init__(self, spreadsheet_id: str | None = None, *, dry_run: bool = False):
        self.spreadsheet_id = spreadsheet_id or read_sheet_id()
        if not self.spreadsheet_id:
            raise SinkError(
                f"no spreadsheet id — put the id from the sheet's URL in {SHEET_ID_FILE}. "
                f"It is the long token between /d/ and /edit.")
        self.dry_run = dry_run
        self.created = 0
        self.already_existed = 0
        self.status_urls: dict[str, str] = {}
        self._svc = None
        self._keys: dict[str, str] | None = None

    def _service(self):
        if self._svc is None:
            import google.auth
            from googleapiclient.discovery import build as gbuild
            try:
                creds, _ = google.auth.default(scopes=list(self.SCOPES))
            except Exception as e:                                        # noqa: BLE001
                raise SinkError(
                    "no Google credentials. Run:\n"
                    "  gcloud auth application-default login "
                    "--scopes=https://www.googleapis.com/auth/spreadsheets,"
                    "https://www.googleapis.com/auth/cloud-platform\n"
                    f"({type(e).__name__}: {e})")
            self._svc = gbuild("sheets", "v4", credentials=creds, cache_discovery=False)
        return self._svc

    def _load_keys(self) -> dict[str, str]:
        """idempotency_key -> VAL-<row>. Read once; kept current as we append."""
        if self._keys is not None:
            return self._keys
        sheets = self._service().spreadsheets()
        try:
            got = sheets.values().get(spreadsheetId=self.spreadsheet_id,
                                      range=f"{self.TAB}!A2:A").execute()
        except Exception as e:                                            # noqa: BLE001
            if "Unable to parse range" in str(e):
                # First run: the tab does not exist yet.
                sheets.batchUpdate(
                    spreadsheetId=self.spreadsheet_id,
                    body={"requests": [{"addSheet": {"properties": {"title": self.TAB}}}]}
                ).execute()
                sheets.values().update(
                    spreadsheetId=self.spreadsheet_id, range=f"{self.TAB}!A1",
                    valueInputOption="RAW", body={"values": [list(self.HEADERS)]}).execute()
                got = {"values": []}
            else:
                raise SinkError(f"cannot read the sheet: {e}")
        self._keys = {row[0]: f"VAL-{i + 2}"
                      for i, row in enumerate(got.get("values") or []) if row}
        return self._keys

    def create(self, draft) -> str:
        payload = asdict(draft) if hasattr(draft, "__dataclass_fields__") else dict(draft)
        key = payload["idempotency_key"]
        if self.dry_run:
            return f"DRYSHEET-{key[:12]}"

        seen = self._load_keys()
        if key in seen:
            self.already_existed += 1
            return seen[key]                  # the original reference, as the contract requires

        import uuid
        from datetime import datetime, timezone
        token = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        ents = payload.get("entities") or {}
        row = [key, now, payload.get("source_system", ""), payload.get("source_id", ""),
               payload.get("source_permalink", "") or "", payload.get("title", ""),
               payload.get("intent") or "", payload.get("dc_code") or "",
               payload.get("raiser") or "", payload.get("occurrence_count") or 1,
               payload.get("first_raised_at") or "", payload.get("last_raised_at") or "",
               payload.get("reply_count") or 0,
               "" if payload.get("first_response_latency_s") is None
               else payload["first_response_latency_s"],
               payload.get("state") or "open", "; ".join(payload.get("flags") or []),
               json.dumps(ents, ensure_ascii=False), payload.get("description", ""),
               token, "", now]
        self._service().spreadsheets().values().append(
            spreadsheetId=self.spreadsheet_id, range=f"{self.TAB}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": [row]}).execute()

        self.created += 1
        ref = f"VAL-{len(seen) + 2}"
        seen[key] = ref
        self.status_urls[key] = f"sheet://{self.spreadsheet_id}#{token}"
        return ref


def read_sheet_id(path: Path = SHEET_ID_FILE) -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


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
    if name in ("sheets_api", "gsheets"):
        return GoogleSheetsApiTicketSink(**kw)
    if name == "file":
        return FileTicketSink(kw.get("path") or (_BACKEND / "data" / "intake" / "tickets.jsonl"))
    raise SinkError(f"unknown sink {name!r} — one of: dry, sheet, sheets_api, file")
