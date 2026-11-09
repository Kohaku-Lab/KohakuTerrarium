"""Turn outcome and dispatched-job ownership regressions with real collaborators."""

import asyncio
import gc

import pytest

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.llm.base import NativeToolCall
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.core.config.types import AgentConfig
from kohakuterrarium.core.events import create_user_input_event
from kohakuterrarium.core.output_wiring import OutputWiringEntry
from kohakuterrarium.errors import ResponseStreamError
from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.modules.output.event import OutputEvent
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.testing.llm import ScriptedLLM
from tests.helpers.response_stream import PausedResponseLLM


class _FailingLLM(ScriptedLLM):
    def __init__(self, script, failure, fail_call=1, entered=None):
        super().__init__(script)
        self.failure = failure
        self.fail_call = fail_call
        self.entered = entered
        self.failed = asyncio.Event()

    async def chat(self, messages, **kwargs):
        async for chunk in super().chat(messages, **kwargs):
            yield chunk
        if self.call_count == self.fail_call:
            if self.entered is not None:
                await self.entered.wait()
            self.failed.set()
            raise self.failure


@pytest.mark.parametrize("tool_format", ["bracket", "xml", "native"])
@pytest.mark.parametrize("text", ["buffered partial", "partial [/owned]unfinished"])
@pytest.mark.parametrize("action", ["inspect", "rewind"])
async def test_user_interrupt_has_no_deferred_history_writes(
    tmp_path, monkeypatch, tool_format, text, action
):
    llm = PausedResponseLLM(text)
    agent, trace = await _build(tmp_path, llm, tool_format)
    owned_streams = []
    for name in ("run_once", "_run_text_completion", "_run_native_completion"):
        original = getattr(agent.controller, name)

        def track(*args, _original=original, **kwargs):
            stream = _original(*args, **kwargs)
            owned_streams.append(stream)
            return stream

        monkeypatch.setattr(agent.controller, name, track)
    task = asyncio.create_task(agent.run("first", raise_on_error=False))
    try:
        await asyncio.wait_for(llm.waiting.wait(), 5)
        await agent.interrupt_and_wait()
        assert (await task).status == "interrupted"
        assert llm.closed.is_set()
        messages = agent.controller.conversation.get_messages()
        partials = [m for m in messages if m.role == "assistant"]
        if action == "inspect":
            assert all(stream.ag_frame is None for stream in owned_streams)
            assert len(partials) == 1
            assert partials[0].get_text_content() == text
            assert partials[0].metadata["completion_state"] == "interrupted"
            endings = [
                i for i, e in enumerate(trace.events) if e.type == "processing_end"
            ]
            canonical = [
                i for i, e in enumerate(trace.events) if e.type == "assistant_message"
            ]
            assert len(canonical) == 1 and canonical[0] < endings[0]
        else:
            await agent.rewind_to(0)
        snapshot = agent.controller.conversation.snapshot_messages()
        event_count = len(trace.events)
        for stream in reversed(owned_streams):
            await stream.aclose()
        gc.collect()
        for _ in range(4):
            await asyncio.sleep(0)
        assert agent.controller.conversation.snapshot_messages() == snapshot
        assert len(trace.events) == event_count
        await agent.run("continue")
        assert (text in str(llm.last_messages)) == (action == "inspect")
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await agent.stop()


@pytest.mark.parametrize("tool_format", ["bracket", "xml"])
async def test_interrupt_survives_failed_buffered_display(tmp_path, tool_format):
    llm = PausedResponseLLM("buffered interrupted text")
    agent, trace = await _build(tmp_path, llm, tool_format)

    async def fail_write(content):
        raise OSError("display unavailable")

    trace.write_stream = fail_write
    task = asyncio.create_task(agent.run("first", raise_on_error=False))
    try:
        await asyncio.wait_for(llm.waiting.wait(), 5)
        await agent.interrupt_and_wait()
        assert (await task).status == "interrupted"
        assert not [event for event in trace.events if event.type == "processing_error"]
        assert any(
            m.get_text_content() == "buffered interrupted text"
            for m in agent.controller.conversation.get_messages()
        )
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await agent.stop()


