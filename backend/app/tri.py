"""Tri — a three-valued answer, for checks whose input can be absent.

WHY THIS EXISTS
Two places in this codebase silently converted "I don't know" into "no":

  · `policy_exec.py` — `bool(scans and scans.get("connected_within_tat"))`. If there is no
    scan data at all, this is `False`, which reads as *"no forward connection was made"* —
    evidence AGAINST the captain — when the truth is *"we cannot see the scans"*, which is a
    reason for a human to look. On a money decision those two are opposite conclusions.
  · The captain panel's `metrics.utils.parseNumeric` returns `0` for an unparseable display
    string, and for a lower-is-better metric `0 <= target` reads as **good**. The panel does
    that to avoid crashing a UI, which is the right call for a UI. It is the wrong call for a
    support engine, where an unreadable value would silently become a passing check.

So a check over possibly-absent data returns YES / NO / UNKNOWN, and UNKNOWN is required to
behave as neither: it never satisfies a condition, and it never counts as a refutation. It
escalates.

The discipline in one line: `if tri is YES` to act, `if tri is NO` to argue, and let anything
else fall through to a human.
"""
from __future__ import annotations

from enum import Enum


class Tri(str, Enum):
    """A `str` subclass so it serialises into a trace event / JSON payload as its own name
    without a custom encoder — `json.dumps({"x": Tri.UNKNOWN})` gives `{"x": "UNKNOWN"}`."""

    YES = "YES"
    NO = "NO"
    UNKNOWN = "UNKNOWN"

    def __bool__(self):
        """Refuse implicit truthiness — LOUDLY.

        The previous version of this class just had a comment saying it deliberately defined no
        `__bool__`, which was wrong twice over. `Tri(str, Enum)` inherits `str.__bool__`, and
        every member is a non-empty string, so `bool(Tri.NO)` was **True** — `if verdict:` both
        compiled and read as correct while treating a definite NO as a pass. That is a worse
        version of the exact collapse this type exists to prevent, and the harness assertion
        that supposedly guarded it could not fail (it tested `hasattr` on a metaclass).

        Raising makes the mistake impossible to write instead of merely discouraged. Callers
        must say which branch they mean: `is Tri.YES`, `is Tri.NO`, `is Tri.UNKNOWN`, or
        `.known`.
        """
        raise TypeError(
            f"{type(self).__name__} has no truth value — a three-valued verdict cannot be "
            f"collapsed to a bool. Use `is Tri.YES` / `is Tri.NO` / `is Tri.UNKNOWN`, or "
            f".known. (got {self.name})")

    @property
    def known(self) -> bool:
        return self is not Tri.UNKNOWN

    @classmethod
    def of(cls, value, *, unknown_if_none: bool = True) -> "Tri":
        """Lift a bool / None into a Tri. `None` → UNKNOWN by default, which is the whole point."""
        if value is None and unknown_if_none:
            return cls.UNKNOWN
        return cls.YES if value else cls.NO


def all_yes(*tris: Tri) -> Tri:
    """Conjunction that propagates ignorance.

    NO wins over UNKNOWN — one definite failure is enough to decide, and knowing a check
    failed is more informative than knowing another was unreadable. Otherwise any UNKNOWN
    makes the whole conjunction UNKNOWN. An empty conjunction is UNKNOWN, not YES: "no checks
    ran" must never read as "everything passed".
    """
    if not tris:
        return Tri.UNKNOWN
    if any(t is Tri.NO for t in tris):
        return Tri.NO
    if any(t is Tri.UNKNOWN for t in tris):
        return Tri.UNKNOWN
    return Tri.YES
