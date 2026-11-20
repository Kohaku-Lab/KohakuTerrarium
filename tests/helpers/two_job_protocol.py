"""Finite specification of two background jobs and a single inbox consumer."""

import asyncio
import sys
from collections import deque
from copy import deepcopy
from dataclasses import dataclass, replace

import pytest

from kohakuterrarium import Terrarium
from kohakuterrarium.errors import ConflictError
from kohakuterrarium.llm.base import NativeToolCall
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.core.config.types import AgentConfig
from kohakuterrarium.core.conversation.continuation import live_branch_path
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.session.resume.resume import resume_agent


@dataclass(frozen=True)
class Job:
    """Execution and cancellation state of one identified job."""

    phase: str = "running"
    cancelled: bool = False


@dataclass(frozen=True)
class TwoJobState:
    """Two job outcomes, ordered delivery, and the active consumer batch."""

    jobs: tuple[Job, Job] = (Job(), Job())
    queue: tuple[int, ...] = ()
    consuming: tuple[int, ...] = ()
    delivered: tuple[int, ...] = ()
    consumed: tuple[int, ...] = ()
    batches: tuple[tuple[int, ...], ...] = ()

    @property
    def may_switch(self) -> bool:
        return all(job.phase == "settled" for job in self.jobs)


def successors(state: TwoJobState) -> dict[str, TwoJobState]:
    """Return legal transitions for two declared-background executions."""
    result = {}
    for index, job in enumerate(state.jobs):

        def with_job(updated, **changes):
            jobs = list(state.jobs)
            jobs[index] = updated
            return replace(state, jobs=tuple(jobs), **changes)

        if job.phase == "running":
            result[f"cancel_{index}"] = with_job(Job("cancelling", True))
        if job.phase in {"running", "cancelling"} and not state.consuming:
            result[f"finish_{index}"] = with_job(
                Job("queued", job.cancelled),
                queue=state.queue + (index,),
                delivered=state.delivered + (index,),
            )
    if state.queue and not state.consuming:
        jobs = list(state.jobs)
        for index in state.queue:
            jobs[index] = replace(jobs[index], phase="consuming")
        result["begin"] = replace(
            state,
            jobs=tuple(jobs),
            consuming=state.queue,
            queue=(),
            batches=state.batches + (state.queue,),
        )
    if state.consuming:
        jobs = list(state.jobs)
        for index in state.consuming:
            jobs[index] = replace(jobs[index], phase="settled")
        result["end"] = replace(
            state,
            jobs=tuple(jobs),
            consumed=state.consumed + state.consuming,
            consuming=(),
        )
    return result


def transition_cover():
    """Enumerate reachable states and shortest witnesses for every edge."""
    initial = TwoJobState()
    pending, paths = deque([initial]), {initial: ()}
    edges, traces = set(), set()
    while pending:
        state = pending.popleft()
        for action, target in successors(state).items():
            trace = paths[state] + (action,)
            edges.add((state, action, target))
            traces.add(trace)
            if target not in paths:
                paths[target] = trace
                pending.append(target)
    prefixes = {trace[:n] for trace in traces for n in range(1, len(trace))}
    return set(paths), edges, sorted(traces - prefixes, key=lambda x: (len(x), x))


class _Work(BaseTool):
    def __init__(self, index):
        super().__init__()
        self.index = index
        self.started, self.release, self.cleanup = (asyncio.Event() for _ in range(3))
        self.config.notify_controller_on_background_complete = True

    @property
    def tool_name(self):
        return f"two_work_{self.index}"

    @property
    def description(self):
        return "A separately controlled background tool."

    @property
    def execution_mode(self):
        return ExecutionMode.BACKGROUND

    async def _execute(self, args, **kwargs):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cleanup.set()
            await self.release.wait()
            raise
        return ToolResult(output=f"TWO_RESULT_{self.index}")


