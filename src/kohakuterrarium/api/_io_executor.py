"""Dedicated executor for I/O-heavy route work.

``asyncio.to_thread`` runs on the loop's default executor, sized
``min(32, os.cpu_count() + 4)``.  Multiple framework-internal call
sites use ``to_thread`` for IO — chat WS handshake, runtime-graph
snapshot, identity reads — and several routes do I/O-heavy work that
competes for those slots:

- saved-session indexing opens every ``.kohakutr`` plus its WAL/SHM
  sidecars (dozens of SQLite opens per request);
- catalog scanning walks creature / terrarium config trees and parses
  YAML for every entry;
- on cold caches both can fan out to tens of file operations.

When concurrent requests collide on the default pool, any new
``to_thread`` call queues — the user sees the whole server "block"
while one slow route holds the workers.  A dedicated I/O executor
keeps these I/O fan-outs separate from the rest of the framework's
threading budget.  Route handlers use :func:`run_in_io_executor`
instead of ``asyncio.to_thread``.
"""

import asyncio
import concurrent.futures
from functools import partial
from typing import Any, Callable, TypeVar

_R = TypeVar("_R")

# Sized generously for an I/O-bound thread pool.  SQLite C-level I/O
# releases the GIL on read; the dominant cost is socket / disk wait.
# 64 threads gives the per-file fan-out enough room without competing
# with every other framework ``to_thread`` call.
_MAX_WORKERS = 64

_executor: concurrent.futures.ThreadPoolExecutor | None = None


def get_io_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Return the lazy-built I/O executor singleton.

    Lazy so test runs that never hit I/O-heavy routes don't pay for
    an idle thread pool.  Once built, the executor lives until the
    process exits (``ThreadPoolExecutor`` daemonizes its workers).
    """
    global _executor
    if _executor is None:
        _executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=_MAX_WORKERS,
            thread_name_prefix="kt-io",
        )
    return _executor


async def run_in_io_executor(fn: Callable[..., _R], /, *args: Any, **kwargs: Any) -> _R:
    """Run ``fn(*args, **kwargs)`` on the dedicated I/O executor.

    Drop-in for ``asyncio.to_thread`` — same signature, different
    pool.  Synchronous keyword-only args funnel through
    :func:`functools.partial` so the executor's ``submit`` (which
    doesn't accept kwargs) handles them transparently.
    """
    loop = asyncio.get_running_loop()
    executor = get_io_executor()
    if kwargs:

        return await loop.run_in_executor(executor, partial(fn, *args, **kwargs))
    return await loop.run_in_executor(executor, fn, *args)


__all__ = ["get_io_executor", "run_in_io_executor"]
