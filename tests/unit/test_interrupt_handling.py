import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.agent_tools import AgentToolsMixin
from kohakuterrarium.core.job import JobResult
from kohakuterrarium.modules.subagent.base import SubAgentResult
from kohakuterrarium.serving.manager import KohakuManager


class _FakeOutputRouter:
    def __init__(self):
        self.activities = []

    def notify_activity(self, activity_type, detail, metadata=None):
        self.activities.append((activity_type, detail, metadata or {}))


class _FakeHandle:
    def __init__(self, task, *, promoted=False):
        self.task = task
        self.promoted = promoted

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
        self.executor = _FakeExecutor()
        self.subagent_manager = _FakeSubagentManager()


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


@pytest.mark.asyncio
async def test_manager_cancel_job_prefers_direct_interrupt_path():
    manager = KohakuManager()
    agent = SimpleNamespace(
        _interrupt_direct_job=lambda job_id: job_id == "direct_1",
        executor=SimpleNamespace(cancel=lambda job_id: asyncio.sleep(0, result=False)),
        subagent_manager=SimpleNamespace(
            cancel=lambda job_id: asyncio.sleep(0, result=False)
        ),
    )
    manager._agents["a1"] = SimpleNamespace(agent=agent)

    assert await manager.agent_cancel_job("a1", "direct_1") is True


@pytest.mark.asyncio
async def test_manager_cancel_job_falls_back_for_background_jobs():
    manager = KohakuManager()
    executor_calls = []
    subagent_calls = []

    async def executor_cancel(job_id):
        executor_calls.append(job_id)
        return True

    async def subagent_cancel(job_id):
        subagent_calls.append(job_id)
        return False

    agent = SimpleNamespace(
        _interrupt_direct_job=lambda job_id: False,
        executor=SimpleNamespace(cancel=executor_cancel),
        subagent_manager=SimpleNamespace(cancel=subagent_cancel),
    )
    manager._agents["a1"] = SimpleNamespace(agent=agent)

    assert await manager.agent_cancel_job("a1", "bg_1") is True
    assert executor_calls == ["bg_1"]
    assert subagent_calls == []
