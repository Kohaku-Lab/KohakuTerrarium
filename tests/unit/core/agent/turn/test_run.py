"""Request ownership regressions for the awaited Agent turn driver."""

import asyncio

import pytest

from kohakuterrarium.core.agent import Agent
from kohakuterrarium.core.config_types import AgentConfig, InputConfig, OutputConfig
from kohakuterrarium.core.events import TriggerEvent, create_user_input_event
from kohakuterrarium.core.turn import Activity, AgentEventStream, TurnCapture
from kohakuterrarium.core import agent_turn
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.modules.output.base import BaseOutputModule
from kohakuterrarium.testing.llm import ScriptedLLM


class _GatedLLM(ScriptedLLM):
    def __init__(self, gate_request=1):
        super().__init__(["reply"])
        self.gate_request = gate_request
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cleanup = asyncio.Event()
        self.cleanup.set()
        self.requests = []

    async def chat(self, messages, **kwargs):
        self.requests.append(self._normalize_messages(messages))
        if len(self.requests) == self.gate_request:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.cleanup.wait()
                raise
        async for chunk in super().chat(messages, **kwargs):
            yield chunk


async def _build(tmp_path, llm):
    cfg = AgentConfig(
        name="owned_turns",
        agent_path=tmp_path,
        system_prompt="Test request ownership.",
        include_hints_in_prompt=False,
        tool_format="bracket",
        input=InputConfig(type="none"),
        output=OutputConfig(type="none"),
    )
    agent = await Agent.build(cfg, llm=llm, pwd=tmp_path, io="headless")
    await agent.start()
    return agent


async def test_concurrent_runs_have_distinct_answers(tmp_path):
    llm = ScriptedLLM(["one answer", "two answer"])
    agent = await _build(tmp_path, llm)
    try:
        one, two = await asyncio.gather(agent.run("ONE"), agent.run("TWO"))
        assert (one.text, two.text) == ("one answer", "two answer")
        assert llm.call_count == 2
    finally:
        await agent.stop()


@pytest.mark.parametrize("withdraw", ["timeout", "cancel"])
@pytest.mark.parametrize("claimed", [False, True])
async def test_queued_withdrawal_does_not_interrupt_another_turn(
    tmp_path, withdraw, claimed
):
    llm = _GatedLLM()
    agent = await _build(tmp_path, llm)
    tasks = []
    try:
        if claimed:
            agent._consumer_resume.clear()
        first = asyncio.create_task(agent.run("FIRST", raise_on_error=False))
        tasks.append(first)
        if not claimed:
            await asyncio.wait_for(llm.entered.wait(), 10)
        second = asyncio.create_task(
            agent.run(
                "SECOND",
                timeout=0.05 if withdraw == "timeout" else None,
                raise_on_error=False,
            )
        )
        tasks.append(second)
        await asyncio.sleep(0)
        if claimed:
            agent._consumer_resume.set()
            await asyncio.wait_for(llm.entered.wait(), 10)
            assert agent._event_inbox.empty()
        if withdraw == "cancel":
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
        else:
            assert (await second).status == "timeout"
        assert not first.done()
        assert not llm.cancelled.is_set()
        llm.release.set()
        assert (await first).status == "ok"
        assert (await agent.run("NEXT")).text == "reply"
        assert len(llm.requests) == 2
        assert not any(
            m.get("role") == "user" and "SECOND" in str(m.get("content"))
            for call in llm.requests
            for m in call
        )
    finally:
        llm.release.set()
        llm.cleanup.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await agent.stop()


