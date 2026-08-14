"""Unit tests for :mod:`kohakuterrarium.core.executor`."""

import asyncio
import types
from pathlib import Path
from typing import Any

import pytest

from kohakuterrarium.core.events import EventType
from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.job import JobState, JobStore
from kohakuterrarium.modules.tool.base import (
    BaseTool,
    ExecutionMode,
    ToolConfig,
    ToolResult,
)
from kohakuterrarium.parsing.events import ToolCallEvent

# ── tool fixtures ────────────────────────────────────────────────


class _EchoTool(BaseTool):
    """Returns ``args["msg"]`` as output. Direct-mode."""

    def __init__(self, *, mode=ExecutionMode.DIRECT, max_output=0):
        super().__init__(ToolConfig(max_output=max_output))
        self._mode = mode

    @property
    def tool_name(self):
        return "echo"

    @property
    def description(self):
        return "echo"

    @property
    def execution_mode(self):
        return self._mode

    async def _execute(self, args, **kwargs):
        return ToolResult(output=str(args.get("msg", "")))


class _FailTool(BaseTool):
    @property
    def tool_name(self):
        return "fail"

    @property
    def description(self):
        return "fail"

    async def _execute(self, args, **kwargs):
        raise RuntimeError("boom")


class _ManualReadTool(BaseTool):
    require_manual_read = True

    @property
    def tool_name(self):
        return "manual"

    @property
    def description(self):
        return "manual"

    async def _execute(self, args, **kwargs):
        return ToolResult(output="never reached")


class _UnsafeTool(BaseTool):
    is_concurrency_safe = False

    def __init__(self, hold_seconds=0.05, *, timeout=60.0, name="unsafe"):
        super().__init__(ToolConfig(timeout=timeout))
        self.hold = hold_seconds
        self.name = name
        self.starts: list[float] = []

    @property
    def tool_name(self):
        return self.name

    @property
    def description(self):
        return "unsafe"

    async def _execute(self, args, **kwargs):
        self.starts.append(asyncio.get_event_loop().time())
        await asyncio.sleep(self.hold)
        return ToolResult(output="ok")


class _SlowTool(BaseTool):
    @property
    def tool_name(self):
        return "slow"

    @property
    def description(self):
        return "slow"

    async def _execute(self, args, **kwargs):
        await asyncio.sleep(args.get("seconds", 0.1))
        return ToolResult(output="done")


# ── basic submit / wait_for ──────────────────────────────────────


class TestSubmitWaitFor:
    async def test_register_and_submit(self):
        ex = Executor()
        ex.register_tool(_EchoTool())
        jid = await ex.submit("echo", {"msg": "hi"})
        result = await ex.wait_for(jid)
        assert result is not None
        assert result.output == "hi"
        assert result.success is True
        status = ex.get_status(jid)
        assert status.state == JobState.DONE
        assert ex.get_result(jid) is not None

    async def test_unknown_tool_raises(self):
        ex = Executor()
        with pytest.raises(ValueError, match="not registered"):
            await ex.submit("nope", {})

    async def test_custom_job_id(self):
        ex = Executor()
        ex.register_tool(_EchoTool())
        await ex.submit("echo", {"msg": "x"}, job_id="custom-1")
        assert ex.get_status("custom-1") is not None

    async def test_submit_from_event(self):
        ex = Executor()
        ex.register_tool(_EchoTool())
        evt = ToolCallEvent(name="echo", args={"msg": "via-event"}, raw="")
        jid = await ex.submit_from_event(evt)
        result = await ex.wait_for(jid)
        assert result.output == "via-event"

    async def test_get_tool_and_list(self):
        ex = Executor()
        tool = _EchoTool()
        ex.register_tool(tool)
        assert ex.get_tool("echo") is tool
        assert ex.get_tool("missing") is None
        assert ex.list_tools() == ["echo"]


# ── error paths ──────────────────────────────────────────────────