class _OwnedTool(BaseTool):
    def __init__(self, release, background=False, name="owned"):
        super().__init__()
        self.release = release
        self.background = background
        self.name = name
        self.entered = asyncio.Event()
        self.calls = 0
        self.finished = False

    @property
    def tool_name(self):
        return self.name

    @property
    def description(self):
        return "Deliver a controlled result."

    @property
    def execution_mode(self):
        return ExecutionMode.BACKGROUND if self.background else ExecutionMode.DIRECT

    async def _execute(self, args, **kwargs):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        self.finished = True
        return ToolResult(output=f"{self.name} result")


class _TraceOutput(BaseOutputModule):
    def __init__(self):
        super().__init__()
        self.events = []
        self.terminal = asyncio.Event()
        self.fail_flush = None
        self.flush_failed = asyncio.Event()

    async def write(self, content):
        pass

    async def flush(self):
        if self.fail_flush is not None and self.fail_flush():
            self.fail_flush = None
            self.flush_failed.set()
            raise OSError("output flush failed")

    async def on_processing_end(self):
        self.events.append(OutputEvent(type="processing_end"))

    def on_activity_with_metadata(self, activity_type, detail, metadata):
        self.events.append(
            OutputEvent(type=activity_type, content=detail, payload=metadata)
        )
        if activity_type in {
            "tool_done",
            "tool_error",
            "subagent_done",
            "subagent_error",
        }:
            self.terminal.set()

    async def emit(self, event):
        self.events.append(event)
        if event.type not in {"assistant_message", "assistant_round_start"}:
            await super().emit(event)


class _WireRecorder:
    def __init__(self):
        self.calls = []

    async def emit(self, **kwargs):
        self.calls.append(kwargs)


async def _build(tmp_path, llm, tool_format="bracket", tools=()):
    agent = await Agent.build(
        AgentConfig(
            name="owned_outcomes", system_prompt="test", tool_format=tool_format
        ),
        llm=llm,
        tools=list(tools),
        pwd=tmp_path,
        io="headless",
    )
    trace = _TraceOutput()
    agent.output_router.default_output = trace
    await agent.start()
    return agent, trace


@pytest.mark.parametrize(
    "failure", [ResponseStreamError, RuntimeError, asyncio.CancelledError]
)
@pytest.mark.parametrize("partial", ["", "partial new answer"])
async def test_unsuccessful_turn_never_wires_previous_content(
    tmp_path, failure, partial
):
    llm = _FailingLLM(["previous answer", partial, "recovered"], failure(), fail_call=2)
    agent, _ = await _build(tmp_path, llm)
    content = OutputWiringEntry(to="receiver")
    signal = OutputWiringEntry(to="observer", with_content=False)
    agent.config.output_wiring = [content, signal]
    wire = _WireRecorder()
    agent._wiring_resolver = wire
    try:
        assert (await agent.run("first")).text == "previous answer"
        assert wire.calls[0]["content"] == "previous answer"
        failed = await agent.run("fail", raise_on_error=False)
        assert failed.status in {"error", "interrupted"}
        assert agent._last_turn_text == []
        assert len(wire.calls) == 2
        assert wire.calls[1]["entries"] == [signal]
        assert wire.calls[1]["content"] == ""
        assert (await agent.run("recover")).text == "recovered"
        assert wire.calls[-1]["entries"] == [content, signal]
        assert wire.calls[-1]["content"] == "recovered"
    finally:
        await agent.stop()


async def test_failed_followup_round_does_not_publish_success_preview(tmp_path):
    release = asyncio.Event()
    release.set()
    tool = _OwnedTool(release)
    llm = _FailingLLM(
        ["working [/owned][owned/]", "partial"], ResponseStreamError(), fail_call=2
    )
    agent, trace = await _build(tmp_path, llm, tools=[tool])
    agent.config.output_wiring = [OutputWiringEntry(to="receiver")]
    wire = _WireRecorder()
    agent._wiring_resolver = wire
    try:
        event = create_user_input_event("channel request")
        event.context.update(channel="requests", sender="caller")
        await agent._process_event(event)
        assert llm.call_count == 2
        assert not wire.calls
        assert not any(e.type == "processing_complete" for e in trace.events)
    finally:
        await agent.stop()


