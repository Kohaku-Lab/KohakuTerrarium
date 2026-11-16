"""Finite history-admission model and real-runtime conformance driver."""

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
from kohakuterrarium.core.conversation.continuation import (
    live_branch_path,
    load_persisted_branch_view,
    require_no_running_jobs,
)
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.session.resume.resume import resume_agent


@dataclass(frozen=True)
class HistoryState:
    """Contract state for one executing job and at most one compaction."""

    job: str = "running"
    compact: str = "idle"
    cancel_requested: bool = False

    @property
    def compact_active(self) -> bool:
        return self.compact in {"summarizing", "flushing"}

    @property
    def may_switch(self) -> bool:
        return self.job == "settled" and not self.compact_active

    @property
    def lifecycle(self) -> list[tuple[str, int]]:
        if self.compact == "idle":
            return []
        events = [("compact_start", 1)]
        if self.compact in {"flushing", "complete"}:
            events.append(("compact_complete", 1))
        elif self.compact == "skipped":
            events.append(("compact_skipped", 1))
        return events


def successors(state: HistoryState, launch: str) -> dict[str, HistoryState]:
    """Legal specification transitions, independent of implementation state."""
    transitions = {}
    if state.job == "running":
        transitions["cancel_job"] = replace(
            state, job="cancelling", cancel_requested=True
        )
    if state.job in {"running", "cancelling"}:
        transitions["finish_job"] = replace(
            state, job="queued" if launch == "declared" else "handoff"
        )
    if state.job == "handoff":
        transitions["deliver_job"] = replace(state, job="queued")
    if state.job == "queued":
        transitions["begin_consumption"] = replace(state, job="consuming")
    if state.job == "consuming":
        transitions["end_consumption"] = replace(state, job="settled")
    if state.compact == "idle":
        transitions["start_compact"] = replace(state, compact="summarizing")
        transitions["cancel_before_entry"] = replace(state, compact="skipped")
    if state.compact == "summarizing":
        transitions["apply_summary"] = replace(state, compact="flushing")
        transitions["cancel_compact"] = replace(state, compact="skipped")
    if state.compact == "flushing":
        transitions["finish_flush"] = replace(state, compact="complete")
        transitions["cancel_compact"] = replace(state, compact="complete")
    return transitions


def transition_cover(
    launch: str,
) -> tuple[
    set[HistoryState],
    set[tuple[HistoryState, str, HistoryState]],
    list[tuple[str, ...]],
]:
    """Return reachable states, edges, and shortest edge witnesses without prefixes."""
    initial = HistoryState()
    pending, paths = deque([initial]), {initial: ()}
    edges, witnesses = set(), set()
    while pending:
        source = pending.popleft()
        for action, target in successors(source, launch).items():
            trace = paths[source] + (action,)
            edges.add((source, action, target))
            witnesses.add(trace)
            if target not in paths:
                paths[target] = trace
                pending.append(target)
    prefixes = {trace[:n] for trace in witnesses for n in range(1, len(trace))}
    return set(paths), edges, sorted(witnesses - prefixes, key=lambda t: (len(t), t))


class _Work(BaseTool):
    def __init__(self, launch, suppress_cancel):
        super().__init__()
        self.launch = launch
        self.suppress_cancel = suppress_cancel
        self.started, self.release, self.cleanup = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        self.config.notify_controller_on_background_complete = True

    @property
    def tool_name(self):
        return "model_work"

    @property
    def description(self):
        return "A tool with controlled execution and cancellation cleanup."

    @property
    def execution_mode(self):
        return (
            ExecutionMode.BACKGROUND
            if self.launch == "declared"
            else ExecutionMode.DIRECT
        )

    async def _execute(self, args, **kwargs):
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cleanup.set()
            await self.release.wait()
            if not self.suppress_cancel:
                raise
        return ToolResult(output="RESULT_BRANCH_B")