class TestErrorPaths:
    async def test_tool_exception_becomes_error_result(self):
        ex = Executor()
        ex.register_tool(_FailTool())
        jid = await ex.submit("fail", {})
        result = await ex.wait_for(jid)
        # ``BaseTool.execute`` swallows the exception and wraps it.
        assert result.error is not None
        # Job state stays DONE (error captured in result, not raised).
        # ``result.success`` is False because ``error`` is set.
        assert result.success is False

    async def test_require_manual_read_blocks(self):
        ex = Executor()
        ex.register_tool(_ManualReadTool())
        jid = await ex.submit("manual", {})
        result = await ex.wait_for(jid)
        assert result.error is not None
        assert "info" in result.error.lower()
        assert result.exit_code == 1
        status = ex.get_status(jid)
        assert status.state == JobState.ERROR
        assert ex.get_result(jid) is result


def _agent(skill_mode="dynamic", has_info=True):
    """Minimal agent stub exposing the fields the manual-read gate reads."""
    tools = {"info": object()} if has_info else {}
    registry = types.SimpleNamespace(get_tool=tools.get)
    return types.SimpleNamespace(
        config=types.SimpleNamespace(skill_mode=skill_mode),
        registry=registry,
    )


class TestManualReadGate:
    async def test_dynamic_with_info_still_blocks(self):
        ex = Executor()
        ex._agent = _agent(skill_mode="dynamic", has_info=True)
        ex.register_tool(_ManualReadTool())
        result = await ex.wait_for(await ex.submit("manual", {}))
        assert result.error is not None
        assert result.exit_code == 1

    async def test_static_mode_bypasses_gate(self):
        # Full docs already live in the prompt — the info-read gate is moot.
        ex = Executor()
        ex._agent = _agent(skill_mode="static")
        ex.register_tool(_ManualReadTool())
        result = await ex.wait_for(await ex.submit("manual", {}))
        assert result.error is None
        assert result.output == "never reached"

    async def test_missing_info_tool_bypasses_gate(self):
        # Without an ``info`` tool the block is unsatisfiable, so the tool
        # would be permanently undispatchable — the gate must relax.
        ex = Executor()
        ex._agent = _agent(skill_mode="dynamic", has_info=False)
        ex.register_tool(_ManualReadTool())
        result = await ex.wait_for(await ex.submit("manual", {}))
        assert result.error is None
        assert result.output == "never reached"


# ── on_complete callback + event queue ───────────────────────────


class TestOnCompleteCallback:
    async def test_callback_fires_for_background(self):
        seen = []

        ex = Executor(on_complete=lambda e: seen.append(e))
        ex.register_tool(_EchoTool())
        jid = await ex.submit("echo", {"msg": "x"})
        await ex.wait_for(jid)
        # Callback delivered exactly one TriggerEvent.
        assert len(seen) == 1
        assert seen[0].type == EventType.TOOL_COMPLETE
        # Event also enqueued.
        evt = ex.get_next_event_nowait()
        assert evt is not None

    async def test_is_direct_skips_callback_and_event(self):
        seen = []
        ex = Executor(on_complete=lambda e: seen.append(e))
        ex.register_tool(_EchoTool())
        jid = await ex.submit("echo", {"msg": "x"}, is_direct=True)
        await ex.wait_for(jid)
        assert seen == []
        assert ex.get_next_event_nowait() is None

    async def test_get_next_event_with_timeout(self):
        ex = Executor()
        evt = await ex.get_next_event(timeout=0.01)
        assert evt is None

    async def test_get_next_event_blocking(self):
        ex = Executor()
        ex.register_tool(_EchoTool())

        async def submit_later():
            await asyncio.sleep(0.01)
            await ex.submit("echo", {"msg": "x"})

        asyncio.create_task(submit_later())
        evt = await ex.get_next_event(timeout=1.0)
        assert evt is not None
        assert evt.type == EventType.TOOL_COMPLETE

    async def test_event_failed_carries_error(self):
        ex = Executor()
        ex.register_tool(_FailTool())
        jid = await ex.submit("fail", {})
        await ex.wait_for(jid)
        evt = ex.get_next_event_nowait()
        # Error captured in result; the on-complete path sees the error string.
        assert evt is not None
        # Status reflects the error captured in result.
        status = ex.get_status(jid)
        # The BaseTool execute path catches the exception and returns a
        # ToolResult with ``error`` set; ``state`` follows ``result.success``.
        # So state should be ERROR.
        assert status.state == JobState.ERROR


# ── cancel ───────────────────────────────────────────────────────


