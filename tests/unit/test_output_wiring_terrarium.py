"""Tests for the terrarium output-wiring resolver.

Uses a minimal mock-Agent / mock-CreatureHandle pair so we can exercise
the resolver's target-resolution and dispatch logic without spinning up
a real ``TerrariumRuntime``.
"""

import asyncio
from dataclasses import dataclass, field

import pytest

from kohakuterrarium.core.events import EventType, TriggerEvent
from kohakuterrarium.core.output_wiring import (
    ROOT_TARGET,
    OutputWiringEntry,
)
from kohakuterrarium.terrarium.output_wiring import TerrariumOutputWiringResolver

# ---------------------------------------------------------------------------
# Minimal mocks
# ---------------------------------------------------------------------------


@dataclass
class _MockConfig:
    name: str


@dataclass
class _MockAgent:
    """Just enough of the Agent surface for the resolver to dispatch.

    The resolver touches: ``_running`` (bool), ``_process_event(event)``
    (coroutine), and ``config.name`` (via ``_safe_deliver`` logging).
    """

    name: str
    running: bool = True
    events: list[TriggerEvent] = field(default_factory=list)
    slow_delay: float = 0.0
    raise_on_process: bool = False

    def __post_init__(self) -> None:
        self.config = _MockConfig(name=self.name)

    @property
    def _running(self) -> bool:
        return self.running

    async def _process_event(self, event: TriggerEvent) -> None:
        if self.slow_delay:
            await asyncio.sleep(self.slow_delay)
        if self.raise_on_process:
            raise RuntimeError("boom")
        self.events.append(event)


@dataclass
class _MockHandle:
    name: str
    agent: _MockAgent


def _make_resolver(
    creatures: dict[str, _MockAgent] | None = None,
    root: _MockAgent | None = None,
) -> tuple[TerrariumOutputWiringResolver, dict[str, _MockAgent], _MockAgent | None]:
    creatures = creatures or {}
    handles = {
        name: _MockHandle(name=name, agent=agent) for name, agent in creatures.items()
    }
    resolver = TerrariumOutputWiringResolver(creatures=handles, root_agent=root)
    return resolver, creatures, root


async def _drain_pending_tasks() -> None:
    """Give created dispatch tasks a chance to run to completion."""
    # Yield several times: the dispatch tasks created by emit() run in
    # the same event loop; two round-trips are enough to let them finish.
    for _ in range(5):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class TestTargetResolution:
    @pytest.mark.asyncio
    async def test_creature_to_creature(self):
        coder = _MockAgent(name="coder")
        runner = _MockAgent(name="runner")
        resolver, _, _ = _make_resolver({"coder": coder, "runner": runner})

        await resolver.emit(
            source="coder",
            content="compiled",
            source_event_type="user_input",
            turn_index=1,
            entries=[OutputWiringEntry(to="runner")],
        )
        await _drain_pending_tasks()

        assert len(runner.events) == 1
        event = runner.events[0]
        assert event.type == EventType.CREATURE_OUTPUT
        assert event.content == "compiled"
        assert event.context["source"] == "coder"
        assert event.context["target"] == "runner"
        assert event.context["with_content"] is True
        assert event.prompt_override == "[Output from coder] compiled"

    @pytest.mark.asyncio
    async def test_to_root(self):
        coder = _MockAgent(name="coder")
        root = _MockAgent(name="root")
        resolver, _, _ = _make_resolver({"coder": coder}, root=root)

        await resolver.emit(
            source="coder",
            content="done",
            source_event_type="user_input",
            turn_index=1,
            entries=[OutputWiringEntry(to=ROOT_TARGET, with_content=False)],
        )
        await _drain_pending_tasks()

        assert len(root.events) == 1
        event = root.events[0]
        assert event.context["target"] == "root"
        assert event.context["with_content"] is False
        # with_content=False → content stripped
        assert event.content == ""
        assert event.prompt_override == "[Turn-end from coder]"

    @pytest.mark.asyncio
    async def test_unknown_creature_is_skipped(self):
        coder = _MockAgent(name="coder")
        resolver, _, _ = _make_resolver({"coder": coder})

        await resolver.emit(
            source="coder",
            content="x",
            source_event_type="user_input",
            turn_index=1,
            entries=[OutputWiringEntry(to="nonexistent")],
        )
        await _drain_pending_tasks()

        # Resolver tracked the unknown target for deduplicated warning.
        assert "nonexistent" in resolver._warned_missing

    @pytest.mark.asyncio
    async def test_unknown_logged_once(self):
        coder = _MockAgent(name="coder")
        resolver, _, _ = _make_resolver({"coder": coder})

        for turn in range(3):
            await resolver.emit(
                source="coder",
                content="x",
                source_event_type="user_input",
                turn_index=turn,
                entries=[OutputWiringEntry(to="ghost")],
            )
            await _drain_pending_tasks()

        # The unknown target appears once in the warn-dedup set
        # regardless of how many times we tried to emit to it.
        assert resolver._warned_missing == {"ghost"}

    @pytest.mark.asyncio
    async def test_root_target_without_root(self):
        coder = _MockAgent(name="coder")
        resolver, _, _ = _make_resolver({"coder": coder}, root=None)

        await resolver.emit(
            source="coder",
            content="x",
            source_event_type="user_input",
            turn_index=1,
            entries=[OutputWiringEntry(to=ROOT_TARGET)],
        )
        await _drain_pending_tasks()

        # The magic-string 'root' target was recorded as unresolvable.
        assert ROOT_TARGET in resolver._warned_missing


