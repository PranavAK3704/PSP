"""Conversation session — the running chat history, bounded.

The agent (LLM) drives the dialogue, so the session no longer holds a hand-rolled state
machine (slots/stance/gathering). It holds the Gemini `contents` array so the model sees the
full multi-turn context and continues naturally. Redis in prod.

── WHY BOUNDING IS NOT OPTIONAL ─────────────────────────────────────────────────────────
`contents` is resent IN FULL on every step of every turn. Two consequences, both real:

  1. Cost grows quadratically in conversation length. A tool result appended on turn 1 is
     paid for again on every step of turns 2..N. This is why the search_sops projection in
     tools.py matters more than its size suggests, and why history needs a ceiling.
  2. Sessions were never evicted. `SessionStore._s` only ever grew — one entry per
     conversation id, for the lifetime of the process, each holding the full history. That
     is a memory leak with a per-entry cost proportional to how much was said.

── THE CONSTRAINT THAT MAKES TRIMMING DANGEROUS ─────────────────────────────────────────
`claude_provider._to_anthropic_messages` mints tool-use ids POSITIONALLY at render time and
pairs tool_results to them BY INDEX. So a (model-turn-with-N-calls, user-turn-with-N-results)
sequence is an ATOMIC PAIR. Drop the model turn and keep the results — or vice versa — and
the rendered request has a tool_result referencing an id that was never issued. Anthropic
rejects that with a hard 400, which surfaces through conversation.py's generic handler as
"transport" — a completely misleading symptom for a history bug.

So `trim()` never cuts inside a pair. It walks backwards, finds a safe boundary (a plain
user turn that is NOT a tool-response turn), and cuts there or not at all.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

_lock = threading.Lock()

# Keep the last N conversation turns. 8 is chosen to be generous relative to real support
# conversations (a resolved loss dispute is 2–4 turns) while still bounding the tail.
MAX_HISTORY_TURNS = 8
# Evict a session after this long with no activity. A support conversation that has been
# silent for 2 hours is over; holding its history costs memory and buys nothing.
IDLE_EVICT_SECONDS = 2 * 60 * 60


def _is_tool_response_turn(entry: dict) -> bool:
    """A user turn that carries functionResponses — the second half of an atomic pair."""
    return any("functionResponse" in p for p in (entry.get("parts") or []))


def _is_plain_user_turn(entry: dict) -> bool:
    return entry.get("role") != "model" and not _is_tool_response_turn(entry)


@dataclass
class Session:
    conversation_id: str
    captain_id: str
    contents: list = field(default_factory=list)   # running Gemini conversation array
    turns: int = 0
    last_seen: float = field(default_factory=time.time)
    # Identifier tokens the CAPTAIN has supplied, accumulated across turns. This is the
    # allow-list for the data-plane subset test (see engine/dataplane.py): a tool result may
    # echo an identifier the captain typed — it is already in the model's context — but not
    # one it would otherwise never have seen. It accumulates because an AWB given on turn 1
    # is still theirs on turn 4, and the tool result that quotes it arrives later.
    supplied: set = field(default_factory=set)

    def trim(self, max_turns: int = MAX_HISTORY_TURNS) -> int:
        """Drop the oldest history beyond `max_turns` captain turns. Returns entries removed.

        Cuts ONLY at a plain user turn, never inside a (model-calls, tool-results) pair —
        see the module note. If no safe boundary exists at or before the target, nothing is
        removed: an over-long history costs tokens, whereas a broken pair costs the turn.
        """
        # Index every plain user turn — these are the only legal cut points.
        starts = [i for i, e in enumerate(self.contents) if _is_plain_user_turn(e)]
        if len(starts) <= max_turns:
            return 0
        cut = starts[len(starts) - max_turns]
        if cut <= 0:
            return 0
        del self.contents[:cut]
        return cut


class SessionStore:
    def __init__(self):
        self._s: dict[str, Session] = {}

    def get_or_create(self, conversation_id: str, captain_id: str) -> Session:
        with _lock:
            self._evict_idle_locked()
            s = self._s.get(conversation_id)
            if s is None:
                s = Session(conversation_id=conversation_id, captain_id=captain_id)
                self._s[conversation_id] = s
            s.last_seen = time.time()
            return s

    def _evict_idle_locked(self) -> int:
        """Drop sessions untouched for IDLE_EVICT_SECONDS. Called on every get_or_create,
        which is the only place that grows the map — so the sweep runs exactly as often as
        it needs to and needs no background task."""
        cutoff = time.time() - IDLE_EVICT_SECONDS
        stale = [k for k, v in self._s.items() if v.last_seen < cutoff]
        for k in stale:
            self._s.pop(k, None)
        return len(stale)

    def stats(self) -> dict:
        with _lock:
            return {"sessions": len(self._s),
                    "history_entries": sum(len(s.contents) for s in self._s.values()),
                    "max_history_turns": MAX_HISTORY_TURNS,
                    "idle_evict_seconds": IDLE_EVICT_SECONDS}

    def end(self, conversation_id: str) -> None:
        with _lock:
            self._s.pop(conversation_id, None)


STORE = SessionStore()