class TestCancel:
    async def test_cancel_running_job(self):
        ex = Executor()
        ex.register_tool(_SlowTool())
        jid = await ex.submit("slow", {"seconds": 1.0})
        # Give the task a tick to start.
        await asyncio.sleep(0.01)
        ok = await ex.cancel(jid)
        assert ok is True
        result = await ex.wait_for(jid, timeout=1.0)
        assert result is not None
        assert "interrupted" in (result.error or "").lower()
        status = ex.get_status(jid)
        assert status.state == JobState.CANCELLED

    async def test_cancel_unknown_returns_false(self):
        ex = Executor()
        assert await ex.cancel("nope") is False

    async def test_cancel_completed_returns_false(self):
        ex = Executor()
        ex.register_tool(_EchoTool())
        jid = await ex.submit("echo", {"msg": "x"})
        await ex.wait_for(jid)
        assert await ex.cancel(jid) is False


# ── concurrency-safety serial lock ───────────────────────────────


class TestExecutorException:
    async def test_unexpected_exception_path(self, monkeypatch):
        """Force an exception OUTSIDE the BaseTool wrapper (raw exception
        in ``_run_tool``) — covers the ``except Exception`` arm. We do
        this by mocking ``normalize_tool_output`` to raise."""
        ex = Executor()
        ex.register_tool(_EchoTool())
        from kohakuterrarium.core import executor as ex_mod

        def explode(*a, **k):
            raise RuntimeError("normalize boom")

        monkeypatch.setattr(ex_mod, "normalize_tool_output", explode)
        jid = await ex.submit("echo", {"msg": "hi"})
        result = await ex.wait_for(jid)
        assert result is not None
        assert "normalize boom" in (result.error or "")
        status = ex.get_status(jid)
        assert status.state == JobState.ERROR


class TestEventsAsyncGen:
    async def test_events_yields_in_order(self):
        ex = Executor()
        ex.register_tool(_EchoTool())
        events = []

        async def consume():
            async for evt in ex.events():
                events.append(evt)
                if len(events) >= 2:
                    return

        task = asyncio.create_task(consume())
        await ex.submit("echo", {"msg": "a"})
        await ex.submit("echo", {"msg": "b"})
        await asyncio.wait_for(task, timeout=2.0)
        assert len(events) == 2


class TestEventQueueOnException:
    async def test_failing_tool_emits_event(self):
        ex = Executor()
        ex.register_tool(_FailTool())
        await ex.submit("fail", {})
        evt = await ex.get_next_event(timeout=2.0)
        assert evt is not None
        assert evt.type == EventType.TOOL_COMPLETE


class TestSerialLock:
    async def test_unsafe_tools_serialised(self):
        ex = Executor()
        tool = _UnsafeTool(hold_seconds=0.03)
        ex.register_tool(tool)
        await asyncio.gather(
            ex.submit("unsafe", {}),
            ex.submit("unsafe", {}),
            ex.submit("unsafe", {}),
        )
        results = await ex.wait_all(timeout=5.0)
        assert len(results) == 3
        starts = sorted(tool.starts)
        for a, b in zip(starts, starts[1:]):
            assert b - a >= 0.02

    async def test_allow_concurrent_skips_serial_lock(self):
        ex = Executor()
        tool = _UnsafeTool(hold_seconds=0.03)
        ex.register_tool(tool)
        await ex.submit("unsafe", {})
        await asyncio.sleep(0)
        concurrent = await ex.submit("unsafe", {"allow_concurrent": True})
        await ex.wait_for(concurrent, timeout=1.0)

        assert len(tool.starts) == 2
        assert tool.starts[1] - tool.starts[0] < 0.02

    async def test_text_allow_concurrent_skips_serial_lock(self):
        ex = Executor()
        tool = _UnsafeTool(hold_seconds=0.03)
        ex.register_tool(tool)
        await ex.submit("unsafe", {})
        await asyncio.sleep(0)
        concurrent = await ex.submit("unsafe", {"allow_concurrent": "true"})
        await ex.wait_for(concurrent, timeout=1.0)

        assert len(tool.starts) == 2

    async def test_bash_timeout_includes_lock_wait(self):
        ex = Executor()
        tool = _UnsafeTool(hold_seconds=0.08, timeout=0.02, name="bash")
        ex.register_tool(tool)
        await ex.submit("bash", {})
        await asyncio.sleep(0)
        blocked = await ex.submit("bash", {"timeout": 0.01})
        result = await ex.wait_for(blocked, timeout=1.0)

        assert result is not None
        assert result.metadata["blocked"] is True
        assert result.metadata["command_started"] is False
        assert len(tool.starts) == 1

    async def test_bash_timeout_budget_is_forwarded_after_lock(self):
        ex = Executor()
        tool = _UnsafeTool(hold_seconds=0.01, timeout=0.2, name="bash")
        ex.register_tool(tool)
        first = await ex.submit("bash", {})
        await ex.wait_for(first, timeout=1.0)
        second = await ex.submit("bash", {"timeout": 0.2})
        await ex.wait_for(second, timeout=1.0)

        assert len(tool.starts) == 2

    async def test_bash_timeout_rejects_negative_values(self):
        ex = Executor()
        tool = _UnsafeTool(name="bash")
        ex.register_tool(tool)
        job_id = await ex.submit("bash", {"timeout": -1})
        result = await ex.wait_for(job_id, timeout=1.0)

        assert result is not None
        assert result.error == "timeout must be >= 0"
        assert tool.starts == []

    async def test_bash_timeout_rejects_non_finite_values(self):
        ex = Executor()
        tool = _UnsafeTool(name="bash")
        ex.register_tool(tool)
        job_id = await ex.submit("bash", {"timeout": "nan"})
        result = await ex.wait_for(job_id, timeout=1.0)

        assert result is not None
        assert result.error == "timeout must be finite"
        assert tool.starts == []