class _Provider(ScriptedLLM):
    def __init__(self, launch):
        super().__init__(["MODEL_REPLY"])
        self.launch = launch
        self.launched = False
        self.hold_reply = False
        self.record_completion = False
        self.reply_entered, self.reply_release = asyncio.Event(), asyncio.Event()
        self.completion_requests = []
        self._last_tool_calls = []

    @property
    def last_tool_calls(self):
        return self._last_tool_calls

    async def chat(self, messages, **kwargs):
        normalized = self._normalize_messages(messages)
        self._last_tool_calls = []
        if not self.launched and "launch-model-job" in str(
            normalized[-1].get("content")
        ):
            self.launched = True
            self.call_log.append(normalized)
            self.call_count += 1
            args = '{"run_in_background":true}' if self.launch == "requested" else "{}"
            self._last_tool_calls = [NativeToolCall("model-call", "model_work", args)]
            yield ""
            return
        if self.record_completion:
            self.completion_requests.append(deepcopy(normalized))
        if self.hold_reply:
            self.reply_entered.set()
            await self.reply_release.wait()
            self.hold_reply = False
        async for chunk in super().chat(messages, **kwargs):
            yield chunk


class _Summarizer(ScriptedLLM):
    def __init__(self):
        super().__init__(["SUMMARY_BRANCH_B"])
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def chat(self, messages, **kwargs):
        self.entered.set()
        await self.release.wait()
        async for chunk in super().chat(messages, **kwargs):
            yield chunk


async def _wait(awaitable):
    return await asyncio.wait_for(awaitable, 8)