@pytest.mark.parametrize("timeout", [None, 60])
async def test_caller_cancel_stops_its_active_turn(tmp_path, timeout):
    llm = _GatedLLM()
    agent = await _build(tmp_path, llm)
    task = asyncio.create_task(agent.run("CANCEL", timeout=timeout))
    try:
        await asyncio.wait_for(llm.entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert llm.cancelled.is_set()
        assert not agent.is_processing
        assert (await agent.run("NEXT")).text == "reply"
    finally:
        llm.release.set()
        llm.cleanup.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await agent.stop()


async def test_handoff_keeps_awaited_requests_and_fifo(tmp_path):
    llm = _GatedLLM()
    agent = await _build(tmp_path, llm)
    tasks = []
    try:
        first = asyncio.create_task(agent.run("FIRST", raise_on_error=False))
        tasks.append(first)
        await asyncio.wait_for(llm.entered.wait(), 10)
        for text in ("SECOND", "THIRD"):
            tasks.append(asyncio.create_task(agent.run(text)))
        await asyncio.sleep(0)
        agent.interrupt()
        results = await asyncio.gather(*tasks)
        assert results[0].status == "interrupted"
        assert [r.text for r in results[1:]] == ["reply", "reply"]
        assert len(llm.requests) == 3
        inputs = [
            next(m["content"] for m in reversed(call) if m["role"] == "user")
            for call in llm.requests
        ]
        assert "SECOND" in str(inputs[1]) and "THIRD" not in str(inputs[1])
        assert "THIRD" in str(inputs[2])
    finally:
        llm.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await agent.stop()


async def test_ordinary_events_still_coalesce(tmp_path):
    llm = ScriptedLLM(["combined"])
    agent = await _build(tmp_path, llm)
    try:
        await asyncio.gather(
            agent._process_event(create_user_input_event("ONE")),
            agent._process_event(create_user_input_event("TWO")),
        )
        assert llm.call_count == 1
        assert "ONE" in llm.last_user_message and "TWO" in llm.last_user_message
    finally:
        await agent.stop()


async def test_awaited_request_is_not_folded_into_active_turn(tmp_path):
    llm = _GatedLLM()
    agent = await _build(tmp_path, llm)
    first = asyncio.create_task(agent.run("FIRST"))
    second = None
    try:
        await asyncio.wait_for(llm.entered.wait(), 10)
        second = asyncio.create_task(agent.run("SECOND"))
        await asyncio.sleep(0)
        llm.release.set()
        one, two = await asyncio.gather(first, second)
        assert (one.text, two.text) == ("reply", "reply")
        assert len(llm.requests) == 2
    finally:
        llm.release.set()
        await asyncio.gather(*[t for t in (first, second) if t], return_exceptions=True)
        await agent.stop()


class _BlockingTool(BaseTool):
    tool_name = "owned_block"
    description = "Wait until released."
    execution_mode = ExecutionMode.DIRECT

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()

    def get_parameters_schema(self):
        return {"type": "object", "properties": {}}

    async def _execute(self, args, context=None):
        self.entered.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return ToolResult(output="released")


async def test_queued_request_does_not_promote_active_direct_tool(tmp_path):
    llm = ScriptedLLM(["[/owned_block][owned_block/]", "finished", "next"])
    agent = await _build(tmp_path, llm)
    tool = _BlockingTool()
    agent.add_tool(tool)
    first = asyncio.create_task(agent.run("FIRST", raise_on_error=False))
    try:
        await asyncio.wait_for(tool.entered.wait(), 10)
        second = await agent.run("SECOND", timeout=0.05, raise_on_error=False)
        assert second.status == "timeout"
        assert not first.done()
        assert agent._active_handles
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(tool.cancelled.wait(), 10)
        assert not agent.is_processing
        assert (await agent.run("NEXT")).status == "ok"
    finally:
        tool.release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await agent.stop()


async def test_repeated_cancellation_joins_active_cleanup(tmp_path):
    llm = _GatedLLM()
    llm.cleanup.clear()
    agent = await _build(tmp_path, llm)
    first = asyncio.create_task(agent.run("FIRST"))
    try:
        await asyncio.wait_for(llm.entered.wait(), 10)
        first.cancel()
        await asyncio.wait_for(llm.cancelled.wait(), 10)
        first.cancel()
        await asyncio.sleep(0)
        assert not first.done()
        llm.cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not agent.is_processing
        assert (await agent.run("NEXT")).text == "reply"
    finally:
        llm.release.set()
        llm.cleanup.set()
        await asyncio.gather(first, return_exceptions=True)
        await agent.stop()


@pytest.mark.parametrize("withdraw", ["timeout", "cancel"])
async def test_request_withdrawal_does_not_drop_queued_drive(tmp_path, withdraw):
    llm = _GatedLLM()
    agent = await _build(tmp_path, llm)
    first = asyncio.create_task(
        agent.run(
            "FIRST",
            timeout=1 if withdraw == "timeout" else None,
            raise_on_error=False,
        )
    )
    drive_event = TriggerEvent(
        type="drive_ready",
        content="DRIVE",
        context={"delivery_id": "delivery"},
        stackable=False,
    )
    drive = None
    try:
        await asyncio.wait_for(llm.entered.wait(), 10)
        drive = asyncio.create_task(agent.run_event(drive_event))
        await asyncio.sleep(0)
        if withdraw == "cancel":
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        else:
            result = await first
            assert result.status == "timeout"
            assert not result.interrupted_by_user
        result = await asyncio.wait_for(drive, 10)
        assert result.status == "ok" and result.text == "reply"
        assert result.correlation_id == "delivery"
        assert not result.interrupted_by_user
    finally:
        llm.release.set()
        await asyncio.gather(*[t for t in (first, drive) if t], return_exceptions=True)
        await agent.stop()


async def test_stop_settles_claimed_tail(tmp_path):
    llm = _GatedLLM()
    agent = await _build(tmp_path, llm)
    agent._consumer_resume.clear()
    tasks = [
        asyncio.create_task(agent.run(text, raise_on_error=False))
        for text in ("FIRST", "SECOND")
    ]
    try:
        await asyncio.sleep(0)
        agent._consumer_resume.set()
        await asyncio.wait_for(llm.entered.wait(), 10)
        assert agent._event_inbox.empty()
        await agent.stop()
        results = await asyncio.wait_for(asyncio.gather(*tasks), 1)
        assert results[1].status == "rejected"
        assert len(llm.requests) == 1
    finally:
        llm.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await agent.stop()


class _GatedOutput(BaseOutputModule):
    def __init__(self, phase):
        super().__init__()
        self.phase = phase
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def write(self, content):
        pass

    async def on_processing_start(self):
        if self.phase == "start":
            self.entered.set()
            await self.release.wait()

    async def on_processing_end(self):
        if self.phase == "end":
            self.entered.set()
            await self.release.wait()


@pytest.mark.parametrize("phase", ["start", "end"])
async def test_cancel_during_preparation_or_finalization(tmp_path, phase):
    llm = ScriptedLLM(["answer"])
    agent = await _build(tmp_path, llm)
    output = _GatedOutput(phase)
    agent.output_router.add_secondary(output)
    task = asyncio.create_task(agent.run("FIRST"))
    try:
        await asyncio.wait_for(output.entered.wait(), 10)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        output.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert llm.call_count == (0 if phase == "start" else 1)
        assert not agent.is_processing
        assert (await agent.run("NEXT")).text == "answer"
    finally:
        output.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await agent.stop()


@pytest.mark.parametrize("close", ["aclose", "cancel_next"])
async def test_stream_close_joins_driver_and_queue_getter(tmp_path, close):
    llm = _GatedLLM()
    agent = await _build(tmp_path, llm)
    before = asyncio.all_tasks()
    stream = agent.run_stream("STREAM")
    try:
        event = await anext(stream)
        assert isinstance(event, Activity) and event.kind == "processing_start"
        await asyncio.wait_for(llm.entered.wait(), 10)
        if close == "aclose":
            await stream.aclose()
        else:
            reader = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            reader.cancel()
            with pytest.raises(asyncio.CancelledError):
                await reader
        assert llm.cancelled.is_set()
        assert not agent.is_processing
        assert not (asyncio.all_tasks() - before)
        assert (await agent.run("NEXT")).text == "reply"
    finally:
        llm.release.set()
        await stream.aclose()
        await agent.stop()


async def test_detaching_observer_does_not_cancel_turn(tmp_path):
    llm = _GatedLLM()
    agent = await _build(tmp_path, llm)
    task = asyncio.create_task(agent.run("FIRST"))
    try:
        async with AgentEventStream(agent) as stream:
            await asyncio.wait_for(llm.entered.wait(), 10)
            assert isinstance(await anext(stream), Activity)
        assert not task.done() and not llm.cancelled.is_set()
        llm.release.set()
        assert (await task).text == "reply"
    finally:
        llm.release.set()
        await asyncio.gather(task, return_exceptions=True)
        await agent.stop()


async def test_cleanup_deadline_does_not_pretend_turn_is_idle(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_turn, "_INTERRUPT_GRACE_S", 0.02)
    llm = _GatedLLM()
    llm.cleanup.clear()
    agent = await _build(tmp_path, llm)
    first = asyncio.create_task(agent.run("FIRST", timeout=1, raise_on_error=False))
    second = None
    try:
        await asyncio.wait_for(llm.cancelled.wait(), 10)
        result = await asyncio.wait_for(first, 1)
        assert result.status == "timeout"
        assert agent.is_processing
        second = asyncio.create_task(agent.run("SECOND"))
        await asyncio.sleep(0)
        assert not second.done() and len(llm.requests) == 1
        llm.cleanup.set()
        assert (await second).text == "reply"
        assert not agent.is_processing
    finally:
        llm.cleanup.set()
        await asyncio.gather(*[t for t in (first, second) if t], return_exceptions=True)
        await agent.stop()


async def test_human_interrupt_during_timeout_cleanup_is_preserved(tmp_path):
    llm = _GatedLLM()
    llm.cleanup.clear()
    agent = await _build(tmp_path, llm)
    event = TriggerEvent(type="drive_ready", content="FIRST", stackable=False)
    task = asyncio.create_task(agent.run_event(event, timeout=1))
    try:
        await asyncio.wait_for(llm.cancelled.wait(), 10)
        agent.interrupt()
        llm.cleanup.set()
        result = await task
        assert result.status == "timeout"
        assert result.interrupted_by_user
    finally:
        llm.cleanup.set()
        await asyncio.gather(task, return_exceptions=True)
        await agent.stop()


async def test_interrupt_keeps_claimed_request_before_new_arrival(tmp_path):
    llm = _GatedLLM()
    agent = await _build(tmp_path, llm)
    agent._consumer_resume.clear()
    tasks = [
        asyncio.create_task(agent.run(text, raise_on_error=False))
        for text in ("FIRST", "SECOND")
    ]
    try:
        await asyncio.sleep(0)
        agent._consumer_resume.set()
        await asyncio.wait_for(llm.entered.wait(), 10)
        assert agent._event_inbox.empty()
        tasks.append(asyncio.create_task(agent.run("THIRD")))
        await asyncio.sleep(0)
        agent.interrupt()
        results = await asyncio.gather(*tasks)
        assert [r.status for r in results] == ["interrupted", "ok", "ok"]
        inputs = [
            next(m["content"] for m in reversed(call) if m["role"] == "user")
            for call in llm.requests
        ]
        assert inputs == ["FIRST", "SECOND", "THIRD"]
    finally:
        llm.release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await agent.stop()


async def test_completed_request_cannot_interrupt_next_owner(tmp_path):
    llm = _GatedLLM(gate_request=2)
    agent = await _build(tmp_path, llm)
    next_task = None
    try:
        event = create_user_input_event("DONE")
        event.context["await_turn"] = True
        env = agent._enqueue_awaiting(event, TurnCapture())
        assert (await env.future).status == "ok"
        next_task = asyncio.create_task(agent.run("NEXT"))
        await asyncio.wait_for(llm.entered.wait(), 10)
        await agent._withdraw_awaiting(env)
        assert not env.withdrawn
        assert not llm.cancelled.is_set() and not next_task.done()
        llm.release.set()
        assert (await next_task).text == "reply"
    finally:
        llm.release.set()
        if next_task:
            await asyncio.gather(next_task, return_exceptions=True)
        await agent.stop()


@pytest.mark.parametrize("action", ["resume", "cancel_consumer"])
async def test_claimed_request_waits_for_resume_and_settles_on_consumer_exit(
    tmp_path, action
):
    llm = _GatedLLM()
    agent = await _build(tmp_path, llm)
    agent._consumer_resume.clear()
    tasks = [
        asyncio.create_task(agent.run(text, raise_on_error=False))
        for text in ("FIRST", "SECOND")
    ]
    try:
        await asyncio.sleep(0)
        agent._consumer_resume.set()
        await asyncio.wait_for(llm.entered.wait(), 10)
        assert agent._event_inbox.empty()
        agent.pause()
        llm.release.set()
        assert (await tasks[0]).status == "ok"
        # Let ready callbacks settle without using a wall-clock speed assertion.
        for _ in range(6):
            await asyncio.sleep(0)
        assert not tasks[1].done() and len(llm.requests) == 1
        if action == "resume":
            agent.resume()
            assert (await tasks[1]).text == "reply"
        else:
            agent._consumer_task.cancel()
            await asyncio.gather(agent._consumer_task, return_exceptions=True)
            assert (
                await asyncio.wait_for(asyncio.shield(tasks[1]), 1)
            ).status == "rejected"
    finally:
        agent.resume()
        llm.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await agent.stop()