# ── wait_for / wait_all timeouts ─────────────────────────────────


class TestWaitTimeouts:
    async def test_wait_for_returns_cached_result(self):
        ex = Executor()
        ex.register_tool(_EchoTool())
        jid = await ex.submit("echo", {"msg": "x"})
        await ex.wait_for(jid)
        await asyncio.sleep(0)
        assert ex.get_task(jid) is None
        cached = await ex.wait_for(jid)
        assert cached is not None
        assert cached.output == "x"

    async def test_wait_for_missing(self):
        ex = Executor()
        assert await ex.wait_for("ghost") is None

    async def test_wait_for_timeout(self):
        ex = Executor()
        ex.register_tool(_SlowTool())
        jid = await ex.submit("slow", {"seconds": 0.05})
        out = await ex.wait_for(jid, timeout=0.005)
        assert out is None
        assert ex.get_status(jid).state == JobState.RUNNING
        task = ex.get_task(jid)
        assert task is not None
        assert task.done() is False

        completed = await ex.wait_for(jid, timeout=1.0)
        assert completed is not None
        assert completed.output == "done"
        assert completed.error is None

    async def test_wait_all_empty(self):
        ex = Executor()
        assert await ex.wait_all() == {}

    async def test_wait_all_collects_results(self):
        ex = Executor()
        ex.register_tool(_EchoTool())
        await ex.submit("echo", {"msg": "a"})
        await ex.submit("echo", {"msg": "b"})
        results = await ex.wait_all(timeout=5.0)
        outputs = sorted(r.output for r in results.values())
        assert outputs == ["a", "b"]

    async def test_wait_all_timeout_returns_done_so_far(self):
        ex = Executor()
        ex.register_tool(_SlowTool())
        jid = await ex.submit("slow", {"seconds": 0.05})
        await asyncio.sleep(0.001)
        out = await ex.wait_all(timeout=0.005)
        assert jid not in out
        assert ex.get_status(jid).state == JobState.RUNNING

        completed = await ex.wait_for(jid, timeout=1.0)
        assert completed is not None
        assert completed.output == "done"


# ── output normalisation hook ────────────────────────────────────


class TestOutputNormalisation:
    async def test_max_output_truncates(self):
        ex = Executor()
        ex.register_tool(_EchoTool(max_output=10))
        jid = await ex.submit("echo", {"msg": "x" * 1000})
        result = await ex.wait_for(jid)
        assert "truncated" in result.output
        assert result.metadata.get("truncated") is True

    async def test_truncation_note_points_at_bash_output_file(self):
        # Bash materializes full output to a temp file and exposes the path
        # as metadata["raw_output_path"]; the truncation hint must surface it.
        class _BashLikeTool(_EchoTool):
            @property
            def tool_name(self):
                return "bash"

            async def _execute(self, args, **kwargs):
                return ToolResult(
                    output=str(args.get("msg", "")),
                    metadata={
                        "raw_output_path": "/tmp/kohakuterrarium-bash/bash_1.log"
                    },
                )

        ex = Executor()
        ex.register_tool(_BashLikeTool(max_output=10))
        jid = await ex.submit("bash", {"msg": "x" * 1000})
        result = await ex.wait_for(jid)
        assert (
            "Full output saved to /tmp/kohakuterrarium-bash/bash_1.log" in result.output
        )
        assert "use read to view it" in result.output


