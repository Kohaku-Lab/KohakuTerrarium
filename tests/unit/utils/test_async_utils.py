"""Unit tests for :mod:`kohakuterrarium.utils.async_utils`."""

import asyncio

import pytest

from kohakuterrarium.utils.async_utils import (
    AsyncQueue,
    collect_async_iterator,
    first_result,
    gather_with_concurrency,
    retry_async,
    run_with_timeout,
    to_thread,
)

# ── run_with_timeout ─────────────────────────────────────────────────


class TestRunWithTimeout:
    async def test_completes_within_timeout(self):
        async def quick():
            return 42

        out = await run_with_timeout(quick(), timeout=1.0)
        assert out == 42

    async def test_returns_default_on_timeout(self):
        async def slow():
            await asyncio.sleep(10)
            return 99

        out = await run_with_timeout(slow(), timeout=0.05, default="hit-default")
        assert out == "hit-default"

    async def test_returns_none_when_no_default(self):
        async def slow():
            await asyncio.sleep(10)
            return 99

        out = await run_with_timeout(slow(), timeout=0.05)
        assert out is None

    async def test_propagates_non_timeout_exceptions(self):
        async def boom():
            raise ValueError("specific")

        with pytest.raises(ValueError, match="specific"):
            await run_with_timeout(boom(), timeout=1.0)


# ── gather_with_concurrency ──────────────────────────────────────────


class TestGatherWithConcurrency:
    async def test_returns_all_results_in_order(self):
        async def make(value, delay):
            await asyncio.sleep(delay)
            return value

        results = await gather_with_concurrency(
            2, make("a", 0.0), make("b", 0.0), make("c", 0.0)
        )
        assert results == ["a", "b", "c"]

    async def test_respects_concurrency_limit(self):
        running: list[int] = []
        peak = 0

        async def task(i):
            nonlocal peak
            running.append(i)
            peak = max(peak, len(running))
            await asyncio.sleep(0.05)
            running.remove(i)
            return i

        out = await gather_with_concurrency(2, *(task(i) for i in range(6)))
        assert sorted(out) == [0, 1, 2, 3, 4, 5]
        # At most 2 tasks were ever simultaneously active.
        assert peak <= 2

    async def test_collects_exceptions_as_results(self):
        async def ok():
            return 1

        async def boom():
            raise RuntimeError("x")

        out = await gather_with_concurrency(2, ok(), boom(), ok())
        assert out[0] == 1
        assert isinstance(out[1], RuntimeError)
        assert out[2] == 1


# ── retry_async ──────────────────────────────────────────────────────


class TestRetryAsync:
    async def test_succeeds_first_try(self):
        attempts = 0

        async def work():
            nonlocal attempts
            attempts += 1
            return "ok"

        out = await retry_async(work, max_attempts=3, base_delay=0.0)
        assert out == "ok"
        assert attempts == 1

    async def test_retries_on_exception_then_succeeds(self, monkeypatch):
        # Skip real sleep
        async def _no_sleep(_t):
            return None

        monkeypatch.setattr(asyncio, "sleep", _no_sleep)

        attempts = 0

        async def flaky():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise RuntimeError("not yet")
            return "ok"

        out = await retry_async(flaky, max_attempts=5, base_delay=0.0)
        assert out == "ok"
        assert attempts == 3

    async def test_raises_after_max_attempts(self, monkeypatch):
        async def _no_sleep(_t):
            return None

        monkeypatch.setattr(asyncio, "sleep", _no_sleep)
        attempts = 0

        async def always_fail():
            nonlocal attempts
            attempts += 1
            raise RuntimeError(f"attempt {attempts}")

        with pytest.raises(RuntimeError, match="attempt 3"):
            await retry_async(always_fail, max_attempts=3, base_delay=0.0)
        assert attempts == 3

    async def test_exponential_backoff_caps_at_max_delay(self, monkeypatch):
        sleeps: list[float] = []

        async def _record_sleep(t):
            sleeps.append(t)

        monkeypatch.setattr(asyncio, "sleep", _record_sleep)

        attempts = 0

        async def fail_then_ok():
            nonlocal attempts
            attempts += 1
            if attempts < 4:
                raise RuntimeError()
            return "ok"

        await retry_async(
            fail_then_ok,
            max_attempts=5,
            base_delay=1.0,
            max_delay=3.0,
            exponential=True,
        )
        # Doubling sequence: 1 -> 2 -> 3 (capped) -> ...
        assert sleeps[0] == 1.0
        assert sleeps[1] == 2.0
        assert sleeps[2] == 3.0  # capped

    async def test_constant_delay_when_not_exponential(self, monkeypatch):
        sleeps: list[float] = []

        async def _record_sleep(t):
            sleeps.append(t)

        monkeypatch.setattr(asyncio, "sleep", _record_sleep)
        attempts = 0

        async def fail_three():
            nonlocal attempts
            attempts += 1
            if attempts < 4:
                raise RuntimeError()
            return "ok"

        await retry_async(fail_three, max_attempts=5, base_delay=0.5, exponential=False)
        # Every sleep is the same base_delay.
        assert sleeps == [0.5, 0.5, 0.5]

    async def test_forwards_args_and_kwargs(self):
        async def add(a, b, *, c):
            return a + b + c

        out = await retry_async(add, 1, 2, c=3, max_attempts=1, base_delay=0.0)
        assert out == 6


