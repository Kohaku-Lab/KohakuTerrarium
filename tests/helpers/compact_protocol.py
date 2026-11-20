"""Two-attempt compaction specification and runtime ownership conformance."""

import asyncio
import sys
from copy import deepcopy
from dataclasses import dataclass

from kohakuterrarium import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.core.config.types import AgentConfig
from kohakuterrarium.session.resume.resume import resume_agent

_ACTIVE = {"created", "summarizing", "flushing"}
_COMPLETE = {"flushing", "complete"}
_KINDS = {"compact_start", "compact_complete", "compact_skipped"}


@dataclass(frozen=True)
class CompactState:
    """Attempt phases independent of successful-compaction round numbers."""

    attempts: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.attempts) and self.attempts[-1] in _ACTIVE

    @property
    def completed(self) -> int:
        return sum(phase in _COMPLETE for phase in self.attempts)

    @property
    def lifecycle(self) -> list[tuple[str, int]]:
        events, completed = [], 0
        for phase in self.attempts:
            events.append(("compact_start", completed + 1))
            if phase in _COMPLETE:
                completed += 1
                events.append(("compact_complete", completed))
            elif phase.startswith("skipped"):
                events.append(("compact_skipped", completed + 1))
        return events


def compact_successors(state: CompactState) -> dict[str, CompactState]:
    """Observable transitions for two attempts on the default cooperative loop."""
    phases = state.attempts
    if not state.active:
        return {"start": CompactState(phases + ("created",))} if len(phases) < 2 else {}
    phase = phases[-1]
    transitions = {}
    if phase == "created":
        transitions["enter"] = CompactState(phases[:-1] + ("summarizing",))
    elif phase == "summarizing":
        transitions["apply"] = CompactState(phases[:-1] + ("flushing",))
    else:
        transitions["flush"] = CompactState(phases[:-1] + ("complete",))
    outcome = {
        "created": "skipped_unentered",
        "summarizing": "skipped_entered",
        "flushing": "complete",
    }[phase]
    ended = phases[:-1] + (outcome,)
    transitions["cancel"] = CompactState(ended)
    if phase != "created":
        transitions["cancel_pair"] = CompactState(ended)
    if len(phases) == 1:
        transitions["cancel_handoff"] = CompactState(ended + ("created",))
        if phase != "created":
            transitions["cancel_pair_handoff"] = CompactState(ended + ("summarizing",))
    return transitions


def compact_traces() -> list[tuple[str, ...]]:
    """Enumerate all complete paths through the bounded acyclic specification."""
    pending, traces = [(CompactState(), ())], []
    while pending:
        state, prefix = pending.pop()
        following = compact_successors(state)
        if not following:
            traces.append(prefix)
        for action, target in following.items():
            pending.append((target, prefix + (action,)))
    return sorted(traces)


class _Attempt:
    def __init__(self, number):
        self.number = number
        self.task = self.lease = None
        self.body_entered = False
        self.entered, self.summary_release = asyncio.Event(), asyncio.Event()
        self.flushing, self.flush_release = asyncio.Event(), asyncio.Event()
        self.provider = ScriptedLLM([f"COMPACT_GENERATION_{number}"])
        self.calls = 0


class _SummaryProvider(ScriptedLLM):
    def __init__(self, attempts):
        super().__init__(["unused"])
        self.attempts = attempts

    async def chat(self, messages, **kwargs):
        attempt = next(a for a in self.attempts if a.task is asyncio.current_task())
        attempt.calls += 1
        attempt.entered.set()
        await attempt.summary_release.wait()
        async for chunk in attempt.provider.chat(messages, **kwargs):
            yield chunk


async def _wait(awaitable):
    return await asyncio.wait_for(awaitable, 8)