@pytest.mark.parametrize(
    "tool_format,block", [("bracket", "[/owned][owned/]"), ("xml", "<owned></owned>")]
)
@pytest.mark.parametrize("failure", [ResponseStreamError, RuntimeError])
@pytest.mark.parametrize("done", [False, True])
async def test_stream_error_settles_dispatched_direct_result_before_end(
    tmp_path, tool_format, block, failure, done
):
    release = asyncio.Event()
    if done:
        release.set()
    tool = _OwnedTool(release)
    llm = _FailingLLM([block, "recovered"], failure(), entered=tool.entered)
    agent, trace = await _build(tmp_path, llm, tool_format, [tool])
    task = asyncio.create_task(agent.run("fail", raise_on_error=False))
    try:
        await asyncio.wait_for(llm.failed.wait(), 5)
        if not done:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 0.05)
            assert not any(e.type == "processing_end" for e in trace.events)
        release.set()
        result = await asyncio.wait_for(task, 5)
        assert result.status == "error"
        assert tool.calls == 1 and tool.finished
        assert not agent._active_handles and not agent._direct_job_meta
        terminal = [e for e in trace.events if e.type == "tool_done"]
        assert len(terminal) == 1
        assert trace.events.index(terminal[0]) < next(
            i for i, e in enumerate(trace.events) if e.type == "processing_end"
        )
        assert llm.call_count == 1
        assert (await agent.run("recover")).text == "recovered"
        assert "owned result" in str(llm.last_messages)
        assert llm.call_count == 2 and tool.calls == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await agent.stop()


@pytest.mark.parametrize(
    "owner", ["declared_background", "requested_background", "promoted"]
)
async def test_failed_stream_preserves_background_result_owner(tmp_path, owner):
    release = asyncio.Event()
    tool = _OwnedTool(release, background=owner == "declared_background")
    block = (
        "[/owned]\n@@run_in_background=true\n[owned/]"
        if owner == "requested_background"
        else "[/owned][owned/]"
    )
    llm = _FailingLLM(
        [block, "handled background result"],
        ResponseStreamError(),
        entered=tool.entered,
    )
    agent, trace = await _build(tmp_path, llm, tools=[tool])
    task = asyncio.create_task(agent.run("fail", raise_on_error=False))
    try:
        await asyncio.wait_for(llm.failed.wait(), 5)
        if owner == "promoted":
            job_id = next(iter(agent._active_handles))
            assert agent._promote_handle(job_id)
        failed = await asyncio.wait_for(task, 5)
        assert failed.status == "error"
        assert not trace.terminal.is_set() and not tool.finished
        assert tool.calls == 1
        release.set()
        await asyncio.wait_for(trace.terminal.wait(), 5)
        await agent.run("barrier")
        assert tool.finished and tool.calls == 1
        assert len([e for e in trace.events if e.type == "tool_done"]) == 1
        assert not agent._active_handles and not agent._direct_job_meta
        assert any("owned result" in str(call) for call in llm.call_log[1:])
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await agent.stop()


@pytest.mark.parametrize(
    "tool_format,block", [("bracket", "[/owned][owned/]"), ("xml", "<owned></owned>")]
)
async def test_cancel_during_input_drain_preserves_collected_feedback(
    tmp_path, monkeypatch, tool_format, block
):
    release = asyncio.Event()
    release.set()
    tool = _OwnedTool(release)
    llm = ScriptedLLM([block, "recovered"])
    agent, _ = await _build(tmp_path, llm, tool_format, [tool])
    draining = asyncio.Event()

    async def wait_drain(controller):
        draining.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(agent, "_drain_mid_turn_pending_inputs", wait_drain)
    task = asyncio.create_task(agent.run("first", raise_on_error=False))
    try:
        await asyncio.wait_for(draining.wait(), 5)
        await agent.interrupt_and_wait()
        assert (await task).status == "interrupted"
        assert tool.calls == 1
        messages = agent.controller.conversation.get_messages()
        assert sum("owned result" in m.get_text_content() for m in messages) == 1
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await agent.stop()