class _Provider(ScriptedLLM):
    def __init__(self):
        super().__init__(["REPLY"])
        self.launched = set()
        self.recording = False
        self.requests = []
        self.entered = asyncio.Queue()
        self.releases = asyncio.Queue()
        self._last_tool_calls = []

    @property
    def last_tool_calls(self):
        return self._last_tool_calls

    async def chat(self, messages, **kwargs):
        normalized = self._normalize_messages(messages)
        self._last_tool_calls = []
        for index in range(2):
            if index not in self.launched and f"launch-two-{index}" in str(
                normalized[-1].get("content")
            ):
                self.launched.add(index)
                self.call_log.append(normalized)
                self.call_count += 1
                self._last_tool_calls = [
                    NativeToolCall(f"two-call-{index}", f"two_work_{index}", "{}")
                ]
                yield ""
                return
        if self.recording:
            self.requests.append(deepcopy(normalized))
            gate = asyncio.Event()
            await self.releases.put(gate)
            await self.entered.put(len(self.requests))
            await gate.wait()
        async for chunk in super().chat(messages, **kwargs):
            yield chunk


async def _wait(awaitable):
    return await asyncio.wait_for(awaitable, 8)


class _Runtime:
    def __init__(self):
        self.tools = [_Work(0), _Work(1)]
        self.provider = _Provider()
        self.jobs = []
        self.consumer_entered = asyncio.Queue()
        self.consumer_releases = asyncio.Queue()
        self.tasks = []
        self.agent = None

    async def prepare(self, engine, tmp_path, patch):
        creature = await engine.add_creature(
            AgentConfig(name="two-model", system_prompt="plain", tool_format="native"),
            llm=self.provider,
            tools=self.tools,
            io="headless",
            pwd=tmp_path,
            session=tmp_path / "two-model.kohakutr",
            start=False,
        )
        self.agent = agent = creature.agent
        original_wait = agent._event_inbox.wait_nonempty

        async def wait_nonempty():
            await original_wait()
            if self.provider.recording:
                gate = asyncio.Event()
                await self.consumer_releases.put(gate)
                await self.consumer_entered.put(True)
                await gate.wait()

        patch.setattr(agent._event_inbox, "wait_nonempty", wait_nonempty)
        await creature.start()
        agent.compact_manager.config.enabled = False
        agent.controller.config.include_job_status = False
        await creature.run("BRANCH_A", timeout=8)
        assert await agent.edit_and_rerun(1, "BRANCH_B")
        for index in range(2):
            await creature.run(f"launch-two-{index}", timeout=8)
            await _wait(self.tools[index].started.wait())
            matching = [
                j
                for j in agent.executor.get_running_jobs()
                if j.type_name == f"two_work_{index}"
            ]
            assert len(matching) == 1
            job_id = matching[0].job_id
            self.jobs.append(job_id)
            self.tasks.append(agent.executor.get_task(job_id))
        assert len(set(self.jobs)) == 2
        assert live_branch_path(agent)[1] == 2
        self.provider.recording = True
        await agent._session_output.drain()

    async def act(self, action, state):
        if action.startswith("cancel_"):
            index = int(action[-1])
            assert await self.agent.executor.cancel(self.jobs[index])
            await _wait(self.tools[index].cleanup.wait())
        elif action.startswith("finish_"):
            index = int(action[-1])
            self.tools[index].release.set()
            await _wait(asyncio.shield(self.tasks[index]))
            if not state.queue:
                await _wait(self.consumer_entered.get())
        elif action == "begin":
            gate = await _wait(self.consumer_releases.get())
            gate.set()
            await _wait(self.provider.entered.get())
        elif action == "end":
            completion = self.agent._turn_completion
            assert completion is not None
            gate = await _wait(self.provider.releases.get())
            gate.set()
            await _wait(asyncio.shield(completion))
            if state.queue:
                await _wait(self.consumer_entered.get())
        else:
            raise AssertionError(action)

    def snapshot(self):
        return deepcopy(
            (
                self.agent.controller.conversation.snapshot_messages(
                    preserve_pending_tail=True
                ),
                live_branch_path(self.agent),
                self.agent.session_store.get_events("two-model"),
                self.provider.call_count,
                len(self.agent._event_inbox),
            )
        )

    async def observe(self, state):
        await self.agent._session_output.drain()
        events = self.agent.session_store.get_events("two-model")
        for index, job in enumerate(state.jobs):
            status = self.agent.executor.get_status(self.jobs[index]).state.value
            expected = (
                "running"
                if job.phase in {"running", "cancelling"}
                else ("cancelled" if job.cancelled else "done")
            )
            assert status == expected, f"job {index} execution outcome"
            result = self.agent.executor.get_result(self.jobs[index])
            assert (result is None) == (expected == "running")
            if result is not None:
                assert (
                    result.cancelled == job.cancelled
                ), f"job {index} cancellation attribution"
                if not job.cancelled:
                    assert (
                        result.output == f"TWO_RESULT_{index}"
                    ), f"job {index} execution payload"
            delivered = job.phase in {"queued", "consuming", "settled"}
            results = [
                e
                for e in events
                if e["type"] == "tool_result" and e.get("call_id") == self.jobs[index]
            ]
            assert len(results) == int(delivered), f"job {index} delivery count"
            if results:
                assert bool(results[0].get("cancelled")) == job.cancelled
                if not job.cancelled:
                    assert (
                        results[0]["output"] == f"TWO_RESULT_{index}"
                    ), f"job {index} persisted payload"
        delivered_events = [
            e
            for e in events
            if e["type"] == "tool_result" and e.get("call_id") in self.jobs
        ]
        assert [e["call_id"] for e in delivered_events] == [
            self.jobs[index] for index in state.delivered
        ], "delivery event order"
        assert len(self.agent._event_inbox) == len(state.queue), "ordered inbox length"
        assert self.agent._processing_lock.locked() == bool(state.consuming)
        feedback = [
            m
            for m in self.agent.controller.conversation.get_messages()
            if m.metadata.get("kind") == "tool_results"
        ]
        flattened = [
            job_id
            for message in feedback
            for job_id in message.metadata.get("job_ids", [])
        ]
        expected_ids = [self.jobs[i] for i in state.consumed + state.consuming]
        assert flattened == expected_ids, "consumed job identity/order"
        assert [m.metadata.get("job_ids", []) for m in feedback] == [
            [self.jobs[index] for index in batch] for batch in state.batches
        ], "consumer batch boundaries"
        assert len(self.provider.requests) == len(
            state.batches
        ), "provider request count"
        for request, batch in zip(self.provider.requests, state.batches):
            text = str(request[-1].get("content"))
            expected_parts = []
            for index in batch:
                if state.jobs[index].cancelled:
                    expected_parts.append(f"[Tool {self.jobs[index]} cancelled]")
                else:
                    expected_parts.append(
                        f"[Tool {self.jobs[index]} completed]\nTWO_RESULT_{index}"
                    )
            assert text == "\n\n".join(expected_parts), "batch result attribution"
            for index in range(2):
                if index not in batch:
                    assert (
                        self.jobs[index] not in text
                        and f"TWO_RESULT_{index}" not in text
                    ), f"job {index} batch result attribution"
        if state.may_switch:
            self.agent._ensure_history_mutation_idle()
        else:
            before = self.snapshot()
            with pytest.raises(ConflictError):
                await self.agent.inject_input("forbidden switch", branch_view={1: 1})
            assert self.snapshot() == before, "rejected switch modified state"

    async def release(self):
        self.provider.recording = False
        for tool in self.tools:
            tool.release.set()
        for queue in (self.consumer_releases, self.provider.releases):
            while not queue.empty():
                queue.get_nowait().set()
        if self.tasks:
            await _wait(asyncio.gather(*self.tasks, return_exceptions=True))
        for queue in (self.consumer_releases, self.provider.releases):
            while not queue.empty():
                queue.get_nowait().set()


