"""Durable state — makes the mutable JSON stores survive a redeploy / restart.

On Render's free tier there is NO persistent disk, so the state dir ($PSP_STATE_DIR)
is wiped on every deploy and on idle restarts. To keep authored content (SOPs, domain
brains, governance framework, user accounts) and operational logs (concern log, audits,
traces, cpd) durable, we mirror each state file into Turso (libSQL over HTTP) — the same
DB the loss data already uses — in a dedicated key-value table `psp_state`.

Design: each store keeps its existing local file as a fast cache; Turso is the durable
truth. `durable_path(name)` returns a Path-like handle whose exists/read_text/write_text
go through Turso (mirrored to the local file). If Turso is NOT configured (local dev with
no TURSO_* env), it degrades to a plain local file — behaviour identical to before.

Coherence: a per-process write-through cache serves repeated reads without extra round
trips and stays correct because only this app writes the `psp_state` table.
"""
from __future__ import annotations

import json
import os
import sys as _sys
from pathlib import Path

from .state_paths import state_path
from .substrate import turso_http

_TABLE = os.environ.get("PSP_STATE_TABLE", "psp_state")   # override for isolated testing
_url: str | None = None
_tok: str | None = None
_inited = False
_cache: dict[str, str | None] = {}   # write-through cache of durable values (this process only)
_warned_unreadable: set[str] = set()  # one warning per key, not per write


def _init() -> bool:
    """Resolve Turso config once and ensure the KV table exists. Returns True if durable."""
    global _url, _tok, _inited
    if _inited:
        return _url is not None
    _inited = True
    url, tok = os.environ.get("TURSO_DATABASE_URL"), os.environ.get("TURSO_AUTH_TOKEN")
    if url and tok:
        try:
            turso_http.execute(url, tok,
                f"CREATE TABLE IF NOT EXISTS {_TABLE} (k TEXT PRIMARY KEY, v TEXT, updated_at TEXT)")
            _url, _tok = url, tok
        except Exception:  # noqa: BLE001 — any failure → fall back to local files
            _url = _tok = None
    return _url is not None


def _fetch(name: str) -> tuple[bool, str | None]:
    """(ok, value): ok=False on transport error; value None means the key is genuinely absent.
    Successful results are cached; transport errors are NOT cached (so we retry next time and
    never mistake a blip for 'absent', which would let a store re-seed over durable data)."""
    if not _init():
        return (False, None)
    if name in _cache:
        return (True, _cache[name])
    try:
        rows = turso_http.execute(_url, _tok, f"SELECT v FROM {_TABLE} WHERE k = ?", (name,))
    except Exception:  # noqa: BLE001
        return (False, None)
    val = rows[0].get("v") if rows else None
    _cache[name] = val
    return (True, val)


def _put(name: str, text: str) -> bool:
    if not _init():
        return False
    try:
        turso_http.execute(_url, _tok,
            f"INSERT INTO {_TABLE} (k, v, updated_at) VALUES (?, ?, datetime('now')) "
            f"ON CONFLICT(k) DO UPDATE SET v = excluded.v, updated_at = excluded.updated_at",
            (name, text))
        _cache[name] = text
        return True
    except Exception:  # noqa: BLE001
        _cache[name] = text   # in-process view reflects what we wrote locally; durability degraded
        return False