async def test_interrupt_during_failure_settlement_keeps_interrupted_result(tmp_path):
    release = asyncio.Event()
    tool = _OwnedTool(release)
    llm = _FailingLLM(
        ["[/owned][owned/]", "recovered"], ResponseStreamError(), entered=tool.entered
    )
    agent, trace = await _build(tmp_path, llm, tools=[tool])
    task = asyncio.create_task(agent.run("fail", raise_on_error=False))
    try:
        await asyncio.wait_for(llm.failed.wait(), 5)
        errors = [event for event in trace.events if event.type == "processing_error"]
        assert len(errors) == 1
        assert errors[0].payload["response_outcome"]["code"] == "response_unconfirmed"
        agent.interrupt()
        result = await asyncio.wait_for(task, 5)
        assert result.status == "interrupted"
        await asyncio.wait_for(trace.terminal.wait(), 5)
        assert not tool.finished and tool.calls == 1
        assert not agent._active_handles and not agent._direct_job_meta
        terminal = [e for e in trace.events if e.type == "tool_error"]
        assert len(terminal) == 1
        assert terminal[0].payload["final_state"] == "interrupted"
        assert llm.call_count == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await agent.stop()


class _ChildLLM(ScriptedLLM):
    def __init__(self, release):
        super().__init__(["child result"])
        self.release = release
        self.entered = asyncio.Event()

    async def chat(self, messages, **kwargs):
        self.entered.set()
        await self.release.wait()
        async for chunk in super().chat(messages, **kwargs):
            yield chunk


async def test_dispatch_error_settles_native_direct_subagent(tmp_path):
    release = asyncio.Event()
    child = _ChildLLM(release)
    llm = ScriptedLLM(["dispatch", "recovered"])
    llm.last_tool_calls = [
        NativeToolCall(
            "child_call", "worker", '{"task":"work","run_in_background":false}'
        )
    ]
    agent, trace = await _build(tmp_path, llm, "native")
    agent.add_subagent(
        SubAgentConfig(name="worker", system_prompt="worker", tools=[], max_turns=1)
    )
    agent.registry.register_subagent(
        "worker", agent.subagent_manager.get_config("worker")
    )
    agent.subagent_manager.llm = child
    trace.fail_flush = lambda: bool(agent._active_handles)
    task = asyncio.create_task(agent.run("fail", raise_on_error=False))
    try:
        await asyncio.wait_for(trace.flush_failed.wait(), 5)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(task), 0.05)
        release.set()
        assert (await asyncio.wait_for(task, 5)).status == "error"
        assert child.call_count == 1
        assert not agent._active_handles and not agent._direct_job_meta
        assert len([e for e in trace.events if e.type == "subagent_done"]) == 1
        assert llm.call_count == 1
        replies = [
            msg
            for msg in agent.controller.conversation.get_messages()
            if msg.role == "tool" and msg.tool_call_id == "child_call"
        ]
        assert len(replies) == 1 and "child result" in replies[0].get_text_content()
        llm.last_tool_calls = []
        await agent.run("recover")
        assert "child result" in str(llm.last_messages)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await agent.stop()


@pytest.mark.parametrize(
    "tool_format,block", [("bracket", "[/owned][owned/]"), ("xml", "<owned></owned>")]
)
async def test_cancellation_before_tool_entry_is_never_success(
    tmp_path, tool_format, block
):
    release = asyncio.Event()
    tool = _OwnedTool(release)
    llm = _FailingLLM([block, "recovered"], asyncio.CancelledError())
    agent, trace = await _build(tmp_path, llm, tool_format, [tool])
    try:
        result = await agent.run("cancel", raise_on_error=False)
        assert result.status == "interrupted"
        assert tool.calls == 0
        assert not agent._active_handles and not agent._direct_job_meta
        terminal = [e for e in trace.events if e.type in {"tool_done", "tool_error"}]
        assert len(terminal) == 1
        assert terminal[0].type == "tool_error"
        assert terminal[0].payload["final_state"] == "interrupted"
        await agent.run("recover")
        assert "INTERRUPTED" in str(llm.last_messages)
        assert " - OK" not in str(llm.last_messages)
    finally:
        release.set()
        await agent.stop()


