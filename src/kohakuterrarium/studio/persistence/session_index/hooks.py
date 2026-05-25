"""Push-side integration for the session index sidecar.

The reconciler (``reconcile.py``) is the always-available pull path —
it'll catch every disk change on the next ``?refresh=true`` or
periodic scan.  This module adds the push path so the sidecar
stays current without polling: a SessionStore's lifecycle events
trigger debounced upserts into the index.

Design:

* :func:`push_index_update` — synchronous helper.  Read the store's
  current meta + state, build a fresh :class:`SessionIndexEntry`,
  upsert.  Safe to call repeatedly — :meth:`SessionIndex.upsert`
  is idempotent.

* :class:`SessionIndexHook` — wraps a single SessionStore + index
  pair and debounces ``append_event`` notifications so we don't
  thrash the sidecar on a busy chat (one push per N events OR per
  M seconds, whichever fires first; mirrors the cache-flush gates
  in :class:`SessionStore` itself).

The studio lifecycle layer is the canonical place to attach the
hook (see :mod:`kohakuterrarium.studio.sessions.lifecycle` wiring).
Tests construct the hook directly to assert push semantics.
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
    """Push the store's current state into the index as one upsert.

    Returns the entry that was upserted (handy for tests + the
    lifecycle layer's audit log); returns ``None`` and logs at
    debug level when the store can't be read (corrupted meta,
    closed mid-call, etc.) — never raises into the caller.
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
        logger.debug("push_index_update failed", error=str(exc))
        return None


class SessionIndexHook:
    """Glue one ``SessionStore`` to one ``SessionIndex`` for live updates.

    On construction, subscribes to the store's event stream.  Pushes
    a fresh entry into the index either every ``flush_every_n_events``
    events or every ``flush_every_seconds`` seconds — whichever fires
    first.

    Lifecycle:

    * ``hook = SessionIndexHook(store, index)`` — registers + does
      one initial push so the index reflects ``init_meta`` even
      before the first event arrives.
    * ``hook.flush()`` — caller invokes before ``store.close()`` to
      capture the final state.
    * ``hook.detach()`` — caller invokes after ``store.close()`` so
      the subscriber doesn't dangle.

    The debounce gates mirror :class:`SessionStore`'s own
    ``DEFAULT_FLUSH_EVERY_N_EVENTS`` / ``DEFAULT_FLUSH_EVERY_N_SECONDS``
    — same intent (avoid per-event cost on a busy chat) at a slightly
    looser cadence because the index is cheaper than a SQLite flush.
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

        # Build a thin closure so we keep ``unsubscribe`` correctness —
        # ``store.subscribe`` keys subscribers by identity.
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
        """Stop listening to the store.  Idempotent."""
        if not self._attached or self._listener is None:
            return
        try:
            self._store.unsubscribe(self._listener)
        except Exception as exc:  # noqa: BLE001
            logger.debug("detach unsubscribe failed", error=str(exc))
        self._attached = False
        self._listener = None

    # Convenience: context-manager form for tests + tight scopes.

    def __enter__(self) -> "SessionIndexHook":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.flush()
        self.detach()
