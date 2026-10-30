"""Track native handles and file occupancy for a session memory index."""

import threading
from typing import Any

from kohakuterrarium.session.occupancy.gate import acquire_legacy
from kohakuterrarium.session.vault.handles import dispose
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class MemoryResources:
    """Retain occupancy until all tracked index handles are disposed."""

    def __init__(self, path: str) -> None:
        self._lock = threading.RLock()
        self._lease = acquire_legacy(path, "memory_open")
        self._handles: list[Any] = []
        self._released = False
        self._certain = True

    def open(self, factory, *args, **kwargs):
        """Open and retain one native index table."""
        with self._lock:
            if self._released:
                raise RuntimeError("SessionMemory is closed")
            try:
                handle = factory(*args, **kwargs)
                self._handles.append(handle)
            except BaseException:
                self._certain = False
                raise
            return handle

    def drop(self, handle: Any) -> None:
        """Dispose a temporary index handle before the enclosing memory closes."""
        with self._lock:
            for index, table in enumerate(self._handles):
                if table is handle:
                    try:
                        if not dispose(table):
                            self._certain = False
                    except BaseException:
                        self._certain = False
                        raise
                    self._handles.pop(index)
                    return

    def close(self) -> None:
        """Dispose every handle, retaining failed ones for a retry."""
        with self._lock:
            if self._released and not self._handles:
                return
            try:
                index = 0
                while index < len(self._handles):
                    try:
                        released = dispose(self._handles[index])
                    except Exception:
                        self._certain = False
                        logger.warning(
                            "Memory native handle drop failed", exc_info=True
                        )
                        index += 1
                        continue
                    if not released:
                        self._certain = False
                    self._handles.pop(index)
            except BaseException:
                self._certain = False
                raise
            finally:
                self._released = True
                self._lease.finish(source_released=self._certain)
