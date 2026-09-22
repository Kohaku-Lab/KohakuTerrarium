"""Per-store single-thread executor for off-loop SessionStore work."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")


class StoreAffinityMixin:
    """Dispatch synchronous store work onto one dedicated worker thread."""

    def _init_affinity(self) -> None:
        self._affinity_lock = threading.RLock()
        self._affinity: ThreadPoolExecutor | None = None
        self._affinity_worker_ident: int | None = None
        self._closed = False

    def _mark_affinity_worker(self) -> None:
        self._affinity_worker_ident = threading.get_ident()

    def _ensure_affinity(self) -> ThreadPoolExecutor:
        with self._affinity_lock:
            if self._closed:
                raise RuntimeError("SessionStore is closed")
            if self._affinity is None:
                self._affinity = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="kt-store",
                    initializer=self._mark_affinity_worker,
                )
                self._companion_closers.append(self._shutdown_affinity)
            return self._affinity

    def _begin_close(self) -> bool:
        """Stop accepting work and drain accepted calls before table disposal."""
        with self._affinity_lock:
            if self._closed:
                return False
            if threading.get_ident() == self._affinity_worker_ident:
                raise RuntimeError("Cannot close SessionStore from its affinity worker")
            self._closed = True
        self._shutdown_affinity()
        return True

    def _shutdown_affinity(self) -> None:
        with self._affinity_lock:
            executor, self._affinity = self._affinity, None
        if executor is not None:
            executor.shutdown(wait=True)

    async def run(self, fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
        """Run ``fn`` on this store's affinity thread and return its result.

        Cancelling the caller stops waiting, but accepted work retains its FIFO
        position and still runs. Store close drains it before table disposal.
        """
        return await asyncio.shield(
            asyncio.wrap_future(self.submit(fn, *args, **kwargs))
        )

    def submit(self, fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> Future[_T]:
        """Queue one call in FIFO order, rejecting requests once close begins."""
        with self._affinity_lock:
            executor = self._ensure_affinity()
            return executor.submit(fn, *args, **kwargs)
