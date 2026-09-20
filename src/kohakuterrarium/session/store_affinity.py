"""Per-store single-thread executor for off-loop SessionStore work."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable, TypeVar

_T = TypeVar("_T")


class StoreAffinityMixin:
    """Dispatch synchronous store work onto one dedicated worker thread."""

    _affinity_init_lock = threading.Lock()

    def _ensure_affinity(self) -> ThreadPoolExecutor:
        executor = getattr(self, "_affinity", None)
        if executor is not None:
            return executor
        # The class-level lock makes executor creation atomic even if two
        # threads race the first dispatch; an instance-level lock cannot,
        # because creating it is itself racy.
        with StoreAffinityMixin._affinity_init_lock:
            executor = getattr(self, "_affinity", None)
            if executor is None:
                self._affinity = ThreadPoolExecutor(
                    max_workers=1, thread_name_prefix="kt-store"
                )
                closers = getattr(self, "_companion_closers", None)
                if closers is not None:
                    closers.append(self._shutdown_affinity)
                executor = self._affinity
        return executor

    def _shutdown_affinity(self) -> None:
        executor = getattr(self, "_affinity", None)
        self._affinity = None
        if executor is not None:
            executor.shutdown(wait=True)

    async def run(self, fn: Callable[..., _T], /, *args: Any, **kwargs: Any) -> _T:
        """Run ``fn`` on this store's affinity thread and return its result."""
        if getattr(self, "_closed", False):
            raise RuntimeError("SessionStore is closed")
        loop = asyncio.get_running_loop()
        executor = self._ensure_affinity()
        if kwargs:
            return await loop.run_in_executor(executor, partial(fn, *args, **kwargs))
        if args:
            return await loop.run_in_executor(executor, fn, *args)
        return await loop.run_in_executor(executor, fn)