# ── collect_async_iterator ───────────────────────────────────────────


class TestCollectAsyncIterator:
    async def test_collects_all_items(self):
        async def gen():
            for i in range(5):
                yield i

        out = await collect_async_iterator(gen())
        assert out == [0, 1, 2, 3, 4]

    async def test_respects_max_items(self):
        async def gen():
            for i in range(100):
                yield i

        out = await collect_async_iterator(gen(), max_items=3)
        assert out == [0, 1, 2]

    async def test_empty_iterator_returns_empty_list(self):
        async def gen():
            return
            yield  # pragma: no cover (unreachable)

        out = await collect_async_iterator(gen())
        assert out == []


# ── first_result ─────────────────────────────────────────────────────


class TestFirstResult:
    async def test_returns_first_winner(self):
        async def fast():
            return "winner"

        async def slow():
            await asyncio.sleep(1.0)
            return "loser"

        out = await first_result(fast(), slow())
        assert out == "winner"

    async def test_cancels_remaining(self):
        cancelled = asyncio.Event()

        async def fast():
            return "winner"

        async def slow():
            try:
                await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return "never"

        out = await first_result(fast(), slow())
        assert out == "winner"
        # Give the cancellation a moment to propagate.
        await asyncio.sleep(0.05)
        assert cancelled.is_set()

    async def test_timeout_raises_and_cancels(self):
        async def slow():
            await asyncio.sleep(5.0)
            return "never"

        with pytest.raises(asyncio.TimeoutError):
            await first_result(slow(), timeout=0.05)

    async def test_first_exception_propagates(self):
        async def boom():
            raise RuntimeError("fast-fail")

        async def slow():
            await asyncio.sleep(5.0)
            return "never"

        with pytest.raises(RuntimeError, match="fast-fail"):
            await first_result(boom(), slow())


# ── AsyncQueue ───────────────────────────────────────────────────────


class TestAsyncQueue:
    async def test_put_and_get_roundtrip(self):
        q = AsyncQueue()
        await q.put("a")
        out = await q.get()
        assert out == "a"

    async def test_put_nowait_and_get_nowait(self):
        q = AsyncQueue()
        q.put_nowait("x")
        assert q.get_nowait() == "x"

    async def test_get_empty_with_short_timeout_raises(self):
        q = AsyncQueue()
        with pytest.raises(asyncio.TimeoutError):
            await q.get(timeout=0.05)

    async def test_put_with_timeout_on_full_raises(self):
        q = AsyncQueue(maxsize=1)
        q.put_nowait("first")
        with pytest.raises(asyncio.TimeoutError):
            await q.put("second", timeout=0.05)

    async def test_put_with_no_timeout_blocks_then_succeeds(self):
        q = AsyncQueue(maxsize=1)
        q.put_nowait("first")

        async def consumer():
            await asyncio.sleep(0.05)
            await q.get()

        async def producer():
            await q.put("second")

        await asyncio.gather(consumer(), producer())
        # ``second`` was successfully queued.
        assert q.get_nowait() == "second"

    async def test_get_batch_collects_available(self):
        q = AsyncQueue()
        for i in range(5):
            q.put_nowait(i)
        out = await q.get_batch(max_items=3)
        assert out == [0, 1, 2]

    async def test_get_batch_with_first_via_timeout(self):
        q = AsyncQueue()
        q.put_nowait("only")
        out = await q.get_batch(max_items=10, timeout=0.1)
        assert out == ["only"]

    async def test_get_batch_propagates_timeout(self):
        q = AsyncQueue()
        with pytest.raises(asyncio.TimeoutError):
            await q.get_batch(max_items=3, timeout=0.05)

    async def test_empty_and_qsize(self):
        q = AsyncQueue()
        assert q.empty() is True
        assert q.qsize() == 0
        q.put_nowait("x")
        assert q.empty() is False
        assert q.qsize() == 1

    async def test_join_returns_after_task_done(self):
        q = AsyncQueue()
        q.put_nowait("x")
        await q.get()
        q.task_done()
        # Once task_done balances put_nowait, join() returns immediately.
        await asyncio.wait_for(q.join(), timeout=0.5)


# ── to_thread ────────────────────────────────────────────────────────


class TestToThread:
    async def test_runs_blocking_function(self):
        def blocking(a, b):
            return a + b

        out = await to_thread(blocking, 2, 3)
        assert out == 5

    async def test_forwards_kwargs(self):
        def fmt(*, sep):
            return sep.join(["a", "b"])

        out = await to_thread(fmt, sep="-")
        assert out == "a-b"

    async def test_propagates_exception(self):
        def boom():
            raise ValueError("from thread")

        with pytest.raises(ValueError, match="from thread"):
            await to_thread(boom)
