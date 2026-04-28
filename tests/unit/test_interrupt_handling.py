import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.agent_tools import AgentToolsMixin
from kohakuterrarium.core.events import create_tool_complete_event
from kohakuterrarium.core.job import JobResult
from kohakuterrarium.modules.subagent.base import SubAgentResult


class _FakeOutputRouter:
    def __init__(self):
        self.activities = []

    def notify_activity(self, activity_type, detail, metadata=None):
        self.activities.append((activity_type, detail, metadata or {}))


class _FakeHandle:
    def __init__(self, task, *, promoted=False):
        self.task = task
        self.promoted = promoted

    async def wait(self):
        return await self.task

    @property
    def done(self):
        return self.task.done()


class _FakeSubagentManager:
    def __init__(self):
        self._jobs = {}
        self._result = None
        self.cancel_calls = []

    def get_result(self, job_id):
        return self._result

    async def cancel(self, job_id):
        self.cancel_calls.append(job_id)
        return False


class _FakeExecutor:
    def __init__(self):
        self._result = None
        self.cancel_calls = []
        self._tasks = {}

    def get_result(self, job_id):
        return self._result

    async def cancel(self, job_id):
        self.cancel_calls.append(job_id)
        return False


class _FakeAgentTools(AgentToolsMixin):
    def __init__(self):
        self.output_router = _FakeOutputRouter()
        self._active_handles = {}
        self._direct_job_meta = {}
        self._bg_controller_notify = {}
        self.executor = _FakeExecutor()
        self.subagent_manager = _FakeSubagentManager()
        self._running = True
        self.processed_events = []

    async def _process_event(self, event):
        self.processed_events.append(event)


@pytest.mark.asyncio
async def test_background_completion_notifies_controller_by_default():
    agent = _FakeAgentTools()
    event = create_tool_complete_event("bash_123", "done")

    scheduled = []
    real_create_task = asyncio.create_task

    def _fake_create_task(coro):
        task = real_create_task(coro)
        scheduled.append(task)
        return task

    with patch(
        "kohakuterrarium.core.agent_runtime_tools.asyncio.create_task",
        _fake_create_task,
    ):
        agent._on_bg_complete(event)
        await asyncio.gather(*scheduled)

    assert agent.output_router.activities[0][0] == "tool_done"
    assert agent.processed_events == [event]


@pytest.mark.asyncio
async def test_background_completion_can_skip_controller_notification_but_keep_activity():
    agent = _FakeAgentTools()
    agent._bg_controller_notify["bash_456"] = False
    event = create_tool_complete_event("bash_456", "done")

    with patch(
        "kohakuterrarium.core.agent_runtime_tools.asyncio.create_task"
    ) as create_task:
        agent._on_bg_complete(event)

    assert agent.output_router.activities[0][0] == "tool_done"
    assert agent.processed_events == []
    create_task.assert_not_called()
    assert "bash_456" not in agent._bg_controller_notify


@pytest.mark.asyncio
async def test_wait_handles_emits_direct_completion_as_each_job_finishes():
    agent = _FakeAgentTools()
    controller = SimpleNamespace(
        conversation=SimpleNamespace(append=lambda *args, **kwargs: None)
    )

    fast_done = asyncio.Event()
    release_slow = asyncio.Event()

    async def fast_job():
        fast_done.set()
        return JobResult(job_id="bash_fast", output="fast done")

    async def slow_job():
        await release_slow.wait()
        return JobResult(job_id="bash_slow", output="slow done")

    fast_task = asyncio.create_task(fast_job())
    slow_task = asyncio.create_task(slow_job())
    agent._active_handles["bash_fast"] = _FakeHandle(fast_task)
    agent._active_handles["bash_slow"] = _FakeHandle(slow_task)
    agent._register_direct_job("bash_fast", kind="tool", name="bash")
    agent._register_direct_job("bash_slow", kind="tool", name="bash")

    wait_task = asyncio.create_task(
        agent._wait_handles(
            {
                "bash_fast": agent._active_handles["bash_fast"],
                "bash_slow": agent._active_handles["bash_slow"],
            },
            ["bash_fast", "bash_slow"],
            controller,
            {},
            False,
        )
    )

    await fast_done.wait()
    for _ in range(20):
        if agent.output_router.activities:
            break
        await asyncio.sleep(0)

    assert [activity[2]["job_id"] for activity in agent.output_router.activities] == [
        "bash_fast"
    ]
    assert "bash_fast" not in agent._active_handles
    assert "bash_slow" in agent._active_handles

    release_slow.set()
    results, had_promotions = await wait_task

    assert had_promotions is False
    assert list(results) == ["bash_fast", "bash_slow"]
    assert [activity[2]["job_id"] for activity in agent.output_router.activities] == [
        "bash_fast",
        "bash_slow",
    ]


