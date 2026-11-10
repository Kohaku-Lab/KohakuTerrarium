"""Unit tests for :mod:`kohakuterrarium.core.agent.tools.dispatch`."""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from kohakuterrarium.core.agent.turn.event_loop import AgentEventLoopMixin
from kohakuterrarium.core.agent.tools.dispatch import AgentToolsMixin, _TurnResult
from kohakuterrarium.core.jobs.backgroundify import BackgroundifyHandle
from kohakuterrarium.core.conversation.conversation import Conversation
from kohakuterrarium.core.event_inbox import EventInbox
from kohakuterrarium.core.jobs.job import JobResult
from kohakuterrarium.llm.message import ImagePart
from kohakuterrarium.modules.plugin.base import BasePlugin
from kohakuterrarium.modules.plugin.manager.manager import PluginManager
from kohakuterrarium.parsing import ToolCallEvent
from tests.helpers.waiting import wait_until

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
        self.deferred = []

    def push_event_sync(self, event):
        self.deferred.append(event)


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


class _FakeAgent(AgentToolsMixin, AgentEventLoopMixin):
    def __init__(self):
        self.output_router = _Router()
        self.subagent_manager = _SubAgentManager()
        self.executor = _FakeExecutor()
        self.controller = _Controller()
        self._event_inbox = EventInbox()
        self._direct_job_meta = {}
        self._active_handles = {}
        self._bg_controller_notify = {}
        self._turn_cancelled_jobs = set()
        self._deferred_job_results = []
        self._termination_checker = None
        self._running = True


@pytest.fixture
def agent():
    return _FakeAgent()


# ── _register_direct_job / _clear_direct_job_tracking ────────────


@pytest.mark.parametrize("kind", ["tool", "subagent"])
def test_cancelled_result_has_consistent_activity_text_and_native_feedback(agent, kind):
    agent._register_direct_job(
        "owned_1", kind=kind, name="owned", tool_call_id="call_1"
    )
    result = asyncio.CancelledError()
    agent._emit_direct_completion_activity("owned_1", result)
    activity, detail, metadata = agent.output_router.activity_calls[-1]
    assert activity == ("tool_error" if kind == "tool" else "subagent_error")
    assert "INTERRUPTED" in detail and metadata["final_state"] == "interrupted"
    assert metadata["interrupted"] is True
    text = agent._format_text_results(["owned_1"], {"owned_1": result})
    assert "INTERRUPTED" in text and " - OK" not in text
    agent._add_native_results_to_conversation(
        agent.controller, ["owned_1"], {"owned_1": result}, {"owned_1": "call_1"}
    )
    saved = agent.controller.conversation.get_messages()[-1]
    assert saved.tool_call_id == "call_1"
    assert (
        saved.get_text_content() == "Interrupted: User manually interrupted this job."
    )


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

    def test_tool_done_carries_full_output_and_preview(self, agent):
        # The event log must keep the full tool output (recoverable via
        # search_memory) while UI rendering keeps using the bounded preview.
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        big = "x" * 12000
        result = JobResult(job_id="bash_x", output=big, exit_code=0)
        agent._emit_direct_completion_activity("bash_x", result)
        meta = next(
            c[2] for c in agent.output_router.activity_calls if c[0] == "tool_done"
        )
        assert meta["output"] == big
        assert meta["output_preview"] == big[:5000]
        assert len(meta["output_preview"]) == 5000

    def test_tool_done_preserves_structured_media_result(self, agent):
        agent._register_direct_job(
            "grok_image_gen_x", kind="tool", name="grok_image_gen"
        )
        parts = [ImagePart(url="/api/sessions/g/artifacts/generated_images/a.jpeg")]
        result = JobResult(job_id="grok_image_gen_x", output=parts, exit_code=0)

        agent._emit_direct_completion_activity("grok_image_gen_x", result)

        meta = next(
            c[2] for c in agent.output_router.activity_calls if c[0] == "tool_done"
        )
        assert meta["result"] == [part.to_dict() for part in parts]
        assert meta["output"] == parts

    def test_tool_done_only_forwards_explicit_session_metadata(self, agent):
        agent._register_direct_job("web_search_x", kind="tool", name="web_search")
        result = JobResult(
            job_id="web_search_x",
            output="answer",
            exit_code=0,
            metadata={
                "secret": "must-not-persist",
                "session_metadata": {
                    "backend": "deepseek",
                    "citation_status": "verified",
                },
            },
        )

        agent._emit_direct_completion_activity("web_search_x", result)

        meta = next(
            c[2] for c in agent.output_router.activity_calls if c[0] == "tool_done"
        )
        assert meta["tool_metadata"] == {
            "backend": "deepseek",
            "citation_status": "verified",
        }
        assert "must-not-persist" not in repr(meta)

    def test_tool_error(self, agent):
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        result = JobResult(job_id="bash_x", error="boom", output="")
        agent._emit_direct_completion_activity("bash_x", result)
        kinds = [c[0] for c in agent.output_router.activity_calls]
        assert "tool_error" in kinds

    def test_nonzero_exit_code_emits_tool_error(self, agent):
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        result = JobResult(job_id="bash_x", output="bad", exit_code=2)

        agent._emit_direct_completion_activity("bash_x", result)

        kind, _detail, metadata = agent.output_router.activity_calls[-1]
        assert kind == "tool_error"
        assert metadata["exit_code"] == 2

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
        result.metadata = {
            "tools_used": ["bash"],
            "subagent": "explore",
            "llm_name": "openai/worker",
            "model": "gpt-worker",
        }
        result.get_text_output = lambda: "ok output"
        agent._emit_direct_completion_activity("agent_x", result)
        kinds = [c[0] for c in agent.output_router.activity_calls]
        assert "subagent_done" in kinds
        meta = next(
            c[2] for c in agent.output_router.activity_calls if c[0] == "subagent_done"
        )
        assert meta["subagent"] == "explore"
        assert meta["llm_name"] == "openai/worker"
        assert meta["model"] == "gpt-worker"


