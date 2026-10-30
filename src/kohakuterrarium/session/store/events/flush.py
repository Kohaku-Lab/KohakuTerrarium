"""Commit a session's buffered event rows and FTS rows on shared gates."""

import threading
import time
from collections.abc import Callable
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def insert_fts_rows(fts: Any, rows: list[tuple[str, dict]]) -> None:
    """Insert ``(text, meta)`` rows; a failed batch is retried row by row."""
    if not rows:
        return
    try:
        fts.insert_many(rows)
        return
    except Exception as e:
        logger.warning("FTS batch insert failed", error=str(e), rows=len(rows))
    for text, meta in rows:
        try:
            fts.insert(text, meta)
        except Exception as e:
            logger.warning("FTS indexing failed", error=str(e), exc_info=True)


class EventFlushPipeline:
    """One gate for the events write-back cache and the queued FTS rows.

    A flush runs after ``every_n`` appends, when an append finds the last
    flush older than ``every_seconds``, when the idle timer fires, and on
    explicit ``flush``. Queued FTS rows commit before the events cache.
    """

    def __init__(
        self,
        events: Any,
        fts: Any,
        *,
        every_n: int,
        every_seconds: float,
        timer_factory: Callable[..., threading.Timer] = threading.Timer,
    ) -> None:
        self._events = events
        self._fts = fts
        self._every_n = every_n
        self._every_seconds = every_seconds
        self._timer_factory = timer_factory
        self._lock = threading.RLock()
        self._fts_rows: list[tuple[str, dict]] = []
        self._unflushed = 0
        self._last_flush = time.monotonic()
        self._timer: threading.Timer | None = None
        self._closed = False

    @property
    def lock(self) -> threading.RLock:
        """Held while an append assigns its id and buffers its rows."""
        return self._lock

    @property
    def unflushed(self) -> int:
        return self._unflushed

    def queue_fts(self, text: str, meta: dict) -> None:
        """Queue one FTS row for the next commit; the idle timer covers it."""
        with self._lock:
            self._fts_rows.append((text, meta))
            self._arm_timer()

    def appended(self) -> None:
        """Count one buffered event and run the gate."""
        with self._lock:
            self._unflushed += 1
            n_gate = self._every_n > 0 and self._unflushed >= self._every_n
            t_gate = (
                self._every_seconds > 0
                and time.monotonic() - self._last_flush >= self._every_seconds
            )
            if n_gate or t_gate:
                try:
                    self._commit()
                except Exception as e:
                    logger.warning("Events flush failed", error=str(e), exc_info=True)
                    self._arm_timer()
            else:
                self._arm_timer()

    def flush(self) -> None:
        """Commit everything buffered now; a failure propagates."""
        with self._lock:
            self._cancel_timer()
            self._commit()

    def close(self) -> None:
        """Stop the idle timer and commit what is buffered."""
        with self._lock:
            self._closed = True
            self._cancel_timer()
            self._commit()

    def _commit(self) -> None:
        rows, self._fts_rows = self._fts_rows, []
        if rows and self._fts is not None:
            insert_fts_rows(self._fts, rows)
        self._events.flush_cache()
        self._unflushed = 0
        self._last_flush = time.monotonic()

    def _arm_timer(self) -> None:
        if self._closed or self._timer is not None or self._every_seconds <= 0:
            return
        timer = self._timer_factory(self._every_seconds, self._fire)
        timer.daemon = True
        self._timer = timer
        timer.start()

    def _cancel_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()

    def _fire(self) -> None:
        with self._lock:
            self._timer = None
            if self._closed or (not self._unflushed and not self._fts_rows):
                return
            try:
                self._commit()
            except Exception as e:
                logger.warning("Idle events flush failed", error=str(e), exc_info=True)
                self._arm_timer()
