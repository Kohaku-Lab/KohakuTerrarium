"""Durable streaming-text segment buffer for :class:`SessionOutput` (UXI-02).

Streamed assistant text is coalesced into ONE ``text_chunk`` event per
segment instead of one event per transport write. The in-flight segment
is mirrored to a durable session-state slot (``<namespace>:open_text``)
so a crash/shutdown mid-stream recovers the partial text on resume
rather than dropping it. The event write itself stays in
``SessionOutput`` — this class owns only the buffer + its durable slot.
"""

import time
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_SLOT_SUFFIX = "open_text"


def last_persisted_turn_branch(
    store: Any, prefix: str
) -> tuple[int | None, int | None, list[tuple[int, int]] | None]:
    """Turn/branch/path of the most recent persisted event with a turn.

    A crashed segment belongs to the turn that was in flight when the
    process died, so recovered text is stamped from that last persisted
    event — NOT the resumed agent's (already advanced) current turn.
    """
    try:
        events = store.get_events(prefix)
    except Exception:  # pragma: no cover — defensive
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


# The slot exists only for crash recovery, so mid-stream writes are gated
# to bound work on the hot path: persist once the segment grows past this
# many chars, or this many seconds since the last write. A boundary flush
# always persists (clears). Worst case a hard crash loses the sub-gate tail.
_FLUSH_CHARS = 512
_FLUSH_SECONDS = 0.5


class OpenTextSegment:
    """The current run of streamed text, backed by a durable state slot.

    Appends accumulate in memory for cheap concatenation and are mirrored
    to ``state[<prefix>:open_text]`` on a size/time gate (plus on every
    boundary), so the slot trails the live segment by at most the gate
    window. :meth:`take` returns the in-memory segment for a normal
    boundary flush; :meth:`recover` reads the durable slot (used on
    resume, when the in-memory buffer is empty but a prior process left
    text behind). Both clear the slot.
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
        """Return the in-memory segment and clear memory + slot.

        Only clears the durable slot when it actually holds gated text —
        so a boundary that closes a short (never-persisted) segment does
        not write to the slot at all.
        """
        text = self._buf
        had_slot = self._persisted_len > 0
        self._reset()
        if had_slot:
            self._persist("")
        return text

    def recover(self) -> str:
        """Return the durable slot's text and clear memory + slot.

        For resume: a fresh instance has an empty in-memory buffer but a
        non-empty slot means a previous process died mid-stream.
        """
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
        except Exception as e:  # pragma: no cover — defensive
            logger.warning("open_text slot write failed", error=str(e), exc_info=True)