# ── cancellation handoff ────────────────────────────────────────


class TestCancellationHandoff:
    @pytest.mark.parametrize(
        "notify,stopping", [(True, False), (False, False), (True, True)]
    )
    @pytest.mark.parametrize("native", [False, True])
    @pytest.mark.parametrize("outcome", ["success", "cancel", "error"])
    async def test_pending_then_late_result_preserves_new_round(
        self, agent, native, outcome, notify, stopping, monkeypatch
    ):
        metrics_seen = []
        monkeypatch.setattr(
            "kohakuterrarium.core.agent.tools.dispatch._emit_completion_metrics",
            lambda *args: metrics_seen.append(args),
        )
        conversation = agent.controller.conversation
        call = {
            "id": "same",
            "type": "function",
            "function": {"name": "bash", "arguments": "{}"},
        }
        conversation.append("assistant", "", tool_calls=[call] if native else None)
        release = asyncio.Event()
        entered = asyncio.Event()

        async def slow():
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await release.wait()
                if outcome == "cancel":
                    raise
                if outcome == "error":
                    raise ValueError("cleanup failed")
                return JobResult(job_id="old", output="actual late success")

        task = asyncio.create_task(slow())
        await entered.wait()
        agent._register_direct_job(
            "old",
            kind="tool",
            name="bash",
            tool_call_id="same" if native else None,
            notify_controller_on_background_complete=notify,
        )
        handle = BackgroundifyHandle(task, "old", agent._on_backgroundify_complete)
        agent._active_handles["old"] = handle
        agent._interrupt_requested = True
        assert agent._interrupt_direct_job("old")
        results, promoted = await agent._wait_handles(
            {"old": handle},
            ["old"],
            agent.controller,
            {"old": "same"} if native else {},
            native,
        )
        assert results == {} and promoted
        snapshot = conversation.snapshot_messages()
        tools = [m for m in snapshot if m["role"] == "tool"]
        if native:
            assert len(tools) == 1
            assert tools[0]["metadata"]["tool_result_phase"] == "pending"
            assert "outcome is not yet known" in tools[0]["content"]
        else:
            assert tools == []
        conversation.append("user", "next")
        conversation.append("assistant", "", tool_calls=[call] if native else None)
        if native:
            agent._add_native_results_to_conversation(
                agent.controller,
                ["new"],
                {"new": JobResult(job_id="new", output="new result")},
                {"new": "same"},
            )
        before = conversation.snapshot_messages()
        agent._running = not stopping
        release.set()
        await wait_until(lambda: "old" not in agent._active_handles)
        assert conversation.snapshot_messages() == before
        assert len(metrics_seen) == 1
        assert metrics_seen[0][:3] == (
            False,
            "bash",
            (
                "ok"
                if outcome == "success"
                else "interrupted" if outcome == "cancel" else "error"
            ),
        )
        assert metrics_seen[0][3] >= 0
        assert len(agent._deferred_job_results) == 1
        event, _origin = agent._deferred_job_results[0]
        assert event.job_id == "old"
        assert not len(agent._event_inbox)
        terminal = [
            c
            for c in agent.output_router.activity_calls
            if c[0] in {"tool_done", "tool_error"}
        ]
        assert len(terminal) == 1
        if outcome == "success":
            assert terminal[0][0] == "tool_done"
            assert event.content == "actual late success"
        else:
            assert terminal[0][0] == "tool_error"
            assert event.context["error"]
            assert bool(event.context.get("interrupted")) == (outcome == "cancel")

    async def test_completed_before_interrupt_keeps_waiter_owner(self, agent):
        task = asyncio.create_task(
            asyncio.sleep(
                0, result=JobResult(job_id="done", output="completed side effect")
            )
        )
        await task
        handle = BackgroundifyHandle(task, "done", agent._on_backgroundify_complete)
        agent._register_direct_job(
            "done", kind="tool", name="bash", tool_call_id="done_call"
        )
        agent._active_handles["done"] = handle
        agent._interrupt_requested = True
        assert agent._interrupt_direct_job("done")
        assert "done" in agent._active_handles
        assert "completed side effect" not in str(
            agent.controller.conversation.get_messages()
        )
        assert not agent.output_router.activity_calls
        results, _ = await agent._wait_handles(
            {"done": handle}, ["done"], agent.controller, {"done": "done_call"}, True
        )
        assert results["done"].output == "completed side effect"
        assert not agent._active_handles
        assert (
            len([e for e in agent.output_router.activity_calls if e[0] == "tool_done"])
            == 1
        )

    async def test_fast_completion_keeps_handoff_owner_before_waiter_runs(self, agent):
        task = asyncio.create_task(asyncio.Event().wait())
        await asyncio.sleep(0)
        handle = BackgroundifyHandle(task, "fast", agent._on_backgroundify_complete)
        agent._register_direct_job("fast", kind="tool", name="bash")
        agent._active_handles["fast"] = handle
        agent._interrupt_requested = True
        assert agent._interrupt_direct_job("fast")
        await wait_until(lambda: "fast" not in agent._active_handles)
        activities = len(agent.output_router.activity_calls)
        results, promoted = await agent._wait_handles(
            {"fast": handle}, ["fast"], agent.controller, {}, False
        )
        assert results == {} and promoted
        assert len(agent.output_router.activity_calls) == activities
        assert len(agent._deferred_job_results) == 1