@pytest.mark.parametrize(
    "tool_format,block", [("bracket", "[/owned][owned/]"), ("xml", "<owned></owned>")]
)
async def test_settled_feedback_is_in_current_history_not_pending_input(
    tmp_path, tool_format, block
):
    release = asyncio.Event()
    release.set()
    tool = _OwnedTool(release)
    llm = _FailingLLM(
        [block, "edited reply"], ResponseStreamError(), entered=tool.entered
    )
    agent, _ = await _build(tmp_path, llm, tool_format, [tool])
    try:
        assert (await agent.run("fail", raise_on_error=False)).status == "error"
        assert not agent.controller.has_pending_events()
        messages = agent.controller.conversation.get_messages()
        feedback = [m for m in messages if m.metadata.get("kind") == "tool_results"]
        assert len(feedback) == 1
        assert "owned result" in feedback[0].get_text_content()
        assert feedback[0].metadata["job_ids"]
        assert feedback[0].metadata["tool_result_phase"] == "final"
        assert feedback[0].metadata["turn_index"] == agent._turn_index
        assert feedback[0].metadata["branch_id"] == agent._branch_id
        target = next(
            i
            for i, msg in enumerate(messages)
            if msg.role == "user" and msg.get_text_content() == "fail"
        )
        assert await agent.edit_and_rerun(target, "edited")
        assert llm.call_count == 2
        assert llm.last_user_message == "edited"
        assert "owned result" not in str(llm.last_messages)
        assert tool.calls == 1
    finally:
        await agent.stop()


@pytest.mark.parametrize("done", [False, True])
async def test_spontaneous_cancellation_settles_direct_job(tmp_path, done):
    release = asyncio.Event()
    if done:
        release.set()
    tool = _OwnedTool(release)
    llm = _FailingLLM(
        ["[/owned][owned/]"], asyncio.CancelledError(), entered=tool.entered
    )
    agent, trace = await _build(tmp_path, llm, tools=[tool])
    try:
        result = await agent.run("cancelled stream", raise_on_error=False)
        assert result.status == "interrupted"
        assert not agent._active_handles and not agent._direct_job_meta
        terminal = [e for e in trace.events if e.type in {"tool_done", "tool_error"}]
        assert len(terminal) == 1
        assert tool.finished == done
        assert trace.events.index(terminal[0]) < next(
            i for i, e in enumerate(trace.events) if e.type == "processing_end"
        )
    finally:
        release.set()
        await agent.stop()


@pytest.mark.parametrize("tool_format", ["bracket", "native"])
@pytest.mark.parametrize("failed", [False, True])
async def test_interrupt_preserves_results_already_collected(
    tmp_path, tool_format, failed
):
    ready = asyncio.Event()
    ready.set()
    release = asyncio.Event()
    first = _OwnedTool(ready, name="first")
    second = _OwnedTool(release, name="second")
    script = ["[/first][first/][/second][second/]", "recovered"]
    llm = (
        _FailingLLM(script, ResponseStreamError(), entered=second.entered)
        if failed and tool_format == "bracket"
        else ScriptedLLM(script)
    )
    if tool_format == "native":
        llm.last_tool_calls = [
            NativeToolCall("call_first", "first", "{}"),
            NativeToolCall("call_second", "second", "{}"),
        ]
    agent, trace = await _build(tmp_path, llm, tool_format, [first, second])
    if failed and tool_format == "native":
        trace.fail_flush = lambda: len(agent._active_handles) == 2
    task = asyncio.create_task(agent.run("fail", raise_on_error=False))
    try:
        await asyncio.wait_for(trace.terminal.wait(), 5)
        agent.interrupt()
        assert (await asyncio.wait_for(task, 5)).status == "interrupted"
        if tool_format == "native":
            llm.last_tool_calls = []
        await agent.run("recover")
        assert str(llm.last_messages).count("first result") == 1
        assert first.calls == second.calls == 1
        assert not agent._active_handles and not agent._direct_job_meta
        assert len([e for e in trace.events if e.type == "tool_done"]) == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await agent.stop()


async def test_failed_native_stream_never_dispatches_unconfirmed_calls(tmp_path):
    release = asyncio.Event()
    release.set()
    tool = _OwnedTool(release)
    llm = _FailingLLM(["partial"], ResponseStreamError())
    llm.last_tool_calls = [NativeToolCall("unconfirmed", "owned", "{}")]
    agent, _ = await _build(tmp_path, llm, "native", [tool])
    try:
        result = await agent.run("fail", raise_on_error=False)
        assert result.status == "error" and result.text == "partial"
        assert tool.calls == 0
        assert not agent._active_handles and not agent._direct_job_meta
        assert not any(
            msg.role == "tool" for msg in agent.controller.conversation.get_messages()
        )
    finally:
        await agent.stop()