# ---------------------------------------------------------------------------
# Fan-out and content toggles
# ---------------------------------------------------------------------------


class TestFanOut:
    @pytest.mark.asyncio
    async def test_multiple_targets_each_receive_an_event(self):
        coder = _MockAgent(name="coder")
        a = _MockAgent(name="a")
        b = _MockAgent(name="b")
        c = _MockAgent(name="c")
        resolver, _, _ = _make_resolver({"coder": coder, "a": a, "b": b, "c": c})

        await resolver.emit(
            source="coder",
            content="x",
            source_event_type="user_input",
            turn_index=1,
            entries=[
                OutputWiringEntry(to="a"),
                OutputWiringEntry(to="b"),
                OutputWiringEntry(to="c", with_content=False),
            ],
        )
        await _drain_pending_tasks()

        assert len(a.events) == 1
        assert len(b.events) == 1
        assert len(c.events) == 1
        assert a.events[0].content == "x"
        assert b.events[0].content == "x"
        # with_content=False on c
        assert c.events[0].content == ""

    @pytest.mark.asyncio
    async def test_source_finishes_before_slow_receiver(self):
        """Delivery is fire-and-forget: source's emit() returns promptly
        even when the receiver's _process_event is slow."""
        coder = _MockAgent(name="coder")
        slow = _MockAgent(name="slow", slow_delay=0.5)
        resolver, _, _ = _make_resolver({"coder": coder, "slow": slow})

        loop = asyncio.get_event_loop()
        start = loop.time()
        await resolver.emit(
            source="coder",
            content="x",
            source_event_type="user_input",
            turn_index=1,
            entries=[OutputWiringEntry(to="slow")],
        )
        elapsed = loop.time() - start
        # emit() itself must not block on the slow receiver's 0.5s delay.
        assert elapsed < 0.1

        # The receiver eventually gets it.
        await asyncio.sleep(0.6)
        assert len(slow.events) == 1


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------


class TestResolverRobustness:
    @pytest.mark.asyncio
    async def test_stopped_target_is_skipped(self):
        coder = _MockAgent(name="coder")
        stopped = _MockAgent(name="stopped", running=False)
        resolver, _, _ = _make_resolver({"coder": coder, "stopped": stopped})

        await resolver.emit(
            source="coder",
            content="x",
            source_event_type="user_input",
            turn_index=1,
            entries=[OutputWiringEntry(to="stopped")],
        )
        await _drain_pending_tasks()

        assert stopped.events == []

    @pytest.mark.asyncio
    async def test_receiver_raising_doesnt_break_resolver(self):
        coder = _MockAgent(name="coder")
        broken = _MockAgent(name="broken", raise_on_process=True)
        resolver, _, _ = _make_resolver({"coder": coder, "broken": broken})

        # The key contract: emit() must not raise even when the
        # receiver's _process_event raises.
        await resolver.emit(
            source="coder",
            content="x",
            source_event_type="user_input",
            turn_index=1,
            entries=[OutputWiringEntry(to="broken")],
        )
        await _drain_pending_tasks()
        # If we got here, the resolver swallowed the receiver's error.
        # No state change to check — the resolver intentionally has no
        # error-count surface. Behaviour is: log + continue.

    @pytest.mark.asyncio
    async def test_mixed_entries_with_one_bad(self):
        """A bad entry should not stop good entries from dispatching."""
        coder = _MockAgent(name="coder")
        good = _MockAgent(name="good")
        resolver, _, _ = _make_resolver({"coder": coder, "good": good})

        await resolver.emit(
            source="coder",
            content="x",
            source_event_type="user_input",
            turn_index=1,
            entries=[
                OutputWiringEntry(to="missing"),
                OutputWiringEntry(to="good"),
            ],
        )
        await _drain_pending_tasks()

        assert len(good.events) == 1


# ---------------------------------------------------------------------------
# Prompt rendering on receiver side
# ---------------------------------------------------------------------------


class TestPromptDeliveredToReceiver:
    @pytest.mark.asyncio
    async def test_custom_simple_prompt(self):
        coder = _MockAgent(name="coder")
        runner = _MockAgent(name="runner")
        resolver, _, _ = _make_resolver({"coder": coder, "runner": runner})

        await resolver.emit(
            source="coder",
            content="the code",
            source_event_type="user_input",
            turn_index=42,
            entries=[
                OutputWiringEntry(
                    to="runner",
                    prompt="src={source} turn={turn_index} content={content}",
                )
            ],
        )
        await _drain_pending_tasks()

        assert runner.events[0].prompt_override == (
            "src=coder turn=42 content=the code"
        )

    @pytest.mark.asyncio
    async def test_custom_jinja_prompt(self):
        coder = _MockAgent(name="coder")
        runner = _MockAgent(name="runner")
        resolver, _, _ = _make_resolver({"coder": coder, "runner": runner})

        await resolver.emit(
            source="coder",
            content="ok",
            source_event_type="user_input",
            turn_index=1,
            entries=[
                OutputWiringEntry(
                    to="runner",
                    prompt="{{ source | upper }}: {{ content }}",
                    prompt_format="jinja",
                )
            ],
        )
        await _drain_pending_tasks()

        assert runner.events[0].prompt_override == "CODER: ok"
