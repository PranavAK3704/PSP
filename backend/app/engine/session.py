"""Conversation session — just the running chat history.

The agent (LLM) drives the dialogue, so the session no longer holds a hand-rolled
state machine (slots/stance/gathering). It holds the Gemini `contents` array so the
model sees the full multi-turn context and continues naturally. Redis in prod.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

_lock = threading.Lock()


@dataclass
class Session:
    conversation_id: str
    captain_id: str
    contents: list = field(default_factory=list)   # running Gemini conversation array
    turns: int = 0


class SessionStore:
    def __init__(self):
        self._s: dict[str, Session] = {}

    def get_or_create(self, conversation_id: str, captain_id: str) -> Session:
        with _lock:
            s = self._s.get(conversation_id)
            if s is None:
                s = Session(conversation_id=conversation_id, captain_id=captain_id)
                self._s[conversation_id] = s
            return s

    def end(self, conversation_id: str) -> None:
        with _lock:
            self._s.pop(conversation_id, None)


STORE = SessionStore()
