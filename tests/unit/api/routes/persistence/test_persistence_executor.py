"""Unit tests for the dedicated persistence executor.

Pins three properties:

1. The executor is a separate :class:`ThreadPoolExecutor` instance —
   NOT the loop's default pool that ``asyncio.to_thread`` uses.
2. Concurrent persistence work doesn't starve other ``asyncio.to_thread``
   callers (and vice-versa): a saturated persistence pool must not
   block ``to_thread``.
3. Sized for I/O-fan-out: at least 32 worker threads available.

Together these are the property the user demanded: "session list still
blocks whole server" can never recur as long as the persistence layer
uses its own pool.
"""

import asyncio
import concurrent.futures
import threading
import time

import pytest


@pytest.fixture(autouse=True)
def _reset_executor():
    """Ensure each test starts from a clean executor singleton.

    The persistence executor is now a thin alias over
    :mod:`kohakuterrarium.api._io_executor`, so the singleton state
    lives there.  Snapshot + restore on that module.
    """
    from kohakuterrarium.api import _io_executor as ex

    snap = ex._executor
    ex._executor = None
    yield
    if ex._executor is not None and ex._executor is not snap:
        ex._executor.shutdown(wait=True)
    ex._executor = snap


class TestPersistenceExecutor:
    def test_singleton_creates_real_threadpool(self):
        from kohakuterrarium.api.routes.persistence._executor import (
            get_persistence_executor,
        )

        ex = get_persistence_executor()
        assert isinstance(ex, concurrent.futures.ThreadPoolExecutor)
        # Same instance on subsequent calls.
        assert get_persistence_executor() is ex

    def test_max_workers_at_least_32(self):
        from kohakuterrarium.api.routes.persistence._executor import (
            _MAX_WORKERS,
        )

        # We cap at 64; below 32 would be too small for an I/O fan-out
        # over hundreds of session files.
        assert _MAX_WORKERS >= 32

    async def test_runs_on_a_different_pool_than_to_thread(self):
        """Two parallel saturating workloads must not serialise.

        Saturate ``asyncio.to_thread``'s default pool with a long
        sleep; concurrently issue ``run_in_persistence_executor`` work.
        If both share the same pool, persistence work waits — total
        wall time would be ``sleep_a + sleep_b``.  With separate pools,
        wall time is ``max(sleep_a, sleep_b)``.
        """
        from kohakuterrarium.api.routes.persistence._executor import (
            run_in_persistence_executor,
        )

        loop = asyncio.get_running_loop()
        default_threads = (
            loop._default_executor._max_workers if loop._default_executor else 32
        )

        def _block(s):
            time.sleep(s)
            return threading.get_ident()

        # Fill the default executor with N concurrent sleeps so a NEW
        # ``to_thread`` would queue.  N = (default pool size).
        t0 = time.monotonic()
        # Saturate the default pool.
        starvers = [asyncio.to_thread(_block, 0.3) for _ in range(default_threads or 8)]
        # Concurrently run persistence work.  If it shared the pool it
        # would queue behind the starvers.
        persist_task = asyncio.create_task(run_in_persistence_executor(_block, 0.3))
        results = await asyncio.gather(*starvers, persist_task)
        elapsed = time.monotonic() - t0
        # Two pools → ~0.3s; one pool → ~0.6s minimum (queueing).
        # 0.55s gives generous CI slack while still failing the
        # single-pool implementation.
        assert elapsed < 0.55, (
            f"persistence work serialised behind to_thread: {elapsed:.2f}s "
            "(expected ~0.3s for separate pools)"
        )
        # All workloads completed.
        assert len(results) == (default_threads or 8) + 1
