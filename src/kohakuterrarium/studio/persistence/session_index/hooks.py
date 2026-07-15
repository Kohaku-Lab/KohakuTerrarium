"""Push-side integration for the session index sidecar.

Reconciliation provides the pull path for disk changes. These hooks provide a
push path by translating store events into debounced, idempotent sidecar
upserts. Studio lifecycle wiring owns hook attachment and final flushing.
"""

from collections.abc import Callable
from pathlib import Path
import time

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.session_index.entry import SessionIndexEntry
from kohakuterrarium.studio.persistence.session_index.reconcile import (
    _first_user_input_preview,
    _has_vector_index,
)
from kohakuterrarium.studio.persistence.session_index.store import SessionIndex
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def push_index_update(
    store: SessionStore, index: SessionIndex
) -> SessionIndexEntry | None:
    """Upsert the store's current metadata and return the indexed entry.

    Read or write failures are logged and converted to ``None`` so indexing
    cannot interrupt session processing.
    """
    try:
        path = Path(store._path)
        meta = store.load_meta()
        preview = _first_user_input_preview(store)
        has_vec = _has_vector_index(store)
        entry = SessionIndexEntry.from_meta(
            path=path, meta=meta, preview=preview, has_vector_index=has_vec
        )
        index.upsert(entry)
        return entry
    except Exception as exc:  # noqa: BLE001
        logger.warning("push_index_update failed", error=str(exc), exc_info=True)
        return None


class SessionIndexHook:
    """Keep one live store's index entry current with debounced pushes.

    Construction subscribes and optionally performs an initial push. The event
    count or elapsed-time threshold, whichever occurs first, triggers updates.
    Callers flush before closing the store and detach afterward to avoid a
    dangling subscriber.
    """

    DEFAULT_FLUSH_EVERY_N_EVENTS = 20
    DEFAULT_FLUSH_EVERY_SECONDS = 5.0

    def __init__(
        self,
        store: SessionStore,
        index: SessionIndex,
        *,
        flush_every_n_events: int | None = None,
        flush_every_seconds: float | None = None,
        push_on_attach: bool = True,
    ) -> None:
        self._store = store
        self._index = index
        self._n = int(
            flush_every_n_events
            if flush_every_n_events is not None
            else self.DEFAULT_FLUSH_EVERY_N_EVENTS
        )
        self._s = float(
            flush_every_seconds
            if flush_every_seconds is not None
            else self.DEFAULT_FLUSH_EVERY_SECONDS
        )
        self._unflushed_events = 0
        self._last_push = time.monotonic()
        self._attached = False
        self._listener: Callable[[str, dict], None] | None = None
        self._attach(push_on_attach=push_on_attach)

    def _attach(self, *, push_on_attach: bool) -> None:
        if self._attached:
            return

        # Preserve the exact callback identity required by ``unsubscribe``.
        def _on_event(key: str, data: dict) -> None:  # noqa: ARG001 — protocol args
            self._on_event()

        self._listener = _on_event
        self._store.subscribe(_on_event)
        self._attached = True
        if push_on_attach:
            self.flush()

    def _on_event(self) -> None:
        self._unflushed_events += 1
        now = time.monotonic()
        if self._unflushed_events >= self._n or (now - self._last_push) >= self._s:
            self.flush()

    def flush(self) -> None:
        """Force a push regardless of the debounce state."""
        self._unflushed_events = 0
        self._last_push = time.monotonic()
        push_index_update(self._store, self._index)

    def detach(self) -> None:
        """Idempotently stop listening to store events."""
        if not self._attached or self._listener is None:
            return
        try:
            self._store.unsubscribe(self._listener)
        except Exception as exc:  # noqa: BLE001
            logger.warning("detach unsubscribe failed", error=str(exc), exc_info=True)
        self._attached = False
        self._listener = None

    def __enter__(self) -> "SessionIndexHook":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.flush()
        self.detach()
