"""Debounced index snapshots with sidecar writes outside the store worker."""

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path

from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.session_index.entry import SessionIndexEntry
from kohakuterrarium.studio.persistence.session_index.reconcile import (
    _first_user_input_preview,
    _has_vector_index,
)
from kohakuterrarium.studio.persistence.session_index.store import SessionIndex
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _snapshot_entry(store: SessionStore) -> SessionIndexEntry:
    """Capture store-owned data before passing it to the index writer."""
    meta = store.load_meta()
    return SessionIndexEntry.from_meta(
        path=Path(store._path),
        meta=meta,
        preview=_first_user_input_preview(store, meta),
        has_vector_index=_has_vector_index(store),
    )


def push_index_update(
    store: SessionStore, index: SessionIndex
) -> SessionIndexEntry | None:
    """Synchronously snapshot and upsert an externally owned store."""
    try:
        entry = _snapshot_entry(store)
        index.upsert(entry)
        return entry
    except Exception as exc:  # noqa: BLE001
        logger.warning("push_index_update failed", error=str(exc), exc_info=True)
        return None


class SessionIndexHook:
    """Coalesce event-driven pushes without holding up event persistence.

    A snapshot runs on the store's affinity thread, then the index's shared
    writer persists it. At most one refresh is outstanding per hook. Periodic
    refreshes are asynchronous; ``flush`` forces a current snapshot and waits,
    while ``detach`` stops accepting events and drains outstanding work.
    These lifecycle barriers must be called outside the store worker.
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
        # Future callbacks may run inline while a refresh is being scheduled.
        self._lock = threading.RLock()
        self._pending: Future | None = None
        # Detach before native-table disposal; snapshots use the store worker.
        store._ensure_affinity()
        store._companion_closers.append(self.detach)
        self._attach(push_on_attach=push_on_attach)

    def _attach(self, *, push_on_attach: bool) -> None:
        if self._attached:
            return

        def _on_event(key: str, data: dict) -> None:
            self._on_event()

        self._listener = _on_event
        self._store.subscribe(_on_event)
        self._attached = True
        if push_on_attach:
            self.flush()

    def _schedule_locked(self) -> Future:
        """Queue one immutable snapshot; the caller owns ``_lock``."""
        snapshot = self._store.submit(_snapshot_entry, self._store)
        self._unflushed_events = 0
        pending = self._pending = Future()
        snapshot.add_done_callback(lambda result: self._push(result, pending))
        return pending

    def _push(self, snapshot: Future, pending: Future) -> None:
        # Called only when the snapshot is ready. Waiting for a store inside
        # the shared writer would block unrelated sessions behind that store.
        try:
            written = self._index.submit_update(snapshot.result())
        except Exception as exc:  # noqa: BLE001
            self._complete_refresh(pending, exc)
            return

        def completed(result: Future) -> None:
            try:
                result.result()
            except Exception as exc:  # noqa: BLE001
                self._complete_refresh(pending, exc)
            else:
                self._complete_refresh(pending)

        written.add_done_callback(completed)

    def _complete_refresh(
        self, pending: Future, error: Exception | None = None
    ) -> None:
        if error is not None:
            logger.warning(
                "index refresh failed",
                error=str(error),
                exc_info=(type(error), error, error.__traceback__),
            )
        # A slow refresh must not make the next event immediately overdue.
        with self._lock:
            self._last_push = time.monotonic()
        pending.set_result(None)

    def _on_event(self) -> None:
        with self._lock:
            if not self._attached:
                return
            self._unflushed_events += 1
            if self._pending is not None and not self._pending.done():
                return
            if (
                self._unflushed_events >= self._n
                or time.monotonic() - self._last_push >= self._s
            ):
                self._schedule_locked()

    def flush(self) -> None:
        """Wait for a snapshot requested after this barrier was entered."""
        with self._lock:
            pending = self._pending
        if pending is not None:
            pending.result()
        with self._lock:
            if not self._attached:
                return
            pending = self._schedule_locked()
        pending.result()

    def detach(self) -> None:
        """Stop listening and drain scheduled work before the store closes."""
        with self._lock:
            if not self._attached or self._listener is None:
                return
            listener = self._listener
            self._attached = False
            self._listener = None
            pending = self._pending
        try:
            self._store.unsubscribe(listener)
        except Exception as exc:  # noqa: BLE001
            logger.warning("detach unsubscribe failed", error=str(exc), exc_info=True)
        if pending is not None:
            pending.result()

    def __enter__(self) -> "SessionIndexHook":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.flush()
        finally:
            self.detach()
