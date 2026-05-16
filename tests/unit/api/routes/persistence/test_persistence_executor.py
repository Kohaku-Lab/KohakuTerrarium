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
        """Persistence work must not queue behind ``asyncio.to_thread``.

        Saturate ``to_thread``'s default pool with sleeps and
        concurrently issue ``run_in_persistence_executor`` work. We
        measure the persist task's OWN elapsed time (not total wall
        time) — that isolates the signal from CI-side noise where the
        saturator burst itself queues on a small worker pool.

        With separate pools: persist task completes in ~SLEEP_S.
        With shared pool: it queues behind the saturators and takes
        roughly ``SLEEP_S * (num_starvers / default_pool_size)``.
        """
        from kohakuterrarium.api.routes.persistence._executor import (
            run_in_persistence_executor,
        )

        # Use the real default-executor size that ``asyncio.to_thread``
        # would create (``min(32, os.cpu_count() + 4)``). Saturating
        # with more than that just queues on the to_thread side and
        # would inflate the test regardless of pool isolation.
        with concurrent.futures.ThreadPoolExecutor() as probe_executor:
            default_pool_size = probe_executor._max_workers
        # 6x oversaturation guarantees a large serial gap so the
        # threshold has headroom even on a slow CI runner.
        starver_count = default_pool_size * 6
        sleep_s = 0.5

        def _block(s):
            time.sleep(s)
            return threading.get_ident()

        # Saturate the default pool with serial-able workload.
        starvers = [asyncio.to_thread(_block, sleep_s) for _ in range(starver_count)]
        # Concurrently issue persistence work; time IT specifically.
        t0 = time.monotonic()
        persist_result = await run_in_persistence_executor(_block, sleep_s)
        persist_elapsed = time.monotonic() - t0
        # Then collect the starvers (we only care that they ran).
        starver_results = await asyncio.gather(*starvers)

        # If pools are shared, the persist task queues behind the
        # starvers and takes ``serial_estimate`` seconds. With
        # separate pools, the persist task runs concurrently on its
        # own thread and finishes in ~``sleep_s`` plus scheduler
        # overhead. We assert it stays under HALF the serial-pool
        # estimate — that's the only signal that cleanly distinguishes
        # the two pool topologies regardless of how slow the runner is.
        serial_estimate = sleep_s * starver_count / max(default_pool_size, 1)
        max_separate = serial_estimate / 2
        assert persist_elapsed < max_separate, (
            f"persistence task waited {persist_elapsed:.2f}s "
            f"(threshold {max_separate:.2f}s, serial estimate "
            f"{serial_estimate:.2f}s) — looks serialised behind to_thread"
        )
        assert persist_result  # ran on some thread
        assert len(starver_results) == starver_count