class _Runtime:
    def __init__(self, launch, suppress_cancel):
        self.launch = launch
        self.tool = _Work(launch, suppress_cancel)
        self.provider, self.summarizer = _Provider(launch), _Summarizer()
        self.handoff, self.delivery = asyncio.Event(), asyncio.Event()
        self.inbox_entered, self.inbox_release = asyncio.Event(), asyncio.Event()
        self.flush_entered, self.flush_release = asyncio.Event(), asyncio.Event()
        self.job_id = None
        self.tasks = []
        self.agent = None
        self.compact_task = None
        self.flush_seen = False
        self.released = False

    async def prepare(self, engine, tmp_path, patch):
        creature = await engine.add_creature(
            AgentConfig(name="model", system_prompt="plain", tool_format="native"),
            llm=self.provider,
            tools=[self.tool],
            io="headless",
            pwd=tmp_path,
            session=tmp_path / "model.kohakutr",
            start=False,
        )
        agent = self.agent = creature.agent
        original_wait = agent._event_inbox.wait_nonempty
        original_handoff = agent._on_backgroundify_complete
        original_run = agent.session_store.run

        async def wait_nonempty():
            await original_wait()
            if self.job_id is not None and agent._event_inbox.has_event(
                lambda e: e.job_id == self.job_id
            ):
                self.inbox_entered.set()
                await self.inbox_release.wait()

        async def handoff(job_id, result):
            self.handoff.set()
            await self.delivery.wait()
            await original_handoff(job_id, result)

        async def store_run(function, *args, **kwargs):
            if (
                asyncio.current_task() is manager._compact_task
                and manager._compact_count == 1
                and not self.flush_seen
            ):
                self.flush_seen = True
                self.flush_entered.set()
                await self.flush_release.wait()
            return await original_run(function, *args, **kwargs)

        patch.setattr(agent._event_inbox, "wait_nonempty", wait_nonempty)
        patch.setattr(agent, "_on_backgroundify_complete", handoff)
        await creature.start()
        manager = self.manager = agent.compact_manager
        manager.config.enabled = False
        manager.config.keep_recent_turns = 2
        manager._llm = self.summarizer
        agent.controller.config.include_job_status = False
        patch.setattr(agent.session_store, "run", store_run)
        await creature.run("BRANCH_A", timeout=8)
        assert await agent.edit_and_rerun(1, "BRANCH_B")
        for text in ("B_MIDDLE", "B_TAIL"):
            await creature.run(text, timeout=8)
        launching = asyncio.create_task(creature.run("launch-model-job", timeout=20))
        self.tasks.append(launching)
        await _wait(self.tool.started.wait())
        if self.launch == "promoted":
            assert next(iter(agent._active_handles.values())).promote()
        await _wait(launching)
        self.job_id = agent.executor.get_running_jobs()[0].job_id
        self.job_task = agent.executor.get_task(self.job_id)
        self.tasks.append(self.job_task)
        await agent.inject_input(
            "same-view while working", branch_view=live_branch_path(agent)
        )
        assert live_branch_path(agent)[1] == 2
        self.provider.hold_reply = True
        self.provider.record_completion = True
        await agent._session_output.drain()

    async def act(self, action):
        match action:
            case "cancel_job":
                assert await self.agent.executor.cancel(self.job_id)
                await _wait(self.tool.cleanup.wait())
            case "finish_job":
                self.tool.release.set()
                await _wait(asyncio.shield(self.job_task))
                await _wait(
                    (
                        self.inbox_entered
                        if self.launch == "declared"
                        else self.handoff
                    ).wait()
                )
            case "deliver_job":
                self.delivery.set()
                await _wait(self.inbox_entered.wait())
            case "begin_consumption":
                self.inbox_release.set()
                await _wait(self.provider.reply_entered.wait())
            case "end_consumption":
                completion = self.agent._turn_completion
                assert completion is not None
                self.provider.reply_release.set()
                await _wait(asyncio.shield(completion))
            case "start_compact" | "cancel_before_entry":
                assert self.manager.trigger_compact()
                self.compact_task = self.manager._compact_task
                self.tasks.append(self.compact_task)
                if action == "cancel_before_entry":
                    await self.manager.cancel()
                else:
                    await _wait(self.summarizer.entered.wait())
            case "apply_summary":
                self.summarizer.release.set()
                await _wait(self.flush_entered.wait())
            case "finish_flush":
                self.flush_release.set()
                await _wait(asyncio.shield(self.compact_task))
            case "cancel_compact":
                await self.manager.cancel()
            case _:
                raise AssertionError(f"Unknown model action: {action}")

    def snapshot(self):
        agent = self.agent
        return deepcopy(
            (
                agent.controller.conversation.snapshot_messages(
                    preserve_pending_tail=True
                ),
                live_branch_path(agent),
                load_persisted_branch_view(agent.session_store, "model"),
                agent.session_store.get_events("model"),
                self.provider.call_count,
                len(agent._event_inbox),
            )
        )

    async def observe(self, state):
        agent = self.agent
        await agent._session_output.drain()
        assert self.manager.is_compacting == state.compact_active
        assert self.manager._compact_count == int(
            state.compact in {"flushing", "complete"}
        )
        events = agent.session_store.get_events("model")
        lifecycle = [
            (e["type"], e["round"])
            for e in events
            if e["type"] in {"compact_start", "compact_complete", "compact_skipped"}
        ]
        assert (
            lifecycle == state.lifecycle
        ), f"compact terminal stream: {lifecycle!r} != {state.lifecycle!r}"
        expected_state = (
            "running"
            if state.job in {"running", "cancelling"}
            else (
                "cancelled"
                if state.cancel_requested and not self.tool.suppress_cancel
                else "done"
            )
        )
        actual_state = agent.executor.get_status(self.job_id).state.value
        assert (
            actual_state == expected_state
        ), f"job execution outcome: {actual_state!r} != {expected_state!r}"
        result = agent.executor.get_result(self.job_id)
        if expected_state == "running":
            assert result is None
        else:
            assert result.cancelled == (expected_state == "cancelled")
        delivered = state.job in {"queued", "consuming", "settled"}
        results = [
            e
            for e in events
            if e["type"] == "tool_result" and e.get("call_id") == self.job_id
        ]
        assert len(results) == int(delivered)
        if results:
            assert bool(results[0].get("cancelled")) == (expected_state == "cancelled")
        assert len(agent._event_inbox) == int(state.job == "queued")
        assert agent._processing_lock.locked() == (state.job == "consuming")
        consumed = state.job in {"consuming", "settled"}
        assert len(self.provider.completion_requests) == int(
            consumed
        ), "completion provider request count"
        feedback = [
            m
            for m in agent.controller.conversation.get_messages()
            if m.metadata.get("kind") == "tool_results"
            and self.job_id in m.metadata.get("job_ids", [])
        ]
        assert len(feedback) == int(
            consumed
        ), "completion must be consumed exactly once"
        if consumed:
            expected_result = (
                f"[Tool {self.job_id} cancelled]"
                if expected_state == "cancelled"
                else "RESULT_BRANCH_B"
            )
            assert expected_result in str(self.provider.completion_requests[0])
        if state.may_switch:
            agent._ensure_history_mutation_idle()
            require_no_running_jobs(agent)
        else:
            before = self.snapshot()
            with pytest.raises(ConflictError):
                await agent.inject_input("forbidden branch switch", branch_view={1: 1})
            assert self.snapshot() == before

    async def release(self):
        if self.released:
            return
        for event in (
            self.tool.release,
            self.delivery,
            self.summarizer.release,
            self.flush_release,
        ):
            event.set()
        if self.tasks:
            await _wait(asyncio.gather(*self.tasks, return_exceptions=True))
        if self.job_id is not None and self.agent.is_running:
            await _wait(self.inbox_entered.wait())
            self.inbox_release.set()
            await _wait(self.provider.reply_entered.wait())
            completion = self.agent._turn_completion
            self.provider.reply_release.set()
            if completion is not None:
                await _wait(asyncio.shield(completion))
            await self.agent._session_output.drain()
        self.released = True


