"""Isolate I/O-heavy API work from the event loop's default executor.

Session indexing and catalog scans can fan out across many SQLite and filesystem
operations. A dedicated pool prevents those requests from exhausting the workers
needed by framework operations such as WebSocket setup and identity reads.
"""

import asyncio
import concurrent.futures
from functools import partial
from typing import Any, Callable, TypeVar

_R = TypeVar("_R")

# I/O wait dominates this pool, and SQLite releases the GIL while reading.
# The larger limit accommodates per-file fan-out without consuming default workers.
_MAX_WORKERS = 64

_executor: concurrent.futures.ThreadPoolExecutor | None = None


def get_io_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Return the process-wide I/O pool, creating it only when first needed."""
    global _executor
    if _executor is None:
        _executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=_MAX_WORKERS,
            thread_name_prefix="kt-io",
        )
    return _executor


async def run_in_io_executor(fn: Callable[..., _R], /, *args: Any, **kwargs: Any) -> _R:
    """Run a synchronous callable on the dedicated I/O pool.

    Keyword arguments are bound with :func:`functools.partial` because
    ``run_in_executor`` accepts only positional arguments for the callable.
    """
    loop = asyncio.get_running_loop()
    executor = get_io_executor()
    if kwargs:

        return await loop.run_in_executor(executor, partial(fn, *args, **kwargs))
    return await loop.run_in_executor(executor, fn, *args)


__all__ = ["get_io_executor", "run_in_io_executor"]
