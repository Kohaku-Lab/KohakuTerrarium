"""Unit tests for :mod:`kohakuterrarium.core.backgroundify`."""

import asyncio

import pytest

from kohakuterrarium.core.backgroundify import (
    PromotionResult,
    backgroundify,
)


class TestPromotionResult:
    def test_frozen_dataclass(self):
        r = PromotionResult(job_id="j1")
        with pytest.raises(Exception):
            r.job_id = "j2"

    def test_default_message(self):
        r = PromotionResult(job_id="j1")
        assert r.message.startswith("Task promoted")

    def test_custom_message(self):
        r = PromotionResult(job_id="j", message="custom")
        assert r.message == "custom"


class TestBackgroundifyDirect:
    async def test_wait_returns_task_result(self):
        async def work():
            return 42

        task = asyncio.create_task(work())
        h = backgroundify(task, "j1")
        result = await h.wait()
        assert result == 42
        assert h.done is True
        assert h.promoted is False
        assert h.job_id == "j1"

    async def test_wait_propagates_task_exception(self):
        async def boom():
            raise ValueError("oops")

        task = asyncio.create_task(boom())
        h = backgroundify(task, "j1")
        with pytest.raises(ValueError, match="oops"):
            await h.wait()


class TestBackgroundifyPromote:
    async def test_promote_before_completion(self):
        finished = asyncio.Event()

        async def work():
            await finished.wait()
            return "done"

        task = asyncio.create_task(work())
        h = backgroundify(task, "j1")
        # Promote then wait.
        assert h.promote() is True
        result = await h.wait()
        assert isinstance(result, PromotionResult)
        assert result.job_id == "j1"
        assert h.promoted is True

        # Clean up.
        finished.set()
        await task

    async def test_promote_after_completion_returns_false(self):
        async def work():
            return 1

        task = asyncio.create_task(work())
        h = backgroundify(task, "j1")
        # Wait first so task completes.
        await h.wait()
        # Now promote — must return False (already done).
        assert h.promote() is False
        # Not flagged as promoted.
        assert h.promoted is False

    async def test_promote_during_wait_returns_promotion_result(self):
        """When promote() fires WHILE wait() is racing (not pre-promoted),
        the post-race ``if self._promoted`` branch (line 138-139) executes."""
        finished = asyncio.Event()

        async def slow():
            await finished.wait()
            return "done"

        task = asyncio.create_task(slow())
        h = backgroundify(task, "j1")

        async def waiter():
            return await h.wait()

        wait_task = asyncio.create_task(waiter())
        await asyncio.sleep(0.01)  # let waiter start
        # Now promote — race resolves via promotion.
        h.promote()
        result = await wait_task
        assert isinstance(result, PromotionResult)
        # Clean up.
        finished.set()
        try:
            await asyncio.wait_for(task, timeout=1.0)
        except Exception:
            pass

    async def test_background_init_promotes_immediately(self):
        finished = asyncio.Event()

        async def work():
            await finished.wait()
            return "x"

        task = asyncio.create_task(work())
        h = backgroundify(task, "j1", background_init=True)
        # Already promoted at construction time.
        assert h.promoted is True
        result = await h.wait()
        assert isinstance(result, PromotionResult)
        finished.set()
        await task

    async def test_on_bg_complete_fires_for_promoted_task(self):
        invoked: list[tuple[str, object]] = []

        async def cb(job_id, result):
            invoked.append((job_id, result))

        async def work():
            await asyncio.sleep(0.05)
            return "value"

        task = asyncio.create_task(work())
        h = backgroundify(task, "j1", on_bg_complete=cb)
        h.promote()
        # Drive the loop to let the task complete + callback fire.
        await asyncio.sleep(0.1)
        # The callback was scheduled via create_task; give it a tick.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert ("j1", "value") in invoked

    async def test_on_bg_complete_not_fired_for_direct_completion(self):
        invoked: list = []

        async def cb(job_id, result):
            invoked.append((job_id, result))

        async def work():
            return "value"

        task = asyncio.create_task(work())
        h = backgroundify(task, "j1", on_bg_complete=cb)
        await h.wait()
        # Not promoted → callback must not fire.
        assert invoked == []


class TestBackgroundifyCancellation:
    async def test_cancel_propagates_through_handle(self):
        async def work():
            await asyncio.sleep(10)
            return 1

        task = asyncio.create_task(work())
        h = backgroundify(task, "j1")
        h.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await h.wait()
        assert h.done is True
