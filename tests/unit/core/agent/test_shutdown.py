"""Unit tests for :mod:`kohakuterrarium.core.agent.shutdown`."""

import asyncio
import time
import types

import pytest

from kohakuterrarium.core.agent import shutdown as shutdown_mod
from kohakuterrarium.core.agent.shutdown import (
    AbandonedTask,
    ShutdownDeadline,
    ShutdownState,
    log_late_failure,
    spawn_stage,
)


async def _stubborn(release: asyncio.Event) -> str:
    """Suppress every cancellation until ``release`` is set."""
    while True:
        try:
            await release.wait()
            return "done"
        except asyncio.CancelledError:
            continue


class TestShutdownDeadline:
    async def test_unbounded_join_waits_for_completion(self):
        deadline = ShutdownDeadline(None)
        assert not deadline.bounded
        assert deadline.remaining() is None
        assert not deadline.expired()
        task = asyncio.ensure_future(asyncio.sleep(0.02, result="ok"))
        assert await deadline.join({task}) == set()
        assert task.result() == "ok"

    async def test_join_returns_at_deadline_even_when_task_suppresses_cancel(self):
        release = asyncio.Event()
        task = asyncio.ensure_future(_stubborn(release))
        try:
            await asyncio.sleep(0)
            task.cancel()
            deadline = ShutdownDeadline(0.05)
            started = time.monotonic()
            pending = await deadline.join({task})
            assert pending == {task}
            assert time.monotonic() - started < 1.0
            assert deadline.expired()
            assert not task.done()
        finally:
            release.set()
            assert await task == "done"

    async def test_tighten_only_moves_deadline_earlier(self):
        deadline = ShutdownDeadline(0.05)
        first = deadline.remaining()
        deadline.tighten(60)
        assert deadline.remaining() <= first
        deadline.tighten(0)
        assert deadline.expired()

    async def test_tighten_wakes_an_unbounded_join(self):
        deadline = ShutdownDeadline(None)
        release = asyncio.Event()
        task = asyncio.ensure_future(release.wait())
        join = asyncio.ensure_future(deadline.join({task}))
        await asyncio.sleep(0.01)
        assert not join.done()
        deadline.tighten(0)
        assert await asyncio.wait_for(join, 1) == {task}
        release.set()
        await task

    async def test_force_window_opens_once_after_deadline(self, monkeypatch):
        now = [100.0]
        monkeypatch.setattr(
            shutdown_mod, "time", types.SimpleNamespace(monotonic=lambda: now[0])
        )
        monkeypatch.setattr(shutdown_mod, "FORCE_SETTLE_SECONDS", 0.05)
        deadline = ShutdownDeadline(0)
        assert deadline.remaining(force=True) is None
        deadline.open_force_window()
        assert deadline.remaining(force=True) == pytest.approx(0.05)
        now[0] += 0.02
        deadline.open_force_window()
        assert deadline.remaining(force=True) == pytest.approx(0.03)
        now[0] += 0.05
        assert deadline.remaining(force=True) == 0.0

    async def test_join_consumes_failed_task_exceptions(self):
        async def boom():
            raise ValueError("boom")

        task = asyncio.ensure_future(boom())
        assert await ShutdownDeadline(1).join({task}) == set()
        assert isinstance(task.exception(), ValueError)

    async def test_wait_change_reports_completion_and_timeout(self):
        deadline = ShutdownDeadline(None)
        future = asyncio.get_running_loop().create_future()
        assert await deadline.wait_change(future, 0.01) is False
        future.set_result(None)
        assert await deadline.wait_change(future, 1) is True
        pending = asyncio.get_running_loop().create_future()
        waiter = asyncio.ensure_future(deadline.wait_change(pending, None))
        await asyncio.sleep(0)
        deadline.wake()
        assert await asyncio.wait_for(waiter, 1) is True


class TestShutdownState:
    async def test_clean_state_reports_settled(self):
        report = ShutdownState(ShutdownDeadline(None)).build_report()
        assert report.settled and report.terminated
        assert not report.timed_out and not report.forced
        assert report.abandoned == () and report.errors == ()

    async def test_abandoned_work_is_incomplete_until_it_exits(self):
        release = asyncio.Event()
        task = asyncio.ensure_future(release.wait())
        state = ShutdownState(ShutdownDeadline(0))
        state.expired_stage = "processing"
        state.abandoned.append(AbandonedTask("processing", task))
        state.errors.append(RuntimeError("x"))
        report = state.build_report()
        assert not report.settled and not report.terminated
        assert report.timed_out and report.expired_stage == "processing"
        assert report.abandoned == ("processing",)
        assert report.errors == ("RuntimeError('x')",)
        release.set()
        await task
        later = state.build_report()
        assert later.terminated and not later.settled

    async def test_forced_state_is_never_settled(self):
        state = ShutdownState(ShutdownDeadline(0))
        state.forced = True
        report = state.build_report()
        assert report.forced and report.terminated and not report.settled


class TestHelpers:
    async def test_spawn_stage_names_the_task(self):
        task = spawn_stage("agent:output", asyncio.sleep(0, result=3))
        assert task.get_name() == "shutdown-stage:agent:output"
        assert await task == 3

    async def test_log_late_failure_logs_only_real_failures(self, monkeypatch):
        logged: list[dict] = []
        monkeypatch.setattr(
            shutdown_mod.logger,
            "warning",
            lambda msg, **kw: logged.append({"msg": msg, **kw}),
        )

        async def boom():
            raise OSError("late")

        failed = asyncio.ensure_future(boom())
        cancelled = asyncio.ensure_future(asyncio.sleep(10))
        ok = asyncio.ensure_future(asyncio.sleep(0))
        cancelled.cancel()
        await asyncio.gather(failed, cancelled, ok, return_exceptions=True)
        for task in (failed, cancelled, ok):
            log_late_failure(task)
        assert [entry["error"] for entry in logged] == ["OSError('late')"]


@pytest.mark.parametrize("grace", [0.0, 0.01])
async def test_zero_and_small_grace_are_bounded(grace):
    deadline = ShutdownDeadline(grace)
    task = asyncio.ensure_future(asyncio.sleep(5))
    try:
        assert await deadline.join({task}) == {task}
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