@pytest.mark.asyncio
async def test_finalize_interrupted_direct_tool_emits_terminal_activity_and_clears_tracking():
    agent = _FakeAgentTools()

    async def sleeper():
        await asyncio.sleep(10)

    task = asyncio.create_task(sleeper())
    handle = _FakeHandle(task)
    agent._active_handles["bash_123"] = handle
    agent._register_direct_job("bash_123", kind="tool", name="bash")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    agent.executor._result = JobResult(
        job_id="bash_123", error="User manually interrupted this job."
    )

    await agent._finalize_interrupted_direct_job("bash_123")

    assert "bash_123" not in agent._active_handles
    assert "bash_123" not in agent._direct_job_meta
    assert len(agent.output_router.activities) == 1
    activity_type, detail, metadata = agent.output_router.activities[0]
    assert activity_type == "tool_error"
    assert "INTERRUPTED" in detail
    assert metadata["job_id"] == "bash_123"
    assert metadata["interrupted"] is True
    assert metadata["final_state"] == "interrupted"


@pytest.mark.asyncio
async def test_finalize_interrupted_direct_subagent_preserves_subagent_metadata():
    agent = _FakeAgentTools()

    async def sleeper():
        await asyncio.sleep(10)

    task = asyncio.create_task(sleeper())
    handle = _FakeHandle(task)
    agent._active_handles["agent_explore_123"] = handle
    agent._register_direct_job("agent_explore_123", kind="subagent", name="explore")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    agent.subagent_manager._result = SubAgentResult(
        output="User manually interrupted this job.",
        success=False,
        error="User manually interrupted this job.",
        turns=2,
        metadata={"tools_used": ["grep", "read"]},
        total_tokens=42,
        prompt_tokens=30,
        completion_tokens=12,
        duration=1.25,
    )

    await agent._finalize_interrupted_direct_job("agent_explore_123")

    activity_type, _, metadata = agent.output_router.activities[0]
    assert activity_type == "subagent_error"
    assert metadata["interrupted"] is True
    assert metadata["final_state"] == "interrupted"
    assert metadata["tools_used"] == ["grep", "read"]
    assert metadata["turns"] == 2
    assert metadata["total_tokens"] == 42


def test_agent_interrupt_only_cancels_tracked_direct_jobs():
    cancelled = []

    class _FakeProcessingTask:
        def __init__(self):
            self._done = False

        def done(self):
            return self._done

        def cancel(self):
            cancelled.append("processing")
            self._done = True

    fake = SimpleNamespace(
        _interrupt_requested=False,
        controller=SimpleNamespace(_interrupted=False),
        _processing_task=_FakeProcessingTask(),
        _active_handles={"job_a": object(), "job_b": object()},
        plugins=None,
        config=SimpleNamespace(name="test-agent"),
    )

    def _interrupt_direct_job(job_id):
        cancelled.append(job_id)
        return True

    fake._interrupt_direct_job = _interrupt_direct_job

    Agent.interrupt(fake)

    assert fake._interrupt_requested is True
    assert fake.controller._interrupted is True
    assert cancelled == ["processing", "job_a", "job_b"]


# Phase 3 removed ``KohakuManager``; the cancel-job tests previously
# guarded ``manager.agent_cancel_job`` semantics.  The same priority
# (direct-interrupt → executor → subagent_manager) now lives on the
# studio sessions per-creature ops layer; it is exercised in
# ``tests/unit/test_session_creature_ctl.py`` (added in Phase 2).
# The two manager-shaped tests are intentionally absent here.