async def exercise_mid_turn_fold(
    tmp_path, monkeypatch, first, cancel_second, *, resume=False
):
    """Exercise one completion folded into another completion's active turn."""
    runtime = _Runtime()
    second = 1 - first
    async with Terrarium() as engine:
        with monkeypatch.context() as patch:
            try:
                await runtime.prepare(engine, tmp_path, patch)
                state = TwoJobState()
                await runtime.act(f"finish_{first}", state)
                state = successors(state)[f"finish_{first}"]
                await runtime.observe(state)
                await runtime.act("begin", state)
                state = successors(state)["begin"]
                await runtime.observe(state)
                if cancel_second:
                    await runtime.act(f"cancel_{second}", state)
                    state = successors(state)[f"cancel_{second}"]
                    await runtime.observe(state)
                runtime.tools[second].release.set()
                await _wait(asyncio.shield(runtime.tasks[second]))
                assert runtime.agent._event_inbox.has_event(
                    lambda event: event.job_id == runtime.jobs[second]
                )
                await runtime.agent._session_output.drain()
                before = runtime.snapshot()
                with pytest.raises(ConflictError):
                    await runtime.agent.inject_input(
                        "forbidden switch", branch_view={1: 1}
                    )
                after = runtime.snapshot()
                assert (
                    after == before
                ), f"snapshot differences: {[(i, left, right) for i, (left, right) in enumerate(zip(before, after)) if left != right]}"
                completion = runtime.agent._turn_completion
                assert completion is not None
                gate = await _wait(runtime.provider.releases.get())
                gate.set()
                await _wait(runtime.provider.entered.get())
                assert (
                    runtime.agent._turn_completion is completion
                ), "fold must remain in active turn"
                assert len(runtime.agent._event_inbox) == 0
                request = runtime.provider.requests[-1]
                content = str(request[-1].get("content"))
                expected = (
                    f"[Tool {runtime.jobs[second]} cancelled]"
                    if cancel_second
                    else f"[Tool {runtime.jobs[second]} completed]\nTWO_RESULT_{second}"
                )
                assert content == expected, "folded result attribution"
                all_text = str(request)
                assert all_text.count(f"[Tool {runtime.jobs[first]} completed]") == 1
                assert (
                    sum(str(message.get("content")) == expected for message in request)
                    == 1
                )
                assert len(runtime.provider.requests) == 2
                await runtime.agent._session_output.drain()
                before = runtime.snapshot()
                with pytest.raises(ConflictError):
                    await _wait(
                        runtime.agent.inject_input(
                            "forbidden folded switch", branch_view={1: 1}
                        )
                    )
                after = runtime.snapshot()
                assert (
                    after == before
                ), f"snapshot differences: {[(i, left, right) for i, (left, right) in enumerate(zip(before, after)) if left != right]}"
                gate = await _wait(runtime.provider.releases.get())
                gate.set()
                await _wait(asyncio.shield(completion))
                await runtime.agent._session_output.drain()
                events = runtime.agent.session_store.get_events("two-model")
                results = [
                    event
                    for event in events
                    if event["type"] == "tool_result"
                    and event.get("call_id") in runtime.jobs
                ]
                assert [event["call_id"] for event in results] == [
                    runtime.jobs[first],
                    runtime.jobs[second],
                ]
                assert bool(results[1].get("cancelled")) == cancel_second
                view_b = dict(live_branch_path(runtime.agent))
                runtime.provider.recording = False
                await _wait(
                    runtime.agent.inject_input("allowed switch", branch_view={1: 1})
                )
                request = str(runtime.provider.last_messages)
                assert "BRANCH_A" in request and "BRANCH_B" not in request
                assert not any(
                    marker in request
                    for marker in ("TWO_RESULT_0", "TWO_RESULT_1", *runtime.jobs)
                )
                await _wait(runtime.agent.inject_input("return B", branch_view=view_b))
                request = str(runtime.provider.last_messages)
                assert "BRANCH_A" not in request and "BRANCH_B" in request
                assert request.count(f"TWO_RESULT_{first}") == 1
                second_marker = (
                    f"[Tool {runtime.jobs[second]} cancelled]"
                    if cancel_second
                    else f"TWO_RESULT_{second}"
                )
                assert (
                    request.count(second_marker) == 1
                ), f"restored folded outcome: {runtime.provider.last_messages[1:]} events={[(e.get('type'),e.get('event_id'),e.get('turn_index')) for e in runtime.agent.session_store.get_events('two-model') if e.get('turn_index') == 3]}"
                if resume:
                    await runtime.agent._session_output.drain()
                    expected_messages = deepcopy(
                        runtime.agent.controller.conversation.to_messages()
                    )
                    session_path = runtime.agent.session_store.path
            finally:
                failure = sys.exc_info()[1]
                try:
                    await runtime.release()
                except Exception as cleanup_error:
                    if failure is None:
                        raise
                    failure.args = (
                        f"{failure}\nCleanup also failed: {cleanup_error!r}",
                    )
    if resume:
        provider = ScriptedLLM(["RESTORED"])
        restored, store = resume_agent(session_path, io_mode="plain", llm=provider)
        try:
            await restored.start()
            restored.controller.config.include_job_status = False
            assert restored.controller.conversation.to_messages() == expected_messages
            await _wait(restored.inject_input("after folded resume"))
            request = str(provider.last_messages)
            assert "BRANCH_A" not in request and "BRANCH_B" in request
            assert request.count(f"TWO_RESULT_{first}") == 1
            assert request.count(second_marker) == 1
        finally:
            await restored.stop()
            store.close()


