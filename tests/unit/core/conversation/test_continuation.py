"""Idle-branch continuation: identity, admission, and persist/resume."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from kohakuterrarium import Terrarium, TurnEnded
from kohakuterrarium.errors import AgentNotRunningError, ConflictError, TurnError
from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.llm.base import NativeToolCall
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.testing.output import OutputRecorder
from kohakuterrarium.core.compact.manager import CompactManager
from kohakuterrarium.core.agent.history import message_history
from kohakuterrarium.core.config.types import AgentConfig
from kohakuterrarium.core.conversation.continuation import (
    continue_on_branch,
    live_branch_path,
    load_persisted_branch_view,
    persist_branch_view,
    require_no_running_jobs,
    selected_branch_view,
)
from kohakuterrarium.core.jobs.executor import Executor
from kohakuterrarium.core.jobs.job import JobState
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.session.history.raw import append_user_event_pair
from kohakuterrarium.session.resume.resume import resume_agent

import tests.helpers.history_protocol as history_protocol
import tests.helpers.two_job_protocol as two_job_protocol
from tests.helpers.waiting import wait_until
from tests.helpers.history_protocol import (
    HistoryState,
    exercise_history_trace,
    successors,
    transition_cover,
)
from tests.helpers.two_job_protocol import (
    TwoJobState,
    exercise_mid_turn_fold,
    exercise_two_job_trace,
    successors as two_job_successors,
    transition_cover as two_job_transition_cover,
)


@pytest.mark.parametrize("pending,error", [(True, None), (False, "failed")])
def test_history_mutation_rejects_pending_interruption_settlement(pending, error):
    agent = SimpleNamespace(
        _interrupt_finalizers={object()} if pending else set(),
        _interrupt_finalizer_error=error,
    )
    with pytest.raises(ConflictError, match="interruption settlement"):
        require_no_running_jobs(agent)


def _non_system(agent):
    return [
        (message.role, message.content)
        for message in agent.controller.conversation.get_messages()
        if message.role != "system"
    ]


def _non_system_prefix(messages):
    return [
        (item.get("role"), item.get("content"))
        for item in messages
        if item.get("role") != "system"
    ]


class _BarrierLLM(ScriptedLLM):
    def __init__(self, script, barrier_on_call: int | None = None):
        super().__init__(script)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.barrier_on_call = barrier_on_call
        self._calls_started = 0

    async def chat(self, messages, **kwargs):
        self._calls_started += 1
        if self.barrier_on_call == self._calls_started:
            self.release.clear()
            self.entered.set()
            await self.release.wait()
        async for chunk in super().chat(messages, **kwargs):
            yield chunk


class _SlowBg(BaseTool):
    def __init__(self, mode=ExecutionMode.BACKGROUND):
        super().__init__()
        self.mode = mode
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    @property
    def tool_name(self):
        return "slowbg"

    @property
    def description(self):
        return "Background tool."

    @property
    def execution_mode(self):
        return self.mode

    async def _execute(self, args, **kwargs):
        self.started.set()
        await self.release.wait()
        return ToolResult(output="RESULT_reviewbg")


class _ToolLLM(ScriptedLLM):
    def __init__(self, plan):
        super().__init__(["x"] * max(len(plan), 1))
        self.plan = plan
        self._last_tool_calls = []

    @property
    def last_tool_calls(self):
        return self._last_tool_calls

    async def chat(self, messages, **kwargs):
        normalized = self._normalize_messages(messages)
        self.call_log.append(normalized)
        idx = min(self.call_count, len(self.plan) - 1)
        step = self.plan[idx]
        self.call_count += 1
        self._last_tool_calls = []
        if isinstance(step, NativeToolCall):
            self._last_tool_calls = [step]
            yield ""
            return
        yield step if isinstance(step, str) else str(step)


@pytest.mark.parametrize("suppress", [False, True])
async def test_tui_cancel_keeps_history_fenced_until_cleanup(
    tmp_path, monkeypatch, suppress
):
    runtime = history_protocol._Runtime("declared", suppress)
    async with Terrarium() as engine:
        with monkeypatch.context() as patch:
            try:
                await runtime.prepare(engine, tmp_path, patch)
                runtime.agent._cancel_job(runtime.job_id, runtime.tool.tool_name)
                await asyncio.wait_for(runtime.tool.cleanup.wait(), 8)
                assert (
                    runtime.agent.executor.get_status(runtime.job_id).state
                    == JobState.RUNNING
                )
                assert runtime.agent.executor.get_result(runtime.job_id) is None
                with pytest.raises(ConflictError):
                    await runtime.agent.inject_input("wrong branch", branch_view={1: 1})
                runtime.tool.release.set()
                await asyncio.wait_for(asyncio.shield(runtime.job_task), 8)
                result = runtime.agent.executor.get_result(runtime.job_id)
                assert result.cancelled is not suppress
                assert runtime.agent.executor.get_status(runtime.job_id).state == (
                    JobState.DONE if suppress else JobState.CANCELLED
                )
            finally:
                await runtime.release()


@pytest.mark.parametrize(
    "dispatch", ["collect", "exit_then_cancel", "cancel_then_exit"]
)
@pytest.mark.parametrize("suppress", [False, True])
async def test_direct_tui_cancel_reports_actual_outcome_once(
    tmp_path, monkeypatch, dispatch, suppress
):
    tool = history_protocol._Work("promoted", suppress)
    provider = history_protocol._Provider("promoted")
    async with Terrarium() as engine:
        creature = await engine.add_creature(
            AgentConfig(
                name="direct-cancel", system_prompt="plain", tool_format="native"
            ),
            llm=provider,
            tools=[tool],
            io="headless",
            pwd=tmp_path,
            start=True,
        )
        agent = creature.agent
        recorder = OutputRecorder()
        agent.output_router.add_secondary(recorder)
        entered, resume = asyncio.Event(), asyncio.Event()
        output = agent.output_router.default_output
        flush = output.flush

        async def dispatch_flush():
            if agent._direct_job_meta and not entered.is_set():
                entered.set()
                await resume.wait()
                if dispatch != "collect":
                    raise OSError("post-dispatch flush failed")
            await flush()

        monkeypatch.setattr(output, "flush", dispatch_flush)
        turn = asyncio.create_task(
            creature.run("launch-model-job", timeout=15, raise_on_error=False)
        )
        try:
            await asyncio.wait_for(tool.started.wait(), 8)
            await asyncio.wait_for(entered.wait(), 8)
            job_id = next(iter(agent._active_handles))
            if dispatch == "exit_then_cancel":
                resume.set()
                await asyncio.wait_for(turn, 8)
                assert not tool.cleanup.is_set() and not agent._interrupt_finalizers
            agent._cancel_job(job_id, tool.tool_name)
            await asyncio.wait_for(tool.cleanup.wait(), 8)
            assert agent.executor.get_status(job_id).state == JobState.RUNNING
            resume.set()
            if dispatch != "collect":
                await asyncio.wait_for(turn, 8)
            tool.release.set()
            await asyncio.wait_for(agent.executor.wait_for(job_id), 8)
            await asyncio.wait_for(turn, 8)
            await asyncio.wait_for(agent._await_interrupt_finalizers(), 8)
            assert (
                job_id not in agent._direct_job_meta
                and job_id not in agent._active_handles
            )
            terminals = [
                kind
                for kind in recorder.activity_types()
                if kind in ("tool_error", "tool_done")
            ]
            assert terminals == ["tool_done" if suppress else "tool_error"]
            result = agent.executor.get_result(job_id)
            assert result.cancelled is not suppress
            messages = agent.controller.conversation.to_messages()
            replies = [
                m
                for m in messages
                if m.get("role") == "tool" and m.get("tool_call_id") == "model-call"
            ]
            assert len(replies) == 1
            if suppress:
                assert "RESULT_BRANCH_B" in str(replies[0]["content"])
            else:
                assert (
                    "cancel" in str(replies[0]["content"]).lower()
                    or "interrupt" in str(replies[0]["content"]).lower()
                )
        finally:
            resume.set()
            tool.release.set()
            await asyncio.gather(turn, return_exceptions=True)
            agent.output_router.remove_secondary(recorder)


async def test_full_turn_interrupt_preserves_suppressed_direct_success(tmp_path):
    tool = history_protocol._Work("promoted", True)
    provider = history_protocol._Provider("promoted")
    async with Terrarium() as engine:
        creature = await engine.add_creature(
            AgentConfig(
                name="direct-full-interrupt",
                system_prompt="plain",
                tool_format="native",
            ),
            llm=provider,
            tools=[tool],
            io="headless",
            pwd=tmp_path,
            start=True,
        )
        agent = creature.agent
        turn = asyncio.create_task(
            creature.run("launch-model-job", timeout=15, raise_on_error=False)
        )
        try:
            await asyncio.wait_for(tool.started.wait(), 8)
            job_id = next(iter(agent._active_handles))
            agent.interrupt()
            await asyncio.wait_for(tool.cleanup.wait(), 8)
            tool.release.set()
            await asyncio.wait_for(turn, 8)
            await asyncio.wait_for(agent.executor.wait_for(job_id), 8)
            await wait_until(
                lambda: job_id not in agent._direct_job_meta,
                timeout=8,
                what="direct interruption finalizer",
            )
            messages = agent.controller.conversation.to_messages()
            replies = [
                m
                for m in messages
                if m.get("role") == "tool" and m.get("tool_call_id") == "model-call"
            ]
            assert len(replies) == 1
            assert "RESULT_BRANCH_B" in str(replies[0]["content"])
        finally:
            tool.release.set()
            await asyncio.gather(turn, return_exceptions=True)


@pytest.mark.parametrize("ephemeral", [False, True])
async def test_late_text_result_respects_interaction_reset(tmp_path, ephemeral):
    tool = history_protocol._Work("promoted", True)
    provider = ScriptedLLM(["[/model_work][model_work/]", "next reply"])
    async with Terrarium(session_dir=tmp_path) as engine:
        creature = await engine.add_creature(
            AgentConfig(
                name="late-text",
                system_prompt="plain",
                tool_format="bracket",
                ephemeral=ephemeral,
            ),
            llm=provider,
            tools=[tool],
            io="headless",
            pwd=tmp_path,
            start=True,
        )
        agent = creature.agent
        turn = asyncio.create_task(creature.run("first", timeout=10))
        try:
            await asyncio.wait_for(tool.started.wait(), 5)
            (job_id,) = agent._active_handles
            agent.interrupt()
            await asyncio.wait_for(tool.cleanup.wait(), 5)
            assert (await asyncio.wait_for(turn, 5)).interrupted_by_user
            agent.controller.config.ephemeral = not ephemeral
        finally:
            tool.release.set()
            await asyncio.gather(turn, return_exceptions=True)
        assert (await creature.run("unrelated", timeout=5)).text == "next reply"
        assert provider.call_count == 2
        assert ("RESULT_BRANCH_B" in str(provider.call_log[-1])) == (not ephemeral)
        assert agent.executor.get_status(job_id).state == JobState.DONE
        assert (
            job_id not in agent._direct_job_meta and job_id not in agent._active_handles
        )
        recorded = [
            e
            for e in agent.session_store.get_events(agent.config.name)
            if e["type"] == "tool_result" and e.get("call_id") == job_id
        ]
        assert len(recorded) == 1 and "RESULT_BRANCH_B" in str(recorded[0])


async def test_stop_during_direct_interrupt_does_not_duplicate_result(tmp_path):
    tool = history_protocol._Work("promoted", True)
    provider = history_protocol._Provider("promoted")
    async with Terrarium() as engine:
        creature = await engine.add_creature(
            AgentConfig(
                name="stop-interrupt", system_prompt="plain", tool_format="native"
            ),
            llm=provider,
            tools=[tool],
            io="headless",
            pwd=tmp_path,
            start=True,
        )
        agent = creature.agent
        turn = asyncio.create_task(
            creature.run("launch-model-job", timeout=15, raise_on_error=False)
        )
        stop = None
        try:
            await asyncio.wait_for(tool.started.wait(), 8)
            agent.interrupt()
            await asyncio.wait_for(tool.cleanup.wait(), 8)
            await asyncio.wait_for(turn, 8)
            stop = asyncio.create_task(agent.stop())
            await wait_until(lambda: not agent._running, timeout=8)
            await asyncio.sleep(0)
            tool.release.set()
            await asyncio.wait_for(stop, 8)
            await asyncio.sleep(0)
            replies = [
                m
                for m in agent.controller.conversation.to_messages()
                if m.get("role") == "tool" and m.get("tool_call_id") == "model-call"
            ]
            assert len(replies) == 1
        finally:
            tool.release.set()
            await asyncio.gather(
                turn, *([stop] if stop else []), return_exceptions=True
            )


@pytest.mark.parametrize("error_message", ["cannot deliver interrupted result", ""])
async def test_finalizer_exception_blocks_following_turn(
    tmp_path, monkeypatch, error_message
):
    tool = history_protocol._Work("promoted", False)
    provider = history_protocol._Provider("promoted")
    async with Terrarium() as engine:
        creature = await engine.add_creature(
            AgentConfig(
                name="failed-finalizer", system_prompt="plain", tool_format="native"
            ),
            llm=provider,
            tools=[tool],
            io="headless",
            pwd=tmp_path,
            start=True,
        )
        agent = creature.agent
        turn = asyncio.create_task(
            creature.run("launch-model-job", timeout=15, raise_on_error=False)
        )
        try:
            await asyncio.wait_for(tool.started.wait(), 8)

            def broken(*args, **kwargs):
                raise RuntimeError(error_message)

            recorder = OutputRecorder()
            agent.output_router.add_secondary(recorder)
            job_id = next(iter(agent._active_handles))
            monkeypatch.setattr(agent, "_append_interrupted_tool_message", broken)
            agent.interrupt()
            await asyncio.wait_for(tool.cleanup.wait(), 8)
            await asyncio.wait_for(turn, 8)
            before = provider.call_count
            batch = asyncio.gather(
                agent.inject_input("primary"),
                agent.inject_input("folded"),
                return_exceptions=True,
            )
            try:
                await wait_until(lambda: agent._active_event_run, timeout=3)
                assert [env.event.content for env in agent._active_event_run] == [
                    "primary",
                    "folded",
                ]
            finally:
                tool.release.set()
                outcomes = await asyncio.wait_for(batch, 8)
            assert all(
                isinstance(exc, TurnError) and "settlement failed" in str(exc)
                for exc in outcomes
            )
            await wait_until(lambda: not agent._interrupt_finalizers, timeout=8)
            agent._cancel_job(job_id, tool.tool_name)
            with pytest.raises(RuntimeError, match="settlement failed"):
                await agent._await_interrupt_finalizers()
            assert recorder.activity_types().count("tool_error") == 1
            result = await creature.run("next request", timeout=3, raise_on_error=False)
            assert result.status == "error"
            with pytest.raises(TurnError, match="Interrupted job settlement failed"):
                await agent.inject_input(
                    "not buffered", branch_view=live_branch_path(agent)
                )
            assert provider.call_count == before
        finally:
            tool.release.set()
            await asyncio.gather(turn, return_exceptions=True)


async def test_full_turn_interrupt_finalizer_fences_next_turn(tmp_path):
    tool = history_protocol._Work("promoted", True)
    provider = history_protocol._Provider("promoted")
    async with Terrarium() as engine:
        creature = await engine.add_creature(
            AgentConfig(
                name="direct-interrupt-fence",
                system_prompt="plain",
                tool_format="native",
            ),
            llm=provider,
            session=tmp_path / "fence.kohakutr",
            tools=[tool],
            io="headless",
            pwd=tmp_path,
            start=True,
        )
        agent = creature.agent
        await creature.run("original branch", timeout=8)
        assert await agent.edit_and_rerun(1, "edited branch")
        assert live_branch_path(agent)[1] == 2
        turn = asyncio.create_task(
            creature.run("launch-model-job", timeout=15, raise_on_error=False)
        )
        tasks = [turn]

        async def wait_claim(text):
            await wait_until(
                lambda: agent._active_event_run
                and agent._active_event_run[0].event.content == text,
                timeout=3,
            )

        async def streamed(text):
            async for event in creature.run_stream(text, timeout=8):
                if isinstance(event, TurnEnded):
                    return event.result

        try:
            await asyncio.wait_for(tool.started.wait(), 8)
            agent.interrupt()
            await asyncio.wait_for(tool.cleanup.wait(), 8)
            await asyncio.wait_for(turn, 8)
            (finalizer,) = agent._interrupt_finalizers
            before = provider.call_count
            outcomes = []
            for text, request in (
                ("discard run", creature.run),
                ("discard stream", streamed),
            ):
                task = asyncio.create_task(request(text))
                tasks.append(task)
                await wait_claim(text)
                agent.interrupt()
                result = await asyncio.wait_for(task, 3)
                outcomes.append((result.status, result.interrupted_by_user))
            assert outcomes == [("interrupted", True)] * 2

            batched = [
                asyncio.create_task(agent.inject_input(text))
                for text in ("discard primary", "discard folded")
            ]
            tasks.extend(batched)
            await wait_claim("discard primary")
            assert [env.event.content for env in agent._active_event_run] == [
                "discard primary",
                "discard folded",
            ]
            agent.interrupt()
            assert await asyncio.wait_for(asyncio.gather(*batched), 3) == [True, False]
            assert provider.call_count == before
            assert not finalizer.done()

            next_turn = asyncio.create_task(creature.run("next request", timeout=8))
            tasks.append(next_turn)
            await wait_claim("next request")

            async def switch_after_settlement():
                await finalizer
                assert not agent._interrupt_finalizers
                return await agent.inject_input("switch", branch_view={1: 1})

            switch = asyncio.create_task(switch_after_settlement())
            tasks.append(switch)
            tool.release.set()
            with pytest.raises(ConflictError, match="while a turn is active"):
                await asyncio.wait_for(switch, 8)
            next_result = await next_turn
            assert next_result.ok
            assert not any(
                a.kind in {"tool_done", "tool_error"} for a in next_result.activities
            )
            users = [
                m.get("content")
                for m in provider.call_log[-1]
                if m.get("role") == "user"
            ]
            assert "edited branch" in users and "original branch" not in users
            assert "next request" in users
            assert not any("discard " in str(content) for content in users)
            assert await agent.inject_input("switch after turn", branch_view={1: 1})
            assert live_branch_path(agent)[1] == 1
        finally:
            tool.release.set()
            await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.parametrize("first", [0, 1])
@pytest.mark.parametrize("cancel_second", [False, True])
async def test_two_job_mid_turn_fold(tmp_path, monkeypatch, first, cancel_second):
    await exercise_mid_turn_fold(tmp_path, monkeypatch, first, cancel_second)


async def test_two_job_mid_turn_fold_detects_missing_projection(tmp_path, monkeypatch):
    original = message_history.include_settled_tool_results

    def without_same_turn_projection(
        events, live_event_ids, *, covered_event_ids=None, live_positions=None
    ):
        result = original(
            events,
            live_event_ids,
            covered_event_ids=covered_event_ids,
            live_positions=live_positions,
        )
        by_id = {event.get("event_id"): event for event in events}
        result_by_source = {
            event.get("_source_event_id"): event
            for event in result
            if event.get("_settled_from_call")
        }
        same_turn_ids = {
            event.get("event_id")
            for event in events
            if event.get("type") == "tool_result"
            and event.get("event_id") in result_by_source
            and any(
                call.get("type") == "tool_call"
                and call.get("call_id") == event.get("call_id")
                and call.get("turn_index") == event.get("turn_index")
                for call in events
            )
        }
        events = list(events)
        without_late = []
        for event in result:
            if event.get("_source_event_id") in same_turn_ids:
                continue
            if event.get("event_id") in same_turn_ids:
                continue
            without_late.append(event)
        for source_id in same_turn_ids:
            original_index = next(
                i
                for i, event in enumerate(events)
                if event.get("event_id") == source_id
            )
            following = next(
                (
                    event.get("event_id")
                    for event in events[original_index + 1 :]
                    if event.get("event_id") is not None
                    and any(
                        item.get("event_id") == event.get("event_id")
                        for item in without_late
                    )
                ),
                None,
            )
            insertion = next(
                (
                    i
                    for i, item in enumerate(without_late)
                    if item.get("event_id") == following
                ),
                len(without_late),
            )
            without_late.insert(insertion, by_id[source_id])
        return without_late

    monkeypatch.setattr(
        message_history, "include_settled_tool_results", without_same_turn_projection
    )
    with pytest.raises(AssertionError, match="restored folded outcome") as caught:
        await exercise_mid_turn_fold(tmp_path, monkeypatch, 0, False)
    assert "Cleanup also failed:" not in str(caught.value)


async def test_two_job_mid_turn_fold_mutant_rejects_cleanup_failure(
    tmp_path, monkeypatch
):
    original = two_job_protocol._Runtime.release

    async def release_then_fail(runtime):
        await original(runtime)
        raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(two_job_protocol._Runtime, "release", release_then_fail)
    with pytest.raises(AssertionError, match="Cleanup also failed"):
        await test_two_job_mid_turn_fold_detects_missing_projection(
            tmp_path, monkeypatch
        )


def test_two_job_model_graph():
    states, edges, traces = two_job_transition_cover()
    visited, exercised = {TwoJobState()}, set()
    for trace in traces:
        state = TwoJobState()
        for action in trace:
            target = two_job_successors(state)[action]
            exercised.add((state, action, target))
            visited.add(target)
            state = target
    assert len(visited) == 76 and len(edges) == 88 and len(traces) == 29
    assert visited == states
    assert exercised == edges
    assert any(state.may_switch for state in states)
    assert any(
        state.consuming and any(j.phase == "running" for j in state.jobs)
        for state in states
    )
    assert any(len(state.consuming) == 2 for state in states)


@pytest.mark.parametrize(
    "trace", [pytest.param(t, id="-".join(t)) for t in two_job_transition_cover()[2]]
)
async def test_two_job_model_conformance(tmp_path, monkeypatch, trace):
    await exercise_two_job_trace(tmp_path, monkeypatch, trace)


@pytest.mark.parametrize(
    "fault", ["swap", "duplicate", "extra_request", "repeat", "reverse"]
)
async def test_two_job_model_detects_oracle_faults(tmp_path, monkeypatch, fault):
    original = two_job_protocol._Provider.chat

    async def corrupted(self, messages, **kwargs):
        if self.recording:
            if fault == "swap":
                messages = deepcopy(messages)
                content = messages[-1]["content"]
                messages[-1]["content"] = (
                    content.replace("TWO_RESULT_0", "TEMP_SWAP")
                    .replace("TWO_RESULT_1", "TWO_RESULT_0")
                    .replace("TEMP_SWAP", "TWO_RESULT_1")
                )
            elif fault == "duplicate":
                messages = deepcopy(messages)
                messages[-1]["content"] += " TWO_RESULT_1"
            elif fault == "repeat":
                messages = deepcopy(messages)
                messages[-1]["content"] += "\n\n" + messages[-1]["content"]
            elif fault == "reverse":
                messages = deepcopy(messages)
                messages[-1]["content"] = "\n\n".join(
                    reversed(messages[-1]["content"].split("\n\n"))
                )
            else:
                self.requests.append([])
        async for chunk in original(self, messages, **kwargs):
            yield chunk

    monkeypatch.setattr(two_job_protocol._Provider, "chat", corrupted)
    trace = (
        ("finish_0", "finish_1", "begin", "end")
        if fault == "reverse"
        else ("finish_0", "begin", "end", "finish_1", "begin", "end")
    )
    with pytest.raises(AssertionError, match="two jobs divergent prefix") as caught:
        await exercise_two_job_trace(tmp_path, monkeypatch, trace)
    assert (
        "finish_0 -> finish_1 -> begin" if fault == "reverse" else "finish_0 -> begin"
    ) in str(caught.value)
    assert isinstance(caught.value.__cause__, AssertionError)
    assert (
        "provider request count"
        if fault == "extra_request"
        else "batch result attribution"
    ) in str(caught.value.__cause__)


@pytest.mark.parametrize("launch", ["declared", "requested", "promoted"])
def test_history_model_graph_has_complete_transition_witnesses(launch):
    states, edges, traces = transition_cover(launch)
    visited, exercised = {HistoryState()}, set()
    for trace in traces:
        state = HistoryState()
        for action in trace:
            target = successors(state, launch)[action]
            exercised.add((state, action, target))
            visited.add(target)
            state = target
    assert visited == states
    assert exercised == edges
    assert {state.compact for state in states} == {
        "idle",
        "summarizing",
        "flushing",
        "complete",
        "skipped",
    }
    assert {state.job for state in states} == {
        "running",
        "cancelling",
        "queued",
        "consuming",
        "settled",
    } | ({"handoff"} if launch != "declared" else set())
    assert any(state.may_switch for state in states)
    assert any(
        state.compact == "flushing" and state.job == "settled" and not state.may_switch
        for state in states
    )
    assert transition_cover(launch)[2] == traces


@pytest.mark.parametrize(
    "launch,trace",
    [
        pytest.param(launch, trace, id=f"{launch}-{'-'.join(trace)}")
        for launch in ("declared", "requested", "promoted")
        for trace in transition_cover(launch)[2]
    ],
)
async def test_history_model_transition_conformance(
    tmp_path, monkeypatch, launch, trace
):
    await exercise_history_trace(tmp_path, monkeypatch, launch, trace)


@pytest.mark.parametrize("launch", ["declared", "requested", "promoted"])
async def test_history_model_suppressed_cancellation(tmp_path, monkeypatch, launch):
    trace = (
        "start_compact",
        "cancel_job",
        "finish_job",
        *(("deliver_job",) if launch != "declared" else ()),
        "begin_consumption",
        "apply_summary",
        "cancel_compact",
        "end_consumption",
    )
    await exercise_history_trace(
        tmp_path, monkeypatch, launch, trace, suppress_cancel=True
    )


@pytest.mark.parametrize(
    "fault,trace,diagnostic",
    [
        ("early_job_terminal", ("cancel_job",), "job execution outcome"),
        (
            "missing_compact_terminal",
            ("cancel_before_entry",),
            "compact terminal stream",
        ),
        (
            "duplicate_compact_terminal",
            ("start_compact", "apply_summary", "cancel_compact"),
            "compact terminal stream",
        ),
    ],
)
async def test_history_model_detects_historical_faults(
    tmp_path, monkeypatch, fault, trace, diagnostic
):
    original_cancel_job, original_cancel_compact = (
        Executor.cancel,
        CompactManager.cancel,
    )

    async def early_job_terminal(self, job_id):
        accepted = await original_cancel_job(self, job_id)
        if accepted:
            self.job_store.update_status(job_id, state=JobState.CANCELLED)
        return accepted

    def missing_compact_terminal(self, task, lease):
        if task.cancelled() and self._active_lease is lease:
            self._dispatch.release(lease)
            self._active_lease = None
            if self._compact_task is task:
                self._compact_task = None

    async def duplicate_compact_terminal(self):
        await original_cancel_compact(self)
        if self._compact_count:
            self._emit_compact_skipped("cancelled", "Compaction cancelled")

    match fault:
        case "early_job_terminal":
            monkeypatch.setattr(Executor, "cancel", early_job_terminal)
        case "missing_compact_terminal":
            monkeypatch.setattr(
                CompactManager, "_finalize_cancelled_task", missing_compact_terminal
            )
        case "duplicate_compact_terminal":
            monkeypatch.setattr(CompactManager, "cancel", duplicate_compact_terminal)
    with pytest.raises(AssertionError, match="divergent prefix") as caught:
        await exercise_history_trace(tmp_path, monkeypatch, "requested", trace)
    assert " -> ".join(trace) in str(caught.value)
    assert isinstance(caught.value.__cause__, AssertionError)
    assert diagnostic in str(caught.value.__cause__)


@pytest.mark.parametrize("fault", ["extra_request", "leaked_tail", "cleanup_error"])
async def test_history_model_oracle_regressions(tmp_path, monkeypatch, fault):
    original_act = history_protocol._Runtime.act
    original_release = history_protocol._Runtime.release
    original_chat = history_protocol._Provider.chat

    async def extra_request(self, action):
        await original_act(self, action)
        if action == "end_consumption":
            async for _ in self.provider.chat(
                self.agent.controller.conversation.to_messages()
            ):
                pass

    async def leaked_tail(self, messages, **kwargs):
        if messages[-1].get("content") == "allowed branch switch":
            messages = deepcopy(messages)
            messages.insert(-1, {"role": "user", "content": "B_TAIL"})
        async for chunk in original_chat(self, messages, **kwargs):
            yield chunk

    async def broken_delivery(self, action):
        if action == "deliver_job":
            raise TimeoutError("delivery never arrived")
        await original_act(self, action)

    async def broken_cleanup(self):
        await original_release(self)
        raise TimeoutError("cleanup delivery missing")

    trace = ("finish_job", "deliver_job", "begin_consumption", "end_consumption")
    match fault:
        case "extra_request":
            monkeypatch.setattr(history_protocol._Runtime, "act", extra_request)
            diagnostic = "divergent prefix"
        case "leaked_tail":
            monkeypatch.setattr(history_protocol._Provider, "chat", leaked_tail)
            diagnostic = "branch isolation"
        case "cleanup_error":
            monkeypatch.setattr(history_protocol._Runtime, "act", broken_delivery)
            monkeypatch.setattr(history_protocol._Runtime, "release", broken_cleanup)
            diagnostic = "divergent prefix"
    with pytest.raises(AssertionError, match=diagnostic) as caught:
        await exercise_history_trace(tmp_path, monkeypatch, "requested", trace)
    if fault == "extra_request":
        assert "completion provider request count" in str(caught.value.__cause__)
    if fault == "cleanup_error":
        assert "finish_job -> deliver_job" in str(caught.value)
        assert isinstance(caught.value.__cause__, TimeoutError)
        assert str(caught.value.__cause__) == "delivery never arrived"
        assert "cleanup delivery missing" in str(caught.value)


async def _started_with_bg(tmp_path, plan, mode=ExecutionMode.BACKGROUND):
    tool = _SlowBg(mode)
    provider = _ToolLLM(plan)
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    await engine.__aenter__()
    creature = await engine.add_creature(
        AgentConfig(name="alice", system_prompt="plain", tool_format="native"),
        llm=provider,
        tools=[tool],
        io="headless",
        start=True,
    )
    return engine, creature, creature.agent, provider, tool


async def _started(tmp_path, script, *, barrier_on_call=None):
    provider = (
        _BarrierLLM(script, barrier_on_call=barrier_on_call)
        if barrier_on_call is not None
        else ScriptedLLM(script)
    )
    engine = Terrarium(session_dir=str(tmp_path / "sessions"))
    await engine.__aenter__()
    creature = await engine.add_creature(
        AgentConfig(name="alice", system_prompt="plain"),
        llm=provider,
        io="headless",
        start=True,
    )
    return engine, creature, creature.agent, provider


def _snapshot(agent, provider):
    return {
        "conv": _non_system(agent),
        "view": selected_branch_view(agent),
        "persisted": load_persisted_branch_view(agent.session_store, "alice"),
        "turn": agent._turn_index,
        "branch": agent._branch_id,
        "path": list(agent._parent_branch_path),
        "calls": provider.call_count,
        "inbox": len(agent._event_inbox),
    }


async def test_no_store_rejects_a_different_branch():
    processed = []

    async def process(event):
        processed.append(event)
        return True

    agent = SimpleNamespace(
        session_store=None,
        config=SimpleNamespace(name="alice"),
        _parent_branch_path=[],
        _turn_index=1,
        _branch_id=1,
        _message_mutation_lock=None,
        _running=True,
        _paused=False,
        _process_event=process,
        _ensure_history_mutation_idle=lambda: None,
    )
    with pytest.raises(ConflictError, match="without a session store"):
        await continue_on_branch(agent, object(), {1: 2})
    assert processed == []


async def test_no_store_same_view_uses_baseline_input():
    processed = []

    async def process(event):
        processed.append(event.content)
        return True

    agent = SimpleNamespace(
        session_store=None,
        config=SimpleNamespace(name="alice"),
        _parent_branch_path=[],
        _turn_index=1,
        _branch_id=1,
        _message_mutation_lock=None,
        _running=True,
        _paused=False,
        _process_event=process,
        _ensure_history_mutation_idle=lambda: None,
    )
    event = SimpleNamespace(content="hello")
    assert await continue_on_branch(agent, event, {1: 1}) is True
    assert processed == ["hello"]


async def test_stopped_rejection_is_readonly(tmp_path):
    engine, creature, agent, provider = await _started(tmp_path, ["A1", "A1-rerun"])
    try:
        await creature.run("U1", timeout=8)
        await agent.regenerate_last_response()
        before = _snapshot(agent, provider)
        await agent.stop()
        with pytest.raises(AgentNotRunningError):
            await agent.inject_input("continue-original", branch_view={1: 1})
        after = _snapshot(agent, provider)
        after.pop("inbox")
        before.pop("inbox")
        assert after == before
    finally:
        await engine.__aexit__(None, None, None)


@pytest.mark.parametrize("selection", ["confirmed", "missing", "stale", "partial"])
async def test_same_branch_paused_input_runs_after_resume(
    tmp_path, monkeypatch, selection
):
    engine, creature, agent, provider = await _started(tmp_path, ["A1", "A2", "queued"])
    try:
        await creature.run("U1", timeout=8)
        await creature.run("U2", timeout=8)
        current = live_branch_path(agent)
        persist_branch_view(agent.session_store, "alice", current)
        if selection == "missing":
            persist_branch_view(agent.session_store, "alice", None)
        elif selection == "stale":
            persist_branch_view(agent.session_store, "alice", {1: 1})
        requested = {1: 1} if selection == "partial" else current
        reads = []
        original = agent.session_store.get_events

        def observed_read(*args, **kwargs):
            reads.append(args)
            return original(*args, **kwargs)

        monkeypatch.setattr(agent.session_store, "get_events", observed_read)
        agent.pause()
        queued = await agent.inject_input("queued-same", branch_view=requested)
        assert queued is False
        assert provider.call_count == 2
        assert bool(reads) is (selection != "confirmed")
        assert load_persisted_branch_view(agent.session_store, "alice") == current
        reads.clear()
        agent.resume()
        for _ in range(80):
            if provider.call_count > 2:
                break
            await asyncio.sleep(0.02)
        assert _non_system_prefix(provider.call_log[-1]) == [
            ("user", "U1"),
            ("assistant", "A1"),
            ("user", "U2"),
            ("assistant", "A2"),
            ("user", "queued-same"),
        ]
        assert ("assistant", "queued") in _non_system(agent)
        assert reads == []
    finally:
        await engine.__aexit__(None, None, None)


def _user_events(agent):
    return [
        (event["type"], event.get("content"), event["turn_index"], event["branch_id"])
        for event in agent.session_store.get_events("alice")
        if event.get("type") in ("user_input", "user_message", "user_input_injected")
    ]


async def test_user_input_folded_behind_a_channel_message_opens_its_turn(tmp_path):
    engine, creature, agent, provider = await _started(
        tmp_path, ["A1", "A2", "A3", "A4"], barrier_on_call=2
    )
    try:
        await creature.run("U1", timeout=8)
        attach_view = selected_branch_view(agent)
        agent.pause()
        channel = TriggerEvent(
            type=EventType.CHANNEL_MESSAGE,
            content="[channel team] bob: done",
            context={"channel": "team", "sender": "bob"},
            stackable=True,
        )
        delivered = asyncio.create_task(agent._process_event(channel))
        await asyncio.sleep(0)
        folded = await agent.inject_input(
            "U2", source="web", pending_id="p-u2", branch_view=attach_view
        )
        assert folded is False
        agent.resume()
        await asyncio.wait_for(provider.entered.wait(), 2)
        assert live_branch_path(agent) == {1: 1, 2: 1}
        queued = await agent.inject_input(
            "Q", source="web", pending_id="p-q", branch_view=attach_view
        )
        assert queued is False
        provider.release.set()
        await delivered
        for _ in range(100):
            if provider.call_count == 3 and not agent.is_processing:
                break
            await asyncio.sleep(0.02)
        assert load_persisted_branch_view(agent.session_store, "alice") == {
            1: 1,
            2: 1,
        }
        assert await agent.inject_input("U3", source="web", branch_view=attach_view)
        assert _user_events(agent) == [
            ("user_input", "U1", 1, 1),
            ("user_message", "U1", 1, 1),
            ("user_input", "U2", 2, 1),
            ("user_message", "U2", 2, 1),
            ("user_input_injected", "Q", 2, 1),
            ("user_input", "U3", 3, 1),
            ("user_message", "U3", 3, 1),
        ]
        live = _non_system(agent)
        assert live[2:] == [
            ("user", "[channel_message] [channel team] bob: done\n\nU2"),
            ("assistant", "A2"),
            ("user", "Q"),
            ("assistant", "A3"),
            ("user", "U3"),
            ("assistant", "A4"),
        ]
        agent._reload_conversation_under_branch_view(live_branch_path(agent))
        assert _non_system(agent) == live
    finally:
        provider.release.set()
        await engine.__aexit__(None, None, None)


@pytest.mark.parametrize("phase", ["before_write", "after_write"])
async def test_continuation_during_rerun_write_is_rejected(tmp_path, phase):
    engine, creature, agent, provider = await _started(
        tmp_path, ["A1", "A1-rerun", "must-not-run"]
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    original = agent.session_store.run
    written = {"n": 0}

    async def blocked_run(fn, *args, **kwargs):
        if fn is append_user_event_pair:
            if phase == "after_write":
                result = await original(fn, *args, **kwargs)
                written["n"] += 1
                entered.set()
                await release.wait()
                return result
            entered.set()
            await release.wait()
            written["n"] += 1
            return await original(fn, *args, **kwargs)
        return await original(fn, *args, **kwargs)

    try:
        await creature.run("U1", timeout=8)
        inbox_before = len(agent._event_inbox)
        agent.session_store.run = blocked_run
        rerun = asyncio.create_task(agent.regenerate_last_response())
        await asyncio.wait_for(entered.wait(), 2)
        calls = provider.call_count
        live = live_branch_path(agent)
        with pytest.raises(ConflictError):
            await agent.inject_input("continue-original", branch_view={1: 1})
        with pytest.raises(ConflictError):
            await agent.inject_input("same-view", branch_view=live)
        assert provider.call_count == calls
        assert len(agent._event_inbox) == inbox_before
        if phase == "after_write":
            assert written["n"] == 1
            assert live[1] == 2
        else:
            assert written["n"] == 0
        release.set()
        await rerun
        assert "A1-rerun" in _assistant_contents(agent)
    finally:
        release.set()
        await engine.__aexit__(None, None, None)


def _assistant_contents(agent):
    return [
        message.content
        for message in agent.controller.conversation.get_messages()
        if message.role == "assistant"
    ]


async def test_reload_read_failure_never_generates_wrong_branch(tmp_path, monkeypatch):
    engine, creature, agent, provider = await _started(
        tmp_path, ["A1", "A1-rerun", "must-not-run"]
    )
    try:
        await creature.run("U1", timeout=8)
        await agent.regenerate_last_response()
        before = _snapshot(agent, provider)
        monkeypatch.setattr(
            agent.session_store,
            "get_events",
            lambda name: (_ for _ in ()).throw(OSError("history unavailable")),
        )
        with pytest.raises(OSError):
            await agent.inject_input("continue-original", branch_view={1: 1})
        monkeypatch.undo()
        after = _snapshot(agent, provider)
        assert after["conv"] == before["conv"]
        assert after["view"] == before["view"]
        assert after["persisted"] == before["persisted"]
        assert after["calls"] == before["calls"]
        assert "A1-rerun" in _assistant_contents(agent)
    finally:
        await engine.__aexit__(None, None, None)


async def test_persist_failure_keeps_live_agent_on_current_branch(
    tmp_path, monkeypatch
):
    engine, creature, agent, provider = await _started(
        tmp_path, ["A1", "A1-rerun", "orig-cont"]
    )
    try:
        await creature.run("U1", timeout=8)
        await agent.regenerate_last_response()
        before = _snapshot(agent, provider)

        def boom(*_args, **_kwargs):
            raise OSError("cannot persist branch view")

        monkeypatch.setattr(
            "kohakuterrarium.core.conversation.continuation.persist_branch_view", boom
        )
        with pytest.raises(OSError):
            await agent.inject_input("continue-original", branch_view={1: 1})
        assert _snapshot(agent, provider)["conv"] == before["conv"]
        assert agent._branch_id == before["branch"]
        assert provider.call_count == before["calls"]
        monkeypatch.undo()
        await agent.inject_input("continue-original", branch_view={1: 1})
        assert _non_system_prefix(provider.call_log[-1]) == [
            ("user", "U1"),
            ("assistant", "A1"),
            ("user", "continue-original"),
        ]
    finally:
        await engine.__aexit__(None, None, None)


async def test_same_view_input_queues_during_rerun_generation(tmp_path):
    engine, creature, agent, provider = await _started(
        tmp_path, ["A1", "A1-rerun", "follow"], barrier_on_call=2
    )
    try:
        await creature.run("U1", timeout=8)
        rerun = asyncio.create_task(agent.regenerate_last_response())
        await asyncio.wait_for(provider.entered.wait(), 2)
        assert provider._calls_started == 2
        live = selected_branch_view(agent)
        inject = asyncio.create_task(agent.inject_input("follow-up", branch_view=live))
        await asyncio.sleep(0)
        assert provider.call_count == 1
        provider.release.set()
        await rerun
        await inject
        assert _non_system_prefix(provider.call_log[-1])[-2:] == [
            ("assistant", "A1-rerun"),
            ("user", "follow-up"),
        ]
        assert "A1-rerun" in _assistant_contents(agent)
        assert "follow" in _assistant_contents(agent)
    finally:
        provider.release.set()
        await engine.__aexit__(None, None, None)


async def test_resume_uses_saved_original_branch_not_latest(tmp_path):
    engine, creature, agent, provider = await _started(
        tmp_path, ["A1", "A1-rerun", "orig-cont", "after-resume"]
    )
    session_path = None
    try:
        await creature.run("U1", timeout=8)
        await agent.regenerate_last_response()
        assert live_branch_path(agent)[1] == 2
        await agent.inject_input("continue-original", branch_view={1: 1})
        assert live_branch_path(agent)[1] == 1
        session_path = agent.session_store.path
        await agent.stop()
    finally:
        await engine.__aexit__(None, None, None)

    resumed, store = resume_agent(session_path, io_mode="plain", llm=provider)
    try:
        assert selected_branch_view(resumed)[1] == 1
        assert _non_system(resumed) == [
            ("user", "U1"),
            ("assistant", "A1"),
            ("user", "continue-original"),
            ("assistant", "orig-cont"),
        ]
        await resumed.start()
        await resumed.inject_input("after-resume")
        assert _non_system_prefix(provider.call_log[-1]) == [
            ("user", "U1"),
            ("assistant", "A1"),
            ("user", "continue-original"),
            ("assistant", "orig-cont"),
            ("user", "after-resume"),
        ]
        assert "A1-rerun" not in [
            content for role, content in _non_system_prefix(provider.call_log[-1])
        ]
    finally:
        await resumed.stop()
        store.close()


@pytest.mark.parametrize("launch", ["declared", "requested", "promoted"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_running_job_keeps_same_view_and_rejects_history_change(
    tmp_path, launch, cancelled
):
    engine, creature, agent, provider, tool = await _started_with_bg(
        tmp_path,
        [
            "seed",
            "rerun",
            NativeToolCall(
                "b1",
                "slowbg",
                '{"run_in_background":true}' if launch == "requested" else "{}",
            ),
            "follow",
        ],
        ExecutionMode.BACKGROUND if launch == "declared" else ExecutionMode.DIRECT,
    )
    try:
        await creature.run("seed", timeout=8)
        await agent.regenerate_last_response()
        original = {1: 1}
        assert live_branch_path(agent)[1] == 2
        second = asyncio.create_task(creature.run("start bg", timeout=20))
        await asyncio.wait_for(tool.started.wait(), 5)
        if launch == "promoted":
            handle = next(iter(agent._active_handles.values()))
            assert handle.promote()
        await second
        assert agent.executor.get_running_jobs()
        live = live_branch_path(agent)
        calls_before = provider.call_count
        await agent.inject_input("normal followup", branch_view=live)
        assert live_branch_path(agent)[1] == live[1]
        with pytest.raises(ConflictError, match="running"):
            await agent.regenerate_last_response()
        with pytest.raises(ConflictError, match="running"):
            await agent.edit_and_rerun(1, "edited while running")
        with pytest.raises(ConflictError, match="running"):
            await agent.inject_input("switch", branch_view=original)
        assert live_branch_path(agent)[1] == live[1]
        assert provider.call_count >= calls_before
        job_id = agent.executor.get_running_jobs()[0].job_id
        if cancelled:
            task = agent.executor.get_task(job_id)
            before = _snapshot(agent, provider)
            assert await agent.executor.cancel(job_id)
            with pytest.raises(ConflictError, match="running"):
                await agent.inject_input(
                    "switch after cancel request", branch_view=original
                )
            with pytest.raises(ConflictError, match="running"):
                await agent.regenerate_last_response()
            with pytest.raises(ConflictError, match="running"):
                await agent.edit_and_rerun(1, "edit after cancel request")
            assert not task.done()
            assert _snapshot(agent, provider) == before

        async def change_during_handoff():
            assert agent.executor.get_running_jobs() == []
            assert agent.executor.get_pending_count() == 0
            before = _snapshot(agent, provider)
            with pytest.raises(ConflictError):
                await agent.edit_and_rerun(-1, "edited during handoff", turn_index=1)
            with pytest.raises(ConflictError):
                await agent.regenerate_last_response()
            with pytest.raises(ConflictError):
                await agent.inject_input("switch during handoff", branch_view=original)
            assert _snapshot(agent, provider) == before

        tool.release.set()
        handoff = asyncio.create_task(change_during_handoff())
        await asyncio.wait_for(handoff, 8)
        await agent.inject_input(
            "same view after completion", branch_view=live_branch_path(agent)
        )
        completion = next(
            content
            for role, content in _non_system_prefix(provider.call_log[-1])
            if role == "user" and f"[Tool {job_id} " in str(content)
        )
        results = [
            event
            for event in agent.session_store.get_events("alice")
            if event["type"] == "tool_result" and event.get("call_id") == job_id
        ]
        assert len(results) == 1
        if cancelled:
            assert results[0]["cancelled"] is True
            assert results[0]["final_state"] == "cancelled"
            assert agent.executor.get_result(job_id).cancelled is True
        feedback = (
            f"[Tool {job_id} cancelled]"
            if cancelled
            else f"[Tool {job_id} completed]\nRESULT_reviewbg"
        )
        assert completion == (
            f"{feedback}\n\nsame view after completion"
            if launch == "declared"
            else f"same view after completion\n\n{feedback}"
        )
        assert agent._active_handles == {}
        assert agent.executor.get_running_jobs() == []
        assert len(agent._event_inbox) == 0
        await agent.inject_input("switch after settlement", branch_view=original)
        assert _non_system_prefix(provider.call_log[-1]) == [
            ("user", "seed"),
            ("assistant", "seed"),
            ("user", "switch after settlement"),
        ]
    finally:
        tool.release.set()
        await engine.__aexit__(None, None, None)