# ── pending / running / task accessors ───────────────────────────


class TestAccessors:
    async def test_get_pending_count(self):
        ex = Executor()
        ex.register_tool(_SlowTool())
        assert ex.get_pending_count() == 0
        jid = await ex.submit("slow", {"seconds": 1.0})
        assert ex.get_pending_count() == 1
        await ex.cancel(jid)
        await ex.wait_for(jid)
        assert ex.get_pending_count() == 0

    async def test_completed_tasks_follow_job_store_retention(self):
        ex = Executor(job_store=JobStore(max_completed=2))
        ex.register_tool(_EchoTool())
        job_ids = []
        for index in range(5):
            job_id = await ex.submit("echo", {"msg": str(index)})
            job_ids.append(job_id)
            await ex.wait_for(job_id)
        await asyncio.sleep(0)

        assert ex._tasks == {}
        assert ex.get_pending_count() == 0
        assert ex.get_result(job_ids[0]) is None
        assert ex.get_result(job_ids[-1]).output == "4"
        assert set(await ex.wait_all()) == set(job_ids[-2:])

    async def test_get_task(self):
        ex = Executor()
        ex.register_tool(_EchoTool())
        jid = await ex.submit("echo", {"msg": "x"})
        task = ex.get_task(jid)
        assert task is not None
        await task
        await asyncio.sleep(0)
        assert ex.get_task(jid) is None
        assert ex.get_task("nope") is None

    async def test_get_running_jobs(self):
        ex = Executor()
        ex.register_tool(_SlowTool())
        jid = await ex.submit("slow", {"seconds": 1.0})
        await asyncio.sleep(0.01)
        running = ex.get_running_jobs()
        assert any(j.job_id == jid for j in running)
        await ex.cancel(jid)


# ── ToolContext build / plugin runtime_services ──────────────────


class TestEmitToolWaitEdges:
    def test_no_agent_no_op(self):
        ex = Executor()
        ex._agent = None
        # Must not raise.
        ex._emit_tool_wait("bash", 10.0, "serial_lock")

    def test_no_router_no_op(self):
        ex = Executor()
        ex._agent = types.SimpleNamespace(output_router=None)
        # Must not raise.
        ex._emit_tool_wait("bash", 10.0, "serial_lock")


class TestWrapToolExecuteBuildsContext:
    def test_called_with_none_context_builds_one(self):
        ex = Executor()
        tool = _EchoTool()
        ex.register_tool(tool)
        # Call with context=None — builds a fresh ToolContext (line 144).
        out = ex._wrap_tool_execute(tool, {}, job_id="x", context=None)
        # Without plugins, returns tool.execute directly (identity by ref
        # may differ due to bound-method per-access; verify it's callable).
        assert callable(out)


class TestRunToolExceptionWithDirect:
    async def test_exception_in_run_tool_direct_skips_event(self, monkeypatch):
        """Force an exception in _run_tool with is_direct=True so we
        cover the ``if not is_direct:`` skip-path in the except handler."""
        ex = Executor()
        ex.register_tool(_FailTool())
        from kohakuterrarium.core import executor as ex_mod

        def explode(*a, **k):
            raise RuntimeError("boom")

        monkeypatch.setattr(ex_mod, "normalize_tool_output", explode)
        jid = await ex.submit("fail", {}, is_direct=True)
        result = await ex.wait_for(jid)
        # No event was queued (is_direct skip).
        assert ex.get_next_event_nowait() is None
        assert result.error


class TestGetNextEventBlocking:
    async def test_blocks_until_event_arrives(self):
        ex = Executor()
        ex.register_tool(_EchoTool())

        async def submit_later():
            await asyncio.sleep(0.005)
            await ex.submit("echo", {"msg": "x"})

        asyncio.create_task(submit_later())
        evt = await ex.get_next_event()  # no timeout → blocks
        assert evt is not None


