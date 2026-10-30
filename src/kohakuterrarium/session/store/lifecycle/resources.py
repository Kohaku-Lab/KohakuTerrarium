"""SessionStore occupancy enrollment around native session-file handles."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kohakuterrarium.session.occupancy.registry import (
    register_open_store,
    unregister_open_store,
)
from kohakuterrarium.session.occupancy.gate import ResourceLease, acquire_legacy
from kohakuterrarium.session.store.lifecycle.lock import (
    TABLE_ATTRS,
    acquire_writer_lock,
    close_tables,
    discard_partial_open,
    release_writer_lock,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class StoreResourceMixin:
    """Enroll legacy occupancy before native open and finish it after disposal."""

    _path: str
    _writer_lock: Any
    _resource_lease: ResourceLease | None
    _companion_closers: list[Callable[[], None]]
    fts: Any

    def _enroll_resources(self, *, writer_lock: bool) -> None:
        """Register, enroll, then take the optional writer lock."""
        self._resource_lease = None
        self._writer_lock = None
        register_open_store(self._path, self)
        try:
            self._resource_lease = acquire_legacy(self._path, "open")
            if writer_lock:
                self._writer_lock = acquire_writer_lock(self._path)
        except BaseException:
            unregister_open_store(self._path, self)
            self._finish_resource_lease(True)
            raise

    def _discard_failed_open(self) -> None:
        """Drop partial native handles and finish occupancy with proven release."""
        released = False
        # A read-only store never opens an FTS handle.
        names = TABLE_ATTRS if self._readonly else (*TABLE_ATTRS, "fts")
        try:
            released = discard_partial_open(
                [(name, getattr(self, name, None)) for name in names], self._path
            )
        finally:
            try:
                release_writer_lock(self._writer_lock)
            finally:
                self._writer_lock = None
                unregister_open_store(self._path, self)
                self._finish_resource_lease(released)

    def _finish_resource_lease(self, source_released: bool) -> None:
        lease = self._resource_lease
        if lease is None:
            return
        self._resource_lease = None
        lease.finish(source_released=bool(source_released))

    def _close_enrolled_resources(self) -> None:
        """Close tables, companions, and the writer lock, then finish occupancy."""
        unregister_open_store(self._path, self)
        released = False
        try:
            tables = tuple(getattr(self, name) for name in TABLE_ATTRS)
            released = close_tables(
                tables, self.fts, self._writer_lock, self._companion_closers
            )
        finally:
            self._writer_lock = None
            self._finish_resource_lease(released)
            logger.debug("SessionStore closed", path=self._path)
