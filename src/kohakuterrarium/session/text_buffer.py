"""Coalesce streamed assistant text and preserve partial segments for recovery.

The buffer mirrors in-flight text to ``<namespace>:open_text`` while leaving
event persistence to ``SessionOutput``.
"""

import time
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_SLOT_SUFFIX = "open_text"


def last_persisted_turn_branch(
    store: Any, prefix: str
) -> tuple[int | None, int | None, list[tuple[int, int]] | None]:
    """Return turn, branch, and parent path from the latest indexed event.

    Recovered text must retain the interrupted turn rather than inherit the
    resumed agent's already-advanced turn index.
    """
    try:
        events = store.get_events(prefix)
    except Exception:  # pragma: no cover - recovery must tolerate unavailable stores
        return None, None, None
    for evt in reversed(events):
        ti = evt.get("turn_index")
        if isinstance(ti, int) and ti > 0:
            bi = evt.get("branch_id")
            bi = bi if isinstance(bi, int) and bi > 0 else None
            ppath = evt.get("parent_branch_path")
            ppath = [tuple(p) for p in ppath] if isinstance(ppath, list) else None
            return ti, bi, ppath
    return None, None, None


# Gate durable writes by size and time to limit streaming-path overhead. A
# hard crash can lose only the text accumulated since the most recent gate.
_FLUSH_CHARS = 512
_FLUSH_SECONDS = 0.5


class OpenTextSegment:
    """Buffer one streamed-text segment with gated durable recovery state.

    ``append`` mirrors accumulated text after the size or time threshold.
    ``take`` closes a live segment, while ``recover`` restores a segment left
    by an interrupted process. Both reset memory and durable state.
    """

    def __init__(self, store: Any, prefix: str) -> None:
        self._store = store
        self._key = f"{prefix}:{_SLOT_SUFFIX}"
        self._buf = ""
        self._persisted_len = 0
        self._last_persist = time.monotonic()

    def append(self, chunk: str) -> None:
        """Extend the segment, persisting to the slot on the flush gate."""
        self._buf += chunk
        now = time.monotonic()
        if (
            len(self._buf) - self._persisted_len >= _FLUSH_CHARS
            or now - self._last_persist >= _FLUSH_SECONDS
        ):
            self._persist(self._buf)
            self._persisted_len = len(self._buf)
            self._last_persist = now

    def take(self) -> str:
        """Return the live segment and clear its memory and durable state.

        Short segments never cross the persistence gate, so closing them does
        not perform an unnecessary state write.
        """
        text = self._buf
        had_slot = self._persisted_len > 0
        self._reset()
        if had_slot:
            self._persist("")
        return text

    def recover(self) -> str:
        """Return interrupted text from durable state and clear the segment."""
        text = self._load()
        self._reset()
        if text:
            self._persist("")
        return text

    def _reset(self) -> None:
        self._buf = ""
        self._persisted_len = 0
        self._last_persist = time.monotonic()

    def _load(self) -> str:
        try:
            value = self._store.state.get(self._key)
        except (KeyError, TypeError):
            return ""
        return value if isinstance(value, str) else ""

    def _persist(self, text: str) -> None:
        try:
            self._store.state[self._key] = text
        except Exception as e:  # pragma: no cover - buffering cannot fail the turn
            logger.warning("open_text slot write failed", error=str(e), exc_info=True)