async def exercise_history_trace(
    tmp_path, monkeypatch, launch, trace, *, suppress_cancel=False, resume=False
):
    """Check each generated transition against real collaborators and branch APIs."""
    state, prefix = HistoryState(), []
    runtime = _Runtime(launch, suppress_cancel)
    async with Terrarium() as engine:
        with monkeypatch.context() as patch:
            try:
                await runtime.prepare(engine, tmp_path, patch)
                await runtime.observe(state)
                for action in trace:
                    state = successors(state, launch)[action]
                    prefix.append(action)
                    try:
                        await runtime.act(action)
                        await runtime.observe(state)
                    except Exception as exc:
                        raise AssertionError(
                            f"{launch}: divergent prefix {' -> '.join(prefix)}; expected {state}"
                        ) from exc
                agent = runtime.agent
                await runtime.release()
                settled = replace(
                    state,
                    job="settled",
                    compact="complete" if state.compact_active else state.compact,
                )
                await runtime.observe(settled)
                runtime.provider.record_completion = False
                view_b = dict(live_branch_path(agent))
                await agent.inject_input("allowed branch switch", branch_view={1: 1})
                request = str(runtime.provider.last_messages)
                assert "BRANCH_A" in request
                forbidden = (
                    "BRANCH_B",
                    "B_MIDDLE",
                    "B_TAIL",
                    "launch-model-job",
                    "same-view while working",
                    "RESULT_BRANCH_B",
                    "SUMMARY_BRANCH_B",
                )
                leaked = [text for text in forbidden if text in request]
                assert not leaked, f"branch isolation: leaked {leaked!r}"
                if resume:
                    await agent.inject_input("return to B", branch_view=view_b)
                    await agent._session_output.drain()
                    expected = deepcopy(agent.controller.conversation.to_messages())
                    session_path = agent.session_store.path
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
        llm = ScriptedLLM(["RESUMED"])
        restored, store = resume_agent(session_path, io_mode="plain", llm=llm)
        try:
            await restored.start()
            restored.controller.config.include_job_status = False
            assert restored.controller.conversation.to_messages() == expected
            await restored.inject_input("after resume")
            assert "BRANCH_A" not in str(llm.last_messages)
            assert restored.compact_rounds == runtime.manager._compact_count
        finally:
            await restored.stop()
            store.close()