# ── _add_native_results_to_conversation ──────────────────────────


class TestAddNativeResults:
    def test_reused_provider_id_keeps_both_round_results(self, agent):
        conversation = agent.controller.conversation
        call = {
            "id": "same",
            "type": "function",
            "function": {"name": "bash", "arguments": "{}"},
        }
        for job in ("bash_old", "bash_new"):
            conversation.append("assistant", "", tool_calls=[call])
            agent._add_native_results_to_conversation(
                agent.controller,
                [job],
                {job: JobResult(job_id=job, output=job)},
                {job: "same"},
            )
        messages = conversation.to_messages()
        assert [m["content"] for m in messages if m["role"] == "tool"] == [
            "bash_old",
            "bash_new",
        ]
        assert (
            len([m for m in conversation.snapshot_messages() if m["role"] == "tool"])
            == 2
        )
        agent._add_native_results_to_conversation(
            agent.controller,
            ["bash_new"],
            {"bash_new": JobResult(job_id="bash_new", output="duplicate")},
            {"bash_new": "same"},
        )
        assert len([m for m in conversation.get_messages() if m.role == "tool"]) == 2

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

    @pytest.mark.parametrize("notify", [True, False])
    async def test_promotion_recorded(self, agent, notify):
        release, admitted = asyncio.Event(), asyncio.Event()

        async def coro():
            await release.wait()
            return JobResult(job_id="x", output="finished")

        async def complete(job_id, result):
            await agent._on_backgroundify_complete(job_id, result)
            admitted.set()

        task = asyncio.create_task(coro())
        handle = BackgroundifyHandle(task, "x", on_bg_complete=complete)
        agent._active_handles["x"] = handle
        agent._bg_controller_notify["x"] = notify
        agent._register_direct_job("x", kind="tool", name="t")
        assert handle.promote()
        try:
            results, promotions = await agent._wait_handles(
                {"x": handle}, ["x"], agent.controller, {"x": "call_1"}, True
            )
            assert promotions is True
            assert results == {}
            assert agent._active_handles == {"x": handle}
            assert agent._direct_job_meta == {}
            assert agent._bg_controller_notify == {"x": notify}
            placeholder = agent.controller.conversation.get_messages()[-1]
            assert placeholder.tool_call_id == "call_1"
            assert placeholder.metadata == {
                "job_id": "x",
                "tool_result_phase": "pending",
            }
            release.set()
            await asyncio.wait_for(admitted.wait(), 2)
            queued = agent._event_inbox.drain_all()
            assert [(env.event.job_id, env.event.content) for env in queued] == (
                [("x", "finished")] if notify else []
            )
            assert agent._active_handles == {}
            assert agent._bg_controller_notify == {}
        finally:
            release.set()
            await task