class _CompactRuntime:
    def __init__(self):
        self.attempts, self.events, self.cancellers = [], [], []
        self.agent = None
        self.released = False

    async def prepare(self, engine, tmp_path, patch):
        creature = await engine.add_creature(
            AgentConfig(name="compact-model", system_prompt="plain"),
            llm=ScriptedLLM(["answer"]),
            io="headless",
            pwd=tmp_path,
            session=tmp_path / "compact-model.kohakutr",
            start=True,
        )
        self.agent = creature.agent
        self.manager = manager = self.agent.compact_manager
        manager.config.enabled = False
        manager.config.keep_recent_turns = 1
        self.agent.controller.config.include_job_status = False
        manager._llm = _SummaryProvider(self.attempts)
        for text in ("first", "middle", "tail", "latest"):
            await creature.run(text, timeout=8)
        await self.agent._session_output.drain()
        self.original_messages = deepcopy(
            self.agent.controller.conversation.to_messages()
        )
        original_notify = self.agent.output_router.notify_activity
        original_run = self.agent.session_store.run
        original_compact = manager._run_compact

        def notify(kind, message, metadata=None):
            if kind in _KINDS:
                self.events.append((kind, metadata["round"]))
            return original_notify(kind, message, metadata=metadata)

        async def run_compact(lease=None):
            attempt = next(a for a in self.attempts if a.task is asyncio.current_task())
            attempt.body_entered = True
            await original_compact(lease)

        async def store_run(function, *args, **kwargs):
            attempt = next(
                (a for a in self.attempts if a.task is asyncio.current_task()), None
            )
            if attempt and self.events[-1][0] == "compact_complete":
                attempt.flushing.set()
                await attempt.flush_release.wait()
            return await original_run(function, *args, **kwargs)

        patch.setattr(self.agent.output_router, "notify_activity", notify)
        patch.setattr(manager, "_run_compact", run_compact)
        patch.setattr(self.agent.session_store, "run", store_run)

    def start(self):
        attempt = _Attempt(len(self.attempts) + 1)
        self.attempts.append(attempt)
        assert (
            self.manager.trigger_compact()
        ), "next attempt must be admitted after settlement"
        attempt.task, attempt.lease = (
            self.manager._compact_task,
            self.manager._active_lease,
        )
        assert attempt.lease is not None
        if len(self.attempts) == 2:
            assert attempt.lease.token > self.attempts[0].lease.token
        return attempt

    def assert_owner(self, attempt):
        manager = self.manager
        assert manager._compact_task is attempt.task, "active task ownership"
        assert manager._active_lease is attempt.lease, "active lease ownership"
        assert manager.is_compacting, "active single-flight ownership"

    def assert_handoff(self, old, successor):
        assert old.task.done(), "successor requires old execution settlement"
        assert (
            not successor.body_entered
        ), "default-loop handoff must precede successor entry"
        self.assert_owner(successor)

    async def cancel(self, *, pair, handoff):
        old = self.attempts[-1]
        callback_errors = []

        def start_successor(task):
            try:
                assert task is old.task
                successor = self.start()
                self.assert_handoff(old, successor)
            except Exception as exc:
                callback_errors.append(exc)

        if handoff:
            old.task.add_done_callback(start_successor)

        async def second_caller():
            await self.manager.cancel()
            if handoff and not callback_errors:
                self.assert_handoff(old, self.attempts[-1])

        if pair:
            other = asyncio.create_task(second_caller())
            self.cancellers.append(other)
        await self.manager.cancel()
        if callback_errors:
            raise callback_errors[0]
        if handoff:
            self.assert_handoff(old, self.attempts[-1])
        if pair:
            await _wait(asyncio.shield(other))
            if handoff:
                await _wait(self.attempts[-1].entered.wait())

    async def act(self, action):
        match action:
            case "start":
                self.start()
            case "enter":
                await _wait(self.attempts[-1].entered.wait())
            case "apply":
                self.attempts[-1].summary_release.set()
                await _wait(self.attempts[-1].flushing.wait())
            case "flush":
                self.attempts[-1].flush_release.set()
                await _wait(self.manager.wait_for_current())
            case "cancel" | "cancel_pair" | "cancel_handoff" | "cancel_pair_handoff":
                await self.cancel(pair="pair" in action, handoff="handoff" in action)
            case _:
                raise AssertionError(f"Unknown compact action: {action}")

    def observe(self, state):
        manager = self.manager
        assert self.events == state.lifecycle, "attempt terminal/round stream"
        assert manager._compact_count == state.completed, "successful-compaction count"
        assert manager.is_compacting == state.active, "single-flight lifetime"
        assert len(self.attempts) == len(state.attempts)
        for index, (attempt, phase) in enumerate(zip(self.attempts, state.attempts)):
            entered = phase not in {"created", "skipped_unentered"}
            assert attempt.body_entered == entered, "coroutine entry count"
            assert attempt.calls == int(entered), "summary request count"
            assert attempt.task.done() == (phase not in _ACTIVE), "execution settlement"
            if index < len(self.attempts) - 1:
                assert (
                    attempt.lease is not manager._active_lease
                ), "retired lease revived"
        if state.active:
            self.assert_owner(self.attempts[-1])
            snapshot = (
                manager._compact_task,
                manager._active_lease,
                deepcopy(self.events),
                deepcopy(self.agent.controller.conversation.to_messages()),
            )
            assert (
                not manager.trigger_compact()
            ), "busy trigger must not admit a third flight"
            assert snapshot == (
                manager._compact_task,
                manager._active_lease,
                self.events,
                self.agent.controller.conversation.to_messages(),
            ), "busy trigger changed the active attempt"
        else:
            assert manager._compact_task is None, "settled task reference"
            assert manager._active_lease is None, "settled lease reference"
        completed = [
            i + 1 for i, phase in enumerate(state.attempts) if phase in _COMPLETE
        ]
        messages = self.agent.controller.conversation.to_messages()
        expected = self.original_messages
        if completed:
            expected = (
                self.original_messages[:1]
                + [
                    {
                        "role": "assistant",
                        "content": (
                            f"[Previous context summary (compact round {state.completed})]"
                            f"\n\nCOMPACT_GENERATION_{completed[-1]}"
                        ),
                    }
                ]
                + self.original_messages[-2:]
            )
        assert messages == expected, "system/summary/live-tail prompt"

    async def release(self):
        if self.released:
            return
        for attempt in self.attempts:
            attempt.summary_release.set()
            attempt.flush_release.set()
        tasks = [a.task for a in self.attempts if a.task is not None] + self.cancellers
        if tasks:
            await _wait(asyncio.gather(*tasks, return_exceptions=True))
        self.released = True


