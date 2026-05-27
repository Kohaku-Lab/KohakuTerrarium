"""Unit tests for :mod:`kohakuterrarium.core.agent_tools`."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kohakuterrarium.core.agent_tools import AgentToolsMixin, _TurnResult
from kohakuterrarium.core.backgroundify import BackgroundifyHandle, PromotionResult
from kohakuterrarium.core.conversation import Conversation
from kohakuterrarium.core.job import JobResult
from kohakuterrarium.parsing import ToolCallEvent

# ── fake agent harness ───────────────────────────────────────────


class _Router:
    def __init__(self):
        self.activity_calls: list[tuple] = []

    def notify_activity(self, kind, msg, metadata=None):
        self.activity_calls.append((kind, msg, metadata))


class _Controller:
    def __init__(self):
        self.conversation = Conversation()
        self.conversation.append("system", "sys")


class _SubAgentJob:
    def __init__(self):
        self.subagent = MagicMock()
        self.subagent.cancel = MagicMock()


class _SubAgentManager:
    def __init__(self):
        self._jobs: dict[str, _SubAgentJob] = {}
        self._results: dict[str, Any] = {}

    def get_result(self, job_id):
        return self._results.get(job_id)


class _FakeExecutor:
    def __init__(self):
        self._results: dict[str, Any] = {}

    def get_result(self, jid):
        return self._results.get(jid)


class _FakeAgent(AgentToolsMixin):
    def __init__(self):
        self.output_router = _Router()
        self.subagent_manager = _SubAgentManager()
        self.executor = _FakeExecutor()
        self.controller = _Controller()
        self._direct_job_meta = {}
        self._active_handles = {}
        self._bg_controller_notify = {}
        self._termination_checker = None
        self._running = True
        self._processed: list = []

    async def _process_event(self, evt):
        self._processed.append(evt)


@pytest.fixture
def agent():
    return _FakeAgent()


# ── _register_direct_job / _clear_direct_job_tracking ────────────


class TestRegisterAndClear:
    def test_register_records_metadata(self, agent):
        agent._register_direct_job(
            "bash_1", kind="tool", name="bash", tool_call_id="call_1"
        )
        meta = agent._direct_job_meta["bash_1"]
        assert meta["kind"] == "tool"
        assert meta["name"] == "bash"
        assert meta["tool_call_id"] == "call_1"
        assert meta["background"] is False
        assert meta["interruptible"] is True
        assert "started_at" in meta

    def test_clear_removes_all_traces(self, agent):
        agent._register_direct_job("x", kind="tool", name="t")
        agent._active_handles["x"] = MagicMock()
        agent._bg_controller_notify["x"] = True
        agent._clear_direct_job_tracking("x")
        assert "x" not in agent._direct_job_meta
        assert "x" not in agent._active_handles
        assert "x" not in agent._bg_controller_notify


# ── _interrupt_direct_job ────────────────────────────────────────


class TestInterruptDirectJob:
    def test_no_meta_returns_false(self, agent):
        assert agent._interrupt_direct_job("ghost") is False

    def test_promoted_returns_false(self, agent):
        agent._register_direct_job("x", kind="tool", name="t")
        h = MagicMock(spec=BackgroundifyHandle)
        h.promoted = True
        h.done = False
        agent._active_handles["x"] = h
        assert agent._interrupt_direct_job("x") is False

    def test_done_returns_false(self, agent):
        agent._register_direct_job("x", kind="tool", name="t")
        h = MagicMock(spec=BackgroundifyHandle)
        h.promoted = False
        h.done = True
        agent._active_handles["x"] = h
        assert agent._interrupt_direct_job("x") is False

    async def test_active_tool_cancelled(self, agent):
        agent._register_direct_job("x", kind="tool", name="t")
        h = MagicMock(spec=BackgroundifyHandle)
        h.promoted = False
        h.done = False
        h.task = MagicMock()
        agent._active_handles["x"] = h
        ok = agent._interrupt_direct_job("x")
        assert ok is True
        h.task.cancel.assert_called_once()
        # Let the finalize task run.
        for _ in range(5):
            await asyncio.sleep(0)

    async def test_subagent_cancellation_also_cancels_subagent(self, agent):
        agent._register_direct_job("agent_x", kind="subagent", name="explore")
        h = MagicMock(spec=BackgroundifyHandle)
        h.promoted = False
        h.done = False
        h.task = MagicMock()
        agent._active_handles["agent_x"] = h
        job = _SubAgentJob()
        agent.subagent_manager._jobs["agent_x"] = job
        ok = agent._interrupt_direct_job("agent_x")
        assert ok is True
        job.subagent.cancel.assert_called_once()


# ── _emit_direct_completion_activity ─────────────────────────────


class TestEmitDirectCompletion:
    def test_tool_ok(self, agent):
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        result = JobResult(job_id="bash_x", output="hello", exit_code=0)
        agent._emit_direct_completion_activity("bash_x", result)
        kinds = [c[0] for c in agent.output_router.activity_calls]
        assert "tool_done" in kinds

    def test_tool_error(self, agent):
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        result = JobResult(job_id="bash_x", error="boom", output="")
        agent._emit_direct_completion_activity("bash_x", result)
        kinds = [c[0] for c in agent.output_router.activity_calls]
        assert "tool_error" in kinds

    def test_exception_result(self, agent):
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        agent._emit_direct_completion_activity("bash_x", RuntimeError("oops"))
        kinds = [c[0] for c in agent.output_router.activity_calls]
        assert "tool_error" in kinds
        # final_state on metadata is 'error' for plain exceptions.
        meta = next(
            c[2] for c in agent.output_router.activity_calls if c[0] == "tool_error"
        )
        assert meta["final_state"] == "error"

    def test_cancelled_via_jobresult(self, agent):
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        result = JobResult(
            job_id="bash_x",
            error="cancelled",
            output="",
            metadata={"interrupted": True, "final_state": "interrupted"},
        )
        # interrupted flag stored on the JobResult dataclass via direct setattr.
        object.__setattr__(result, "interrupted", True)
        agent._emit_direct_completion_activity("bash_x", result)
        meta = next(
            c[2] for c in agent.output_router.activity_calls if c[0] == "tool_error"
        )
        assert meta["interrupted"] is True

    def test_subagent_ok(self, agent):
        agent._register_direct_job("agent_x", kind="subagent", name="explore")
        result = MagicMock()
        result.output = "ok output"
        result.error = None
        result.exit_code = 0
        result.turns = 3
        result.duration = 1.5
        result.total_tokens = 100
        result.prompt_tokens = 60
        result.completion_tokens = 40
        result.cached_tokens = 20
        result.metadata = {"tools_used": ["bash"]}
        result.get_text_output = lambda: "ok output"
        agent._emit_direct_completion_activity("agent_x", result)
        kinds = [c[0] for c in agent.output_router.activity_calls]
        assert "subagent_done" in kinds


# ── _emit_interrupted_activity ───────────────────────────────────


class TestEmitInterruptedActivity:
    def test_tool_interrupt(self, agent):
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        result = JobResult(job_id="bash_x", error="User manually interrupted this job.")
        agent._emit_interrupted_activity("bash_x", result)
        kinds = [c[0] for c in agent.output_router.activity_calls]
        assert "tool_error" in kinds
        meta = next(
            c[2] for c in agent.output_router.activity_calls if c[0] == "tool_error"
        )
        assert meta["interrupted"] is True

    def test_subagent_interrupt(self, agent):
        agent._register_direct_job("agent_x", kind="subagent", name="explore")
        result = MagicMock()
        result.error = "interrupted"
        result.output = ""
        result.turns = 1
        result.duration = 0.5
        result.total_tokens = 0
        result.prompt_tokens = 0
        result.completion_tokens = 0
        result.cached_tokens = 0
        result.metadata = {}
        agent._emit_interrupted_activity("agent_x", result)
        kinds = [c[0] for c in agent.output_router.activity_calls]
        assert "subagent_error" in kinds


# ── _add_native_results_to_conversation ──────────────────────────


class TestAddNativeResults:
    def test_ok_result_appended(self, agent):
        result = JobResult(job_id="bash_x", output="hello", exit_code=0)
        agent._add_native_results_to_conversation(
            agent.controller,
            ["bash_x"],
            {"bash_x": result},
            {"bash_x": "call_1"},
        )
        last = agent.controller.conversation.get_messages()[-1]
        assert last.role == "tool"
        assert last.tool_call_id == "call_1"
        assert "hello" in last.content

    def test_error_result_prefixed(self, agent):
        result = JobResult(job_id="bash_x", error="boom", output="")
        agent._add_native_results_to_conversation(
            agent.controller,
            ["bash_x"],
            {"bash_x": result},
            {"bash_x": "call_1"},
        )
        last = agent.controller.conversation.get_messages()[-1]
        assert "Error" in last.content
        assert "boom" in last.content

    def test_exception_result(self, agent):
        agent._add_native_results_to_conversation(
            agent.controller,
            ["bash_x"],
            {"bash_x": RuntimeError("crash")},
            {"bash_x": "call_1"},
        )
        last = agent.controller.conversation.get_messages()[-1]
        assert "Error" in last.content
        assert "crash" in last.content

    def test_interrupted_jobresult_prefixed(self, agent):
        result = JobResult(job_id="bash_x", error="cancelled", output="")
        object.__setattr__(result, "interrupted", True)
        agent._add_native_results_to_conversation(
            agent.controller,
            ["bash_x"],
            {"bash_x": result},
            {"bash_x": "call_1"},
        )
        last = agent.controller.conversation.get_messages()[-1]
        assert "Interrupted" in last.content

    def test_promoted_jobs_skipped(self, agent):
        # No result entry for "x" → loop skips.
        before = len(agent.controller.conversation.get_messages())
        agent._add_native_results_to_conversation(
            agent.controller,
            ["x"],
            {},  # promoted
            {},
        )
        after = len(agent.controller.conversation.get_messages())
        assert after == before


# ── _format_text_results ─────────────────────────────────────────


class TestFormatTextResults:
    def test_ok_result(self, agent):
        result = JobResult(job_id="bash_x", output="hello", exit_code=0)
        out = agent._format_text_results(["bash_x"], {"bash_x": result})
        assert "bash_x" in out
        assert "hello" in out
        assert "OK" in out

    def test_error_result(self, agent):
        result = JobResult(job_id="bash_x", error="boom")
        out = agent._format_text_results(["bash_x"], {"bash_x": result})
        assert "ERROR" in out
        assert "boom" in out

    def test_exception_result(self, agent):
        out = agent._format_text_results(["bash_x"], {"bash_x": RuntimeError("oops")})
        assert "FAILED" in out
        assert "oops" in out

    def test_promoted_skipped(self, agent):
        out = agent._format_text_results(["x"], {})
        assert out == ""

    def test_interrupted_jobresult_state(self, agent):
        result = JobResult(job_id="x", error="cancelled", output="")
        object.__setattr__(result, "interrupted", True)
        out = agent._format_text_results(["x"], {"x": result})
        assert "INTERRUPTED" in out

    def test_nonzero_exit_code(self, agent):
        result = JobResult(job_id="x", output="bad", exit_code=2)
        out = agent._format_text_results(["x"], {"x": result})
        assert "exit=2" in out


# ── _handle_promotion ────────────────────────────────────────────


class TestHandlePromotion:
    def test_native_mode_appends_placeholder(self, agent):
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        agent._handle_promotion(
            "bash_x",
            agent.controller,
            tool_call_ids={"bash_x": "call_1"},
            native_mode=True,
        )
        last = agent.controller.conversation.get_messages()[-1]
        assert last.role == "tool"
        assert "Promoted to background" in last.content

    def test_non_native_no_append(self, agent):
        before = len(agent.controller.conversation.get_messages())
        agent._handle_promotion(
            "bash_x",
            agent.controller,
            tool_call_ids={},
            native_mode=False,
        )
        # No tool message appended.
        after_messages = agent.controller.conversation.get_messages()
        # The activity is emitted but no conversation append.
        assert all(m.role != "tool" for m in after_messages[before:])

    def test_meta_marked_background(self, agent):
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        agent._handle_promotion(
            "bash_x",
            agent.controller,
            tool_call_ids={},
            native_mode=False,
        )
        assert agent._direct_job_meta["bash_x"]["background"] is True
        assert agent._direct_job_meta["bash_x"]["interruptible"] is False


# ── _wait_handles ────────────────────────────────────────────────


class TestWaitHandles:
    async def test_empty_returns_empty(self, agent):
        results, promotions = await agent._wait_handles(
            {}, [], agent.controller, {}, False
        )
        assert results == {}
        assert promotions is False

    async def test_completes_normally(self, agent):
        async def coro():
            return JobResult(job_id="x", output="hi")

        task = asyncio.create_task(coro())
        handle = BackgroundifyHandle(job_id="x", task=task)
        await asyncio.sleep(0.01)
        agent._register_direct_job("x", kind="tool", name="t")
        results, _ = await agent._wait_handles(
            {"x": handle}, ["x"], agent.controller, {}, False
        )
        assert "x" in results

    async def test_promotion_recorded(self, agent):
        async def coro():
            return PromotionResult(job_id="x")

        task = asyncio.create_task(coro())
        handle = BackgroundifyHandle(job_id="x", task=task)
        await asyncio.sleep(0.01)
        agent._register_direct_job("x", kind="tool", name="t")
        results, promotions = await agent._wait_handles(
            {"x": handle},
            ["x"],
            agent.controller,
            {"x": "call_1"},
            True,
        )
        assert promotions is True
        # Result not in results dict (promoted goes to placeholder).
        assert "x" not in results


# ── _on_backgroundify_complete ───────────────────────────────────


class TestOnBackgroundifyComplete:
    async def test_jobresult(self, agent):
        result = JobResult(job_id="x", output="ok", exit_code=0)
        await agent._on_backgroundify_complete("x", result)
        await asyncio.sleep(0.01)
        # Routed through _on_bg_complete → _process_event (notify is on
        # by default) → a tool-complete event lands in the queue.
        assert len(agent._processed) == 1
        assert agent._processed[0].job_id == "x"
        assert agent._processed[0].content == "ok"

    async def test_exception(self, agent):
        await agent._on_backgroundify_complete("x", RuntimeError("boom"))
        await asyncio.sleep(0.01)
        # A raw exception result still surfaces a tool-complete event
        # carrying the error string in context.
        assert len(agent._processed) == 1
        assert "boom" in agent._processed[0].context.get("error", "")

    async def test_cancelled_exception(self, agent):
        # Regression test for B-at-1 (fixed): asyncio.CancelledError is a
        # BaseException (not Exception) since Python 3.8; the handler now
        # checks for it explicitly before the generic exception arm, so a
        # cancelled background task is reported as an interrupt.
        await agent._on_backgroundify_complete("x", asyncio.CancelledError())
        await asyncio.sleep(0.01)
        assert len(agent._processed) == 1
        evt = agent._processed[0]
        assert evt.context.get("interrupted") is True
        assert evt.context.get("final_state") == "interrupted"
        assert evt.context.get("error") == "User manually interrupted this job."

    async def test_non_cancelled_exception_is_plain_error(self, agent):
        # The contrast case: a real exception (still a BaseException, but
        # NOT a CancelledError) is reported as a plain error — NOT marked
        # interrupted. This pins the boundary the B-at-1 fix introduced.
        await agent._on_backgroundify_complete("y", RuntimeError("boom"))
        await asyncio.sleep(0.01)
        assert len(agent._processed) == 1
        evt = agent._processed[0]
        assert "boom" in evt.context.get("error", "")
        assert evt.context.get("interrupted") is not True

    async def test_subagent_result_with_metadata(self, agent):
        result = MagicMock()
        result.output = "ok"
        result.error = None
        result.exit_code = 0
        result.turns = 3
        result.duration = 1.5
        result.total_tokens = 100
        result.prompt_tokens = 60
        result.completion_tokens = 40
        result.cached_tokens = 20
        result.interrupted = False
        result.cancelled = False
        result.metadata = {"tools_used": ["bash"]}
        await agent._on_backgroundify_complete("agent_x", result)
        await asyncio.sleep(0.01)
        # Sub-agent completion surfaces an event with the run metadata
        # threaded into context.
        assert len(agent._processed) == 1
        sa = agent._processed[0].context["subagent_metadata"]
        assert sa["turns"] == 3
        assert sa["tools_used"] == ["bash"]


# ── _TurnResult dataclass ────────────────────────────────────────


class TestTurnResult:
    def test_default_factory_lists_independent(self):
        a = _TurnResult()
        b = _TurnResult()
        a.text_output.append("x")
        assert b.text_output == []


# ── _finalize_interrupted_direct_job ─────────────────────────────


class TestFinalizeInterruptedDirectJob:
    async def test_no_handle_returns(self, agent):
        # Not registered → returns silently.
        await agent._finalize_interrupted_direct_job("ghost")

    async def test_tool_finalisation(self, agent):
        agent._register_direct_job("bash_x", kind="tool", name="bash")

        async def runner():
            await asyncio.sleep(0.5)
            return JobResult(job_id="bash_x", output="late")

        task = asyncio.create_task(runner())
        h = BackgroundifyHandle(job_id="bash_x", task=task)
        agent._active_handles["bash_x"] = h
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=0.2)
        except (asyncio.CancelledError, Exception):
            pass
        await agent._finalize_interrupted_direct_job("bash_x")
        # Activity emitted.
        kinds = [c[0] for c in agent.output_router.activity_calls]
        assert "tool_error" in kinds

    async def test_subagent_finalisation_falls_back_to_manager_result(self, agent):
        agent._register_direct_job("agent_x", kind="subagent", name="explore")
        sa_result = MagicMock()
        sa_result.output = ""
        sa_result.error = "stopped"
        sa_result.turns = 0
        sa_result.duration = 0
        sa_result.total_tokens = 0
        sa_result.prompt_tokens = 0
        sa_result.completion_tokens = 0
        sa_result.cached_tokens = 0
        sa_result.metadata = {}
        agent.subagent_manager._results["agent_x"] = sa_result

        async def runner():
            raise asyncio.CancelledError()

        task = asyncio.create_task(runner())
        h = BackgroundifyHandle(job_id="agent_x", task=task)
        agent._active_handles["agent_x"] = h
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await agent._finalize_interrupted_direct_job("agent_x")
        kinds = [c[0] for c in agent.output_router.activity_calls]
        assert "subagent_error" in kinds

    async def test_appends_role_tool_message_so_llm_apis_dont_400(self, agent):
        # Bug 2 regression: when ``agent.interrupt()`` cancels the
        # controller task mid-tool, the controller loop dies BEFORE
        # ``_add_native_results_to_conversation`` runs. Without a
        # synthesised ``role=tool`` message paired to the assistant
        # turn's ``tool_calls``, the next LLM request ships orphan
        # tool_calls and most providers (OpenAI / Anthropic-compat)
        # 400 the call. ``_finalize_interrupted_direct_job`` MUST
        # therefore append the matching tool message itself.
        agent.controller.conversation.append(
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "call_user_42",
                    "type": "function",
                    "function": {"name": "ask_user", "arguments": "{}"},
                }
            ],
        )
        agent._register_direct_job(
            "ask_user_42", kind="tool", name="ask_user", tool_call_id="call_user_42"
        )

        async def runner():
            raise asyncio.CancelledError()

        task = asyncio.create_task(runner())
        h = BackgroundifyHandle(job_id="ask_user_42", task=task)
        agent._active_handles["ask_user_42"] = h
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await agent._finalize_interrupted_direct_job("ask_user_42")

        msgs = agent.controller.conversation.get_messages()
        tool_msg = next(
            (m for m in msgs if getattr(m, "role", None) == "tool"),
            None,
        )
        assert tool_msg is not None, "interrupt path must emit a paired tool message"
        assert tool_msg.tool_call_id == "call_user_42"
        # Content must mention the interruption so the LLM understands.
        assert (
            "Interrupted" in tool_msg.content or "interrupt" in tool_msg.content.lower()
        )

    async def test_synthetic_tool_message_is_idempotent(self, agent):
        # Defensive: if the controller loop happened to append the
        # tool message before the interrupt-finalize task ran, we
        # must NOT append a duplicate (the next LLM call would have
        # two tool messages for one tool_call id, which most providers
        # also reject).
        agent.controller.conversation.append(
            "assistant",
            "",
            tool_calls=[
                {
                    "id": "call_double",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{}"},
                }
            ],
        )
        agent.controller.conversation.append(
            "tool",
            "already-here",
            tool_call_id="call_double",
            name="bash",
        )
        agent._register_direct_job(
            "bash_double", kind="tool", name="bash", tool_call_id="call_double"
        )

        async def runner():
            raise asyncio.CancelledError()

        task = asyncio.create_task(runner())
        h = BackgroundifyHandle(job_id="bash_double", task=task)
        agent._active_handles["bash_double"] = h
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        await agent._finalize_interrupted_direct_job("bash_double")

        msgs = agent.controller.conversation.get_messages()
        tool_msgs = [m for m in msgs if getattr(m, "role", None) == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0].content == "already-here"


# ── _on_backgroundify_complete subagent metadata ─────────────────


class TestOnBackgroundifyCompleteEdges:
    async def test_subagent_metadata_attached_to_event(self, agent):
        captured = []

        async def proc(evt):
            captured.append(evt)

        agent._process_event = proc

        result = MagicMock()
        result.output = "ok"
        result.error = None
        result.exit_code = 0
        result.turns = 2
        result.duration = 0.7
        result.total_tokens = 50
        result.prompt_tokens = 30
        result.completion_tokens = 20
        result.cached_tokens = 5
        result.interrupted = False
        result.cancelled = False
        result.metadata = {"tools_used": ["read"]}
        await agent._on_backgroundify_complete("agent_x", result)
        await asyncio.sleep(0.01)
        assert captured
        evt = captured[0]
        assert "subagent_metadata" in evt.context
        sa = evt.context["subagent_metadata"]
        assert sa["turns"] == 2
        assert sa["tools_used"] == ["read"]

    async def test_jobresult_interrupted_flag(self, agent):
        captured = []

        async def proc(evt):
            captured.append(evt)

        agent._process_event = proc

        result = JobResult(job_id="x", output="", error="boom")
        object.__setattr__(result, "interrupted", True)
        await agent._on_backgroundify_complete("x", result)
        await asyncio.sleep(0.01)
        # Event built with interrupted in context.
        assert captured
        assert captured[0].context.get("interrupted") is True
        assert captured[0].context.get("final_state") == "interrupted"

    async def test_jobresult_cancelled_flag(self, agent):
        captured = []

        async def proc(evt):
            captured.append(evt)

        agent._process_event = proc

        result = JobResult(job_id="x", output="ok")
        object.__setattr__(result, "cancelled", True)
        await agent._on_backgroundify_complete("x", result)
        await asyncio.sleep(0.01)
        assert captured
        assert captured[0].context.get("cancelled") is True

    async def test_truthy_non_object_result(self, agent):
        await agent._on_backgroundify_complete("x", "plain string")
        await asyncio.sleep(0.01)

    async def test_falsy_result(self, agent):
        await agent._on_backgroundify_complete("x", None)
        await asyncio.sleep(0.01)


# ── exception-typed handles via JobResult-with-error path ─────────


class TestEmitDirectCompletionSubagentError:
    def test_subagent_error_metadata_includes_existing_result(self, agent):
        agent._register_direct_job("agent_x", kind="subagent", name="explore")
        existing = MagicMock()
        existing.turns = 5
        existing.duration = 3.0
        existing.total_tokens = 100
        existing.prompt_tokens = 60
        existing.completion_tokens = 40
        existing.cached_tokens = 10
        existing.metadata = {"tools_used": ["bash"]}
        agent.subagent_manager._results["agent_x"] = existing
        # Result is a plain exception.
        agent._emit_direct_completion_activity("agent_x", RuntimeError("oops"))
        meta = next(
            c[2] for c in agent.output_router.activity_calls if c[0] == "subagent_error"
        )
        assert meta["turns"] == 5
        assert meta["tools_used"] == ["bash"]


# ── _start_tool_async ────────────────────────────────────────────


class TestStartToolAsync:
    async def test_basic_start(self, agent):
        # Wire a real executor + tool.
        from kohakuterrarium.core.executor import Executor
        from kohakuterrarium.modules.tool.base import (
            BaseTool,
            ExecutionMode,
            ToolResult,
        )

        class _Echo(BaseTool):
            @property
            def tool_name(self):
                return "echo"

            @property
            def description(self):
                return "echo"

            @property
            def execution_mode(self):
                return ExecutionMode.DIRECT

            async def _execute(self, args, **kwargs):
                return ToolResult(output=str(args.get("msg", "")))

        agent.executor = Executor()
        agent.executor.register_tool(_Echo())
        evt = ToolCallEvent(name="echo", args={"msg": "hi"}, raw="")
        job_id, task, is_direct = await agent._start_tool_async(evt)
        assert job_id.startswith("echo_")
        assert is_direct is True
        await task

    async def test_unknown_tool_returns_error_job(self, agent):
        from kohakuterrarium.core.executor import Executor

        agent.executor = Executor()
        evt = ToolCallEvent(name="ghost", args={}, raw="")
        job_id, task, is_direct = await agent._start_tool_async(evt)
        assert job_id == "error_ghost"
        assert is_direct is True
        result = await task
        assert result.error

    async def test_start_tool_fallback_when_executor_task_missing(self, agent):
        """When ``executor.get_task`` returns None right after submit,
        the helper wraps a synthetic coro that yields the cached
        result."""
        from kohakuterrarium.core.executor import Executor
        from kohakuterrarium.modules.tool.base import (
            BaseTool,
            ExecutionMode,
            ToolResult,
        )

        class _Echo(BaseTool):
            @property
            def tool_name(self):
                return "echo"

            @property
            def description(self):
                return "echo"

            @property
            def execution_mode(self):
                return ExecutionMode.DIRECT

            async def _execute(self, args, **kwargs):
                return ToolResult(output="ok")

        ex = Executor()
        ex.register_tool(_Echo())
        ex._results["pre"] = JobResult(job_id="pre", output="cached")

        # Stub submit_from_event to return our pre-cached job id, with
        # no Task entry in ``_tasks`` — exercising the synthetic-task path.

        async def _fake_submit(event, is_direct=False):
            return "pre"

        ex.submit_from_event = _fake_submit
        agent.executor = ex
        evt = ToolCallEvent(name="echo", args={}, raw="")
        job_id, task, is_direct = await agent._start_tool_async(evt)
        result = await task
        assert result.output == "cached"


# ── _handle_promotion plugin notify ──────────────────────────────


class TestHandlePromotionPlugins:
    async def test_plugin_notify_fires(self, agent):
        mgr = MagicMock()
        mgr.notify = AsyncMock()
        agent.plugins = mgr
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        agent._handle_promotion("bash_x", agent.controller, {}, False)
        # asyncio.create_task scheduled the plugin notify; give it a tick.
        await asyncio.sleep(0)
        mgr.notify.assert_called()

    def test_no_plugins_no_op(self, agent):
        # ``plugins`` attribute missing means the notify branch is skipped.
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        agent._handle_promotion("bash_x", agent.controller, {}, False)


# ── _emit_direct_completion_activity edge cases ──────────────────


class TestEmitDirectCompletionRecordTermination:
    def test_termination_checker_records_tool_result(self, agent):
        from kohakuterrarium.core.termination import (
            TerminationChecker,
            TerminationConfig,
        )

        agent._termination_checker = TerminationChecker(TerminationConfig(max_turns=10))
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        result = JobResult(job_id="bash_x", output="ok", exit_code=0)
        agent._emit_direct_completion_activity("bash_x", result)
        # Checker has the result recorded.
        assert agent._termination_checker._recent_tool_results

    def test_checker_record_failure_swallowed(self, agent):
        class _BadChecker:
            def record_tool_result(self, r):
                raise RuntimeError("boom")

        agent._termination_checker = _BadChecker()
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        result = JobResult(job_id="bash_x", output="ok", exit_code=0)
        # Must not raise — exception swallowed.
        agent._emit_direct_completion_activity("bash_x", result)


# ── _format_text_results result with cancelled flag ──────────────


class TestFormatTextCancelled:
    def test_cancelled_jobresult_state(self, agent):
        result = JobResult(job_id="x", error="stopped", output="")
        object.__setattr__(result, "cancelled", True)
        out = agent._format_text_results(["x"], {"x": result})
        assert "CANCELLED" in out


class TestAddNativeResultsCancelled:
    def test_cancelled_jobresult_prefix(self, agent):
        result = JobResult(job_id="bash_x", error="stopped", output="")
        object.__setattr__(result, "cancelled", True)
        agent._add_native_results_to_conversation(
            agent.controller,
            ["bash_x"],
            {"bash_x": result},
            {"bash_x": "call_1"},
        )
        last = agent.controller.conversation.get_messages()[-1]
        assert "Cancelled" in last.content

    def test_error_with_output_appends_both(self, agent):
        """When result has both error AND output, content includes
        the output appended after the error (line 523)."""
        result = JobResult(
            job_id="bash_x", error="something went wrong", output="partial output"
        )
        agent._add_native_results_to_conversation(
            agent.controller,
            ["bash_x"],
            {"bash_x": result},
            {"bash_x": "call_1"},
        )
        last = agent.controller.conversation.get_messages()[-1]
        assert "something went wrong" in last.content
        assert "partial output" in last.content

    def test_none_result_appends_empty(self, agent):
        """A None result yields an empty tool message (line 528)."""
        agent._add_native_results_to_conversation(
            agent.controller,
            ["bash_x"],
            {"bash_x": None},
            {"bash_x": "call_1"},
        )
        last = agent.controller.conversation.get_messages()[-1]
        assert last.content == ""


class TestWaitHandlesTaskException:
    async def test_task_raises_exception(self, agent, monkeypatch):
        """When ``future.result()`` raises an exception, _wait_handles
        captures it (lines 118-119). We force this by creating a handle
        whose wait() raises.
        """

        # Build a handle whose wait coroutine raises.
        class _FailHandle:
            def __init__(self):
                self.task = MagicMock()

            async def wait(self):
                raise RuntimeError("from wait")

        agent._register_direct_job("x", kind="tool", name="t")
        h = _FailHandle()
        results, _ = await agent._wait_handles(
            {"x": h}, ["x"], agent.controller, {}, False
        )
        # The exception was caught and stored.
        assert isinstance(results["x"], Exception)


class TestOnBackgroundifyCompleteEventContextInit:
    async def test_event_context_initialized_when_none(self, agent):
        """A result with sub-agent metadata but event.context=None triggers
        the ``event.context = {}`` initialization (line 323)."""
        captured = []

        async def proc(evt):
            captured.append(evt)

        agent._process_event = proc

        # Build a SubAgent-like result with turns.
        from unittest.mock import MagicMock

        result = MagicMock()
        result.output = "ok"
        result.error = None
        result.exit_code = 0
        result.turns = 1
        result.duration = 0.5
        result.total_tokens = 10
        result.prompt_tokens = 6
        result.completion_tokens = 4
        result.cached_tokens = 0
        result.interrupted = False
        result.cancelled = False
        result.metadata = {"tools_used": []}

        # Patch create_tool_complete_event to return an event with
        # context=None so line 323 fires.
        from kohakuterrarium.core import agent_tools as at
        from kohakuterrarium.core.events import TriggerEvent, EventType

        def make_evt(**kwargs):
            return TriggerEvent(
                type=EventType.TOOL_COMPLETE,
                content=kwargs.get("content", ""),
                job_id=kwargs.get("job_id", ""),
                context=None,
            )

        # Replace within agent_tools module.
        original = at.create_tool_complete_event
        at.create_tool_complete_event = make_evt
        try:
            await agent._on_backgroundify_complete("agent_x", result)
            await asyncio.sleep(0.01)
        finally:
            at.create_tool_complete_event = original
        # Event was emitted with subagent metadata in context.
        assert captured
        assert "subagent_metadata" in captured[0].context


class TestEmitDirectCompletionSubagentErrorMetadata:
    def test_subagent_error_with_result_having_error_attribute(self, agent):
        """Lines 426-432: subagent-mode metadata fill when result.error
        is set (not a raw exception)."""
        agent._register_direct_job("agent_x", kind="subagent", name="explore")
        from unittest.mock import MagicMock

        result = MagicMock()
        result.error = "subagent failed"
        result.output = "partial"
        result.turns = 2
        result.duration = 1.0
        result.total_tokens = 50
        result.prompt_tokens = 30
        result.completion_tokens = 20
        result.cached_tokens = 5
        result.interrupted = False
        result.cancelled = False
        result.metadata = {"tools_used": ["bash"]}
        result.get_text_output = lambda: "partial"
        agent._emit_direct_completion_activity("agent_x", result)
        # subagent_error emitted with full metadata.
        kinds = [c[0] for c in agent.output_router.activity_calls]
        assert "subagent_error" in kinds
        meta = next(
            c[2] for c in agent.output_router.activity_calls if c[0] == "subagent_error"
        )
        assert meta["turns"] == 2
        assert meta["tools_used"] == ["bash"]
