"""Tool completion ownership and promoted-result normalization."""

import asyncio

import pytest

from kohakuterrarium.core.jobs.executor import Executor
from kohakuterrarium.core.jobs.job import JobResult, JobStore
from kohakuterrarium.core.tools.dispatch import tool_background_handle
from kohakuterrarium.modules.tool.base import BaseTool, ToolResult


class _GatedTool(BaseTool):
    def __init__(self, *, error=None):
        super().__init__()
        self.error = error
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def tool_name(self):
        return "gated"

    @property
    def description(self):
        return "A gated tool result."

    async def _execute(self, args, **kwargs):
        self.started.set()
        await self.release.wait()
        return ToolResult(output="payload", error=self.error)


class TestToolBackgroundHandle:
    @pytest.mark.parametrize(
        "outcome", ["success", "error", "cancel_before_start", "cancel_running"]
    )
    async def test_promoted_outcome_does_not_depend_on_result_retention(self, outcome):
        executor_events = []
        ex = Executor(JobStore(max_completed=0), on_complete=executor_events.append)
        tool = _GatedTool(error="failure" if outcome == "error" else None)
        ex.register_tool(tool)
        delivered = asyncio.get_running_loop().create_future()

        async def on_complete(job_id, result):
            delivered.set_result((job_id, result))

        jid = await ex.submit(tool.tool_name, {}, is_direct=True)
        task = ex.get_task(jid)
        handle = tool_background_handle(task, jid, True, True, on_complete)
        try:
            if outcome != "cancel_before_start":
                await asyncio.wait_for(tool.started.wait(), 3)
            if outcome.startswith("cancel"):
                assert await ex.cancel(jid)
            else:
                tool.release.set()
            delivered_id, result = await asyncio.wait_for(delivered, 3)
            assert delivered_id == jid
            assert isinstance(result, JobResult)
            assert result.cancelled is outcome.startswith("cancel")
            assert result.success is (outcome == "success")
            if outcome.startswith("cancel"):
                assert result.error == "User manually interrupted this job."
            else:
                assert result.output == "payload"
                assert result.error == tool.error
            assert handle.done
            assert executor_events == []
            assert ex.get_result(jid) is None
        finally:
            tool.release.set()
            await asyncio.gather(task, return_exceptions=True)