async def exercise_compact_trace(tmp_path, monkeypatch, trace, *, resume=False):
    """Check an entire two-attempt model path and optionally reopen its session."""
    runtime, state, prefix = _CompactRuntime(), CompactState(), []
    async with Terrarium() as engine:
        with monkeypatch.context() as patch:
            try:
                await runtime.prepare(engine, tmp_path, patch)
                runtime.observe(state)
                for action in trace:
                    state = compact_successors(state)[action]
                    prefix.append(action)
                    try:
                        await runtime.act(action)
                        runtime.observe(state)
                    except Exception as exc:
                        raise AssertionError(
                            f"compact divergent prefix {' -> '.join(prefix)}; expected {state}"
                        ) from exc
                assert not compact_successors(state), "trace must settle both attempts"
                await runtime.manager.cancel()
                runtime.observe(state)
                await runtime.agent._session_output.drain()
                stored = runtime.agent.session_store.get_events("compact-model")
                assert [
                    (e["type"], e["round"]) for e in stored if e["type"] in _KINDS
                ] == state.lifecycle
                if resume:
                    await runtime.agent.run("persist settled generations", timeout=8)
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
        provider = ScriptedLLM(["restored"])
        restored, store = resume_agent(session_path, io_mode="plain", llm=provider)
        try:
            await restored.start()
            restored.controller.config.include_job_status = False
            assert restored.controller.conversation.to_messages() == expected
            assert restored.compact_rounds == state.completed
            assert [
                (e["type"], e["round"])
                for e in store.get_events("compact-model")
                if e["type"] in _KINDS
            ] == state.lifecycle
            await restored.run("after reopen", timeout=8)
            assert provider.last_messages[:-1] == expected
        finally:
            await restored.stop()
            store.close()