async def exercise_two_job_trace(tmp_path, monkeypatch, trace, *, resume=False):
    """Run a generated two-job witness against the real runtime."""
    runtime, state, prefix = _Runtime(), TwoJobState(), []
    async with Terrarium() as engine:
        with monkeypatch.context() as patch:
            try:
                await runtime.prepare(engine, tmp_path, patch)
                await runtime.observe(state)
                for action in trace:
                    target = successors(state)[action]
                    prefix.append(action)
                    try:
                        await runtime.act(action, state)
                        state = target
                        await runtime.observe(state)
                    except Exception as exc:
                        raise AssertionError(
                            f"two jobs divergent prefix {' -> '.join(prefix)}; expected {target}"
                        ) from exc
                if state.may_switch:
                    view_b = dict(live_branch_path(runtime.agent))
                    runtime.provider.recording = False
                    await runtime.agent.inject_input(
                        "allowed switch", branch_view={1: 1}
                    )
                    request = str(runtime.provider.last_messages)
                    assert "BRANCH_A" in request
                    assert not any(
                        marker in request
                        for marker in (
                            "BRANCH_B",
                            "launch-two-0",
                            "launch-two-1",
                            "TWO_RESULT_0",
                            "TWO_RESULT_1",
                            *runtime.jobs,
                        )
                    ), "branch isolation"
                    if resume:
                        await runtime.agent._session_output.drain()
                        expected = deepcopy(
                            runtime.agent.controller.conversation.to_messages()
                        )
                        session_path = runtime.agent.session_store.path
            finally:
                failure = sys.exc_info()[1]
                try:
                    await runtime.release()
                except Exception as cleanup_error:
                    if failure is None:
                        raise
                    failure.args = (
                        f"{failure}\nCleanup also failed: {cleanup_error!r}",
                    )
    if resume:
        assert state.may_switch, "resume requires a settled witness"
        provider = ScriptedLLM(["RESUMED"])
        restored, store = resume_agent(session_path, io_mode="plain", llm=provider)
        try:
            await restored.start()
            restored.controller.config.include_job_status = False
            assert restored.controller.conversation.to_messages() == expected
            await restored.inject_input("after resume")
            request = str(provider.last_messages)
            assert "BRANCH_A" in request
            assert not any(
                marker in request
                for marker in (
                    "BRANCH_B",
                    "TWO_RESULT_0",
                    "TWO_RESULT_1",
                    *runtime.jobs,
                )
            )
            await restored.inject_input("return to B", branch_view=view_b)
            request = str(provider.last_messages)
            assert "BRANCH_A" not in request and "BRANCH_B" in request
            for index, job in enumerate(state.jobs):
                marker = (
                    f"[Tool {runtime.jobs[index]} cancelled]"
                    if job.cancelled
                    else f"TWO_RESULT_{index}"
                )
                assert (
                    request.count(marker) == 1
                ), f"resumed job {index} outcome: {request}"
            restored_results = [
                (m.metadata.get("job_id"), m.content)
                for m in restored.controller.conversation.get_messages()
                if m.role == "tool" and m.metadata.get("job_id") in runtime.jobs
            ]
            expected_results = [
                (
                    runtime.jobs[index],
                    (
                        f"[Tool {runtime.jobs[index]} cancelled]"
                        if state.jobs[index].cancelled
                        else f"TWO_RESULT_{index}"
                    ),
                )
                for index in range(2)
            ]
            assert (
                restored_results == expected_results
            ), "restored tool results and ownership"
        finally:
            await restored.stop()
            store.close()