class _DurablePath:
    """Path-like handle for a durable state file. Implements only the ops the stores use
    (exists / read_text / write_text) plus __fspath__. Turso is the durable truth; the local
    file is a cache. With Turso off, it's just the local file (unchanged behaviour)."""

    def __init__(self, name: str):
        self._name = name
        self._path = Path(state_path(name))

    def exists(self) -> bool:
        ok, val = _fetch(self._name)
        if ok and val is not None:
            return True
        return self._path.exists()

    def read_failed(self) -> bool:
        """True when a DURABLE read was attempted and failed — as opposed to genuinely absent.

        `read_confirmed()` has always drawn this distinction; the Path-like handle did not, so
        `concern_log._load()` could not tell "the mirror says empty" from "the mirror would not
        answer". With Turso's read quota exhausted that stopped being hypothetical: reads return
        BLOCKED while WRITES still succeed, so a caller that reads 0 rows and then appends would
        publish a one-row log over the mirror's real contents.
        """
        if not _init():
            return False                      # no mirror configured — the local file is truth
        ok, _ = _fetch(self._name)
        return not ok

    def read_json(self, default):
        """Parse this store as JSON, or `default` if it is absent or unparseable.

        ── WHY THIS IS A METHOD AND NOT A THIRTEENTH COPY ─────────────────────────────────────
        Thirteen stores wrote their own `_load()` around exactly this, three of them
        byte-for-byte identical (`audit/cpd.py`, `knowledge/blueprints.py`,
        `ledger/concern_log.py`). The module-level `read_json(name, default)` above already had
        the body, but its docstring scopes it to "stores that use open()/os.path directly", so
        every `durable_path`-based store re-implemented it instead of calling it.

        Taking the handle rather than the name matters: the stores hold a module-level `_STORE`,
        and `read_json(name, ...)` would construct a fresh `_DurablePath` per read. That is
        currently harmless — the instance carries no cache, only `_name` and `_path` — but it
        would silently become a per-call cost the moment this class caches anything, and the
        call site would give no hint that it had.

        ── `default` ALSO DECLARES THE EXPECTED TYPE ─────────────────────────────────────────
        Taken from `audit/calibration.py`, which was the only one of the thirteen to get this
        right, and had already written down why: "a store that is a list where a dict was
        expected makes every downstream `.get()` an AttributeError, and a store that parses to
        an int makes every `for` loop a TypeError. One guard here is worth an isinstance check
        at every use site — and it is the difference between a panel that reports 'no data' and
        a panel that takes the request down."

        The three byte-identical `_load()`s this replaced had NO such guard while annotating
        themselves `-> list[dict]`, so a store holding `{}` returned a dict to a caller about to
        iterate it. Collapsing them onto one body is what makes that fixable in one place.

        A `None` default cannot express a type, so callers wanting "dict or None" pass `{}` and
        map the empty case themselves — see `audit/rubric.py`.
        """
        return read_json_from(self, default)

    def read_text(self, *a, **k) -> str:
        ok, val = _fetch(self._name)
        if ok and val is not None:
            try:
                self._path.write_text(val)   # refresh the local cache after a durable restore
            except Exception:  # noqa: BLE001
                pass
            return val
        return self._path.read_text(*a, **k)

    def write_text(self, data, *a, **k) -> int:
        """Write the local cache, then publish to the mirror — UNLESS the mirror is unreadable.

        ── NEVER PUBLISH OVER A MIRROR YOU CANNOT READ ────────────────────────────────────────
        This published unconditionally, which is safe only while reads work. Turso's read quota is
        currently exhausted, and its failure mode is asymmetric: reads return BLOCKED while WRITES
        STILL SUCCEED. So the sequence was
            read  -> fails -> caller sees an empty or stale store
            write -> succeeds -> that empty/stale store becomes the durable truth
        and the mirror's real contents are gone, with no way to have checked first because reading
        is the thing that is broken.

        Degrading to local-only is strictly better: the local cache keeps working, the mirror keeps
        whatever it had, and the divergence is reported rather than resolved by guessing. The
        stored value is stale from that moment on — which is a known, recoverable state, unlike a
        clobbered one.
        """
        text = data if isinstance(data, str) else str(data)
        try:
            self._path.write_text(data, *a, **k)   # local cache always
        except Exception:  # noqa: BLE001
            pass
        if self.read_failed():
            global _warned_unreadable
            if self._name not in _warned_unreadable:
                _warned_unreadable.add(self._name)
                print(f"WARNING: durable mirror unreadable for {self._name!r} — writing LOCAL "
                      f"ONLY so the stored copy is not overwritten with unverified state. "
                      f"The mirror is now stale for this key.", file=_sys.stderr)
            return len(text)
        _put(self._name, text)                      # durable (best-effort)
        return len(text)

    def __fspath__(self) -> str:
        return str(self._path)

    def __str__(self) -> str:
        return str(self._path)


def durable_path(name: str) -> _DurablePath:
    return _DurablePath(name)


def read_json_from(path, default):
    """Parse any store-like as JSON, or `default` if absent, unreadable or the wrong type.

    Duck-typed on `exists()` / `read_text()` rather than typed to `_DurablePath`, and that is
    load-bearing: `check_calibration.py` swaps the stores for a double implementing exactly those
    two methods to assert "an unreadable store degrades instead of raising". Binding this to the
    concrete class broke that test — the double has no `read_json` — which is the useful signal
    that the duck-typing was a deliberate seam and not an accident.

    ── `default` ALSO DECLARES THE EXPECTED TYPE ─────────────────────────────────────────────
    From `audit/calibration.py`, the only one of thirteen stores to get this right, and it had
    already written down why: "a store that is a list where a dict was expected makes every
    downstream `.get()` an AttributeError, and a store that parses to an int makes every `for`
    loop a TypeError. One guard here is worth an isinstance check at every use site — and it is
    the difference between a panel that reports 'no data' and a panel that takes the request
    down."

    A `None` default cannot express a type, so callers wanting "dict or None" pass `{}` and map
    the empty case themselves — see `audit/rubric.py`.
    """
    try:
        if path.exists():
            parsed = json.loads(path.read_text())
            if isinstance(parsed, type(default)):
                return parsed
    except Exception:  # noqa: BLE001 — a store must never take the app down
        pass
    return default


def read_json(name: str, default):
    """Explicit durable read for stores that use open()/os.path directly (e.g. auth)."""
    dp = _DurablePath(name)
    if not dp.exists():
        return default
    try:
        return json.loads(dp.read_text())
    except Exception:  # noqa: BLE001
        return default


def write_json(name: str, obj) -> None:
    _DurablePath(name).write_text(json.dumps(obj, indent=1))


def read_confirmed(name: str):
    """(ok, parsed_or_None). ok=False means a durable read was ATTEMPTED but FAILED (transport
    error) — the caller must NOT overwrite the store, because it can't know the real state and a
    write would clobber durable truth with whatever the wiped local cache holds. ok=True means the
    state is known (value, or None if genuinely absent). With durable off, the local file is truth."""
    if not _init():
        p = Path(state_path(name))
        if p.exists():
            try:
                return (True, json.loads(p.read_text()))
            except Exception:  # noqa: BLE001
                return (True, None)
        return (True, None)
    ok, v = _fetch(name)
    if not ok:
        return (False, None)                 # transport error — state unknown; do not write
    if v is None:
        p = Path(state_path(name))            # durable confirms absent; honor an unmigrated local file
        if p.exists():
            try:
                return (True, json.loads(p.read_text()))
            except Exception:  # noqa: BLE001
                return (True, None)
        return (True, None)
    try:
        return (True, json.loads(v))
    except Exception:  # noqa: BLE001
        return (True, None)