# ── _on_backgroundify_complete ───────────────────────────────────


class TestOnBackgroundifyComplete:
    async def test_jobresult(self, agent):
        result = JobResult(job_id="x", output="ok", exit_code=0)
        await agent._on_backgroundify_complete("x", result)
        assert len(agent._event_inbox) == 1
        evt = agent._event_inbox.drain_all()[0].event
        assert evt.job_id == "x"
        assert evt.content == "ok"

    async def test_exception(self, agent):
        await agent._on_backgroundify_complete("x", RuntimeError("boom"))
        assert len(agent._event_inbox) == 1
        evt = agent._event_inbox.drain_all()[0].event
        assert evt.context["error"] == "boom"

    async def test_cancelled_exception(self, agent):
        # Regression test for B-at-1 (fixed): asyncio.CancelledError is a
        # BaseException (not Exception) since Python 3.8; the handler now
        # checks for it explicitly before the generic exception arm, so a
        # cancelled background task is reported as an interrupt.
        await agent._on_backgroundify_complete("x", asyncio.CancelledError())
        assert len(agent._event_inbox) == 1
        evt = agent._event_inbox.drain_all()[0].event
        assert evt.context.get("interrupted") is True
        assert evt.context.get("final_state") == "interrupted"
        assert evt.context.get("error") == "User manually interrupted this job."

    async def test_non_cancelled_exception_is_plain_error(self, agent):
        # The contrast case: a real exception (still a BaseException, but
        # NOT a CancelledError) is reported as a plain error — NOT marked
        # interrupted. This pins the boundary the B-at-1 fix introduced.
        await agent._on_backgroundify_complete("y", RuntimeError("boom"))
        assert len(agent._event_inbox) == 1
        evt = agent._event_inbox.drain_all()[0].event
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
        assert len(agent._event_inbox) == 1
        sa = agent._event_inbox.drain_all()[0].event.context["subagent_metadata"]
        assert sa["turns"] == 3
        assert sa["tools_used"] == ["bash"]


# ── _TurnResult dataclass ────────────────────────────────────────


class TestTurnResult:
    def test_default_factory_lists_independent(self):
        a = _TurnResult()
        b = _TurnResult()
        a.text_output.append("x")
        assert b.text_output == []


# ── _on_backgroundify_complete subagent metadata ─────────────────


class TestOnBackgroundifyCompleteEdges:
    async def test_subagent_metadata_attached_to_event(self, agent):
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
        captured = [env.event for env in agent._event_inbox.drain_all()]
        assert len(captured) == 1
        evt = captured[0]
        assert "subagent_metadata" in evt.context
        sa = evt.context["subagent_metadata"]
        assert sa["turns"] == 2
        assert sa["tools_used"] == ["read"]

    async def test_jobresult_interrupted_flag(self, agent):
        result = JobResult(job_id="x", output="", error="boom")
        object.__setattr__(result, "interrupted", True)
        await agent._on_backgroundify_complete("x", result)
        captured = [env.event for env in agent._event_inbox.drain_all()]
        assert len(captured) == 1
        assert captured[0].context.get("interrupted") is True
        assert captured[0].context.get("final_state") == "interrupted"

    async def test_jobresult_cancelled_flag(self, agent):
        result = JobResult(job_id="x", output="ok")
        object.__setattr__(result, "cancelled", True)
        await agent._on_backgroundify_complete("x", result)
        captured = [env.event for env in agent._event_inbox.drain_all()]
        assert len(captured) == 1
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
        from kohakuterrarium.core.jobs.executor import Executor
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
        from kohakuterrarium.core.jobs.executor import Executor

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
        from kohakuterrarium.core.jobs.executor import Executor
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
        ex.job_store.store_result(JobResult(job_id="pre", output="cached"))

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
        promoted = []

        class _Recorder(BasePlugin):
            name = "recorder"

            async def on_task_promoted(self, job_id, tool_name):
                promoted.append((job_id, tool_name))

        manager = PluginManager()
        manager.register(_Recorder())
        agent.plugins = manager
        agent._register_direct_job("bash_x", kind="tool", name="bash")
        agent._handle_promotion("bash_x", agent.controller, {}, False)
        await wait_until(lambda: promoted, what="on_task_promoted")
        assert promoted == [("bash_x", "bash")]

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
        from kohakuterrarium.core.agent.tools import dispatch as at
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
        captured = [env.event for env in agent._event_inbox.drain_all()]
        assert len(captured) == 1
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
