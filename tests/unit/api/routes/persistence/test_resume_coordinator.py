"""Unit tests for the per-session resume coordinator."""

import asyncio

import pytest

from kohakuterrarium.api.routes.persistence.resume_coordinator import (
    ResumeCoordinator,
)


@pytest.mark.asyncio
async def test_same_session_shares_one_in_flight_resume():
    coordinator = ResumeCoordinator()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def resume():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return object()

    first = asyncio.create_task(coordinator.run("session-a", resume))
    await started.wait()
    second = asyncio.create_task(coordinator.run("session-a", resume))
    await asyncio.sleep(0)

    assert calls == 1
    release.set()
    first_result, second_result = await asyncio.gather(first, second)
    assert first_result is second_result


@pytest.mark.asyncio
async def test_different_sessions_resume_independently():
    coordinator = ResumeCoordinator()
    started: list[str] = []
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def resume(key: str):
        started.append(key)
        if len(started) == 2:
            both_started.set()
        await release.wait()
        return key

    first = asyncio.create_task(coordinator.run("session-a", lambda: resume("a")))
    second = asyncio.create_task(coordinator.run("session-b", lambda: resume("b")))
    await asyncio.wait_for(both_started.wait(), timeout=1)

    release.set()
    assert await asyncio.gather(first, second) == ["a", "b"]


@pytest.mark.asyncio
async def test_cancelling_waiter_does_not_cancel_shared_resume():
    coordinator = ResumeCoordinator()
    started = asyncio.Event()
    release = asyncio.Event()

    async def resume():
        started.set()
        await release.wait()
        return "resumed"

    cancelled_waiter = asyncio.create_task(coordinator.run("session-a", resume))
    await started.wait()
    remaining_waiter = asyncio.create_task(coordinator.run("session-a", resume))
    await asyncio.sleep(0)

    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    release.set()
    assert await remaining_waiter == "resumed"


@pytest.mark.asyncio
async def test_conflicting_intent_is_rejected_without_second_operation():
    coordinator = ResumeCoordinator()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def resume():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return "resumed"

    first = asyncio.create_task(coordinator.run("session-a", resume, intent="worker-a"))
    await started.wait()
    with pytest.raises(RuntimeError, match="conflicting resume request"):
        await coordinator.run("session-a", resume, intent="worker-b")
    release.set()

    assert await first == "resumed"
    assert calls == 1


@pytest.mark.asyncio
async def test_failure_is_removed_and_next_call_retries():
    coordinator = ResumeCoordinator()
    attempts = 0

    async def resume():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("resume failed")
        return "resumed"

    with pytest.raises(RuntimeError, match="resume failed"):
        await coordinator.run("session-a", resume)

    assert await coordinator.run("session-a", resume) == "resumed"
    assert attempts == 2