class TestRunToolExceptionWithCallbackBackground:
    async def test_exception_background_fires_on_complete(self, monkeypatch):
        """In background mode (is_direct=False), exception in run_tool
        fires the on_complete callback (line 401)."""
        called = []

        ex = Executor(on_complete=lambda e: called.append(e))
        ex.register_tool(_FailTool())
        from kohakuterrarium.core import executor as ex_mod

        def explode(*a, **k):
            raise RuntimeError("normalize boom")

        monkeypatch.setattr(ex_mod, "normalize_tool_output", explode)
        jid = await ex.submit("fail", {}, is_direct=False)
        await ex.wait_for(jid)
        # Callback fired for the failed job.
        assert called


class TestCancelledToolWithCallback:
    async def test_cancelled_background_fires_on_complete(self):
        """A cancelled background tool fires the on_complete callback
        (line 375)."""
        called = []

        ex = Executor(on_complete=lambda e: called.append(e))
        ex.register_tool(_SlowTool())
        jid = await ex.submit("slow", {"seconds": 5.0}, is_direct=False)
        await asyncio.sleep(0.001)
        await ex.cancel(jid)
        await ex.wait_for(jid, timeout=1.0)
        # Callback received the cancellation event.
        assert called


class TestWaitForDeterministicTimeout:
    async def test_timeout_returns_none(self):
        ex = Executor()

        async def forever():
            await asyncio.sleep(60)
            return JobResult(job_id="x")

        ex._tasks["x"] = asyncio.create_task(forever())
        # Forced timeout returns None.
        out = await ex.wait_for("x", timeout=0.005)
        assert out is None
        ex._tasks["x"].cancel()


class TestToolContextBuild:
    async def test_context_passed_to_needs_context_tool(self):
        captured: dict[str, Any] = {}

        class _NeedsCtx(BaseTool):
            needs_context = True

            @property
            def tool_name(self):
                return "needs"

            @property
            def description(self):
                return "needs"

            async def _execute(self, args, context=None, **kwargs):
                captured["ctx"] = context
                captured["agent_name"] = context.agent_name
                captured["creature_id"] = context.creature_id
                return ToolResult(output="ok")

        ex = Executor()
        ex.register_tool(_NeedsCtx())
        ex._agent_name = "alice"
        ex._creature_id = "creature-alice"
        ex._working_dir = Path.cwd()
        jid = await ex.submit("needs", {})
        await ex.wait_for(jid)
        assert captured["agent_name"] == "alice"
        assert captured["creature_id"] == "creature-alice"

    async def test_emit_tool_wait_through_router(self):
        ex = Executor()
        ex.register_tool(_UnsafeTool(hold_seconds=0.03))
        notes: list[tuple] = []

        class _Router:
            def notify_activity(self, kind, msg, metadata=None):
                notes.append((kind, msg, metadata))

        ex._agent = types.SimpleNamespace(output_router=_Router())
        # Two concurrent unsafe calls — second one waits on the serial lock.
        await asyncio.gather(
            ex.submit("unsafe", {}),
            ex.submit("unsafe", {}),
        )
        await ex.wait_all(timeout=5.0)
        assert any(n[0] == "tool_wait" for n in notes)

    async def test_runtime_services_pulled_from_plugins(self):
        captured = {}

        class _NeedsCtx(BaseTool):
            needs_context = True

            @property
            def tool_name(self):
                return "needs"

            @property
            def description(self):
                return "needs"

            async def _execute(self, args, context=None, **kwargs):
                captured["services"] = dict(context.runtime_services)
                return ToolResult(output="ok")

        class _FakePlugins:
            def collect_runtime_services(self, ctx):
                return {"db": "sqlite-conn"}

            def wrap_method(self, *a, **k):
                # No-op wrapper; the actual exec_fn is passed through.
                return a[2]

        ex = Executor()
        ex.register_tool(_NeedsCtx())
        ex._agent = types.SimpleNamespace(plugins=_FakePlugins())
        jid = await ex.submit("needs", {})
        await ex.wait_for(jid)
        assert captured["services"] == {"db": "sqlite-conn"}
