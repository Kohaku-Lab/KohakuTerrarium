import asyncio
from types import SimpleNamespace

import pytest

from kohakuterrarium.core.output_wiring import OutputWiringEntry
from kohakuterrarium.terrarium.output_wiring import TerrariumOutputWiringResolver


class FakeAgent:
    def __init__(self, name: str, creature_id: str | None = None) -> None:
        self.config = SimpleNamespace(name=name)
        if creature_id is not None:
            self._creature_id = creature_id
        self._running = True
        self.events = []

    async def _process_event(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_resolver_blocks_self_target_without_explicit_marker():
    agent = FakeAgent("alpha")
    resolver = TerrariumOutputWiringResolver(
        creatures={"alpha": SimpleNamespace(agent=agent)},
        root_agent=None,
    )

    await resolver.emit(
        source="alpha",
        content="loop",
        source_event_type="test",
        turn_index=1,
        entries=[OutputWiringEntry(to="alpha")],
    )

    assert agent.events == []


@pytest.mark.asyncio
async def test_resolver_allows_self_target_with_explicit_marker():
    agent = FakeAgent("alpha")
    resolver = TerrariumOutputWiringResolver(
        creatures={"alpha": SimpleNamespace(agent=agent)},
        root_agent=None,
    )

    await resolver.emit(
        source="alpha",
        content="loop",
        source_event_type="test",
        turn_index=1,
        entries=[OutputWiringEntry(to="alpha", allow_self_trigger=True)],
    )
    await asyncio.sleep(0)

    assert len(agent.events) == 1


@pytest.mark.asyncio
async def test_resolver_blocks_self_target_by_creature_id():
    agent = FakeAgent("alpha", creature_id="alpha_123")
    resolver = TerrariumOutputWiringResolver(
        creatures={"alpha_123": SimpleNamespace(name="alpha", agent=agent)},
        root_agent=None,
    )

    await resolver.emit(
        source="alpha_123",
        content="loop",
        source_event_type="test",
        turn_index=1,
        entries=[OutputWiringEntry(to="alpha")],
    )

    assert agent.events == []


@pytest.mark.asyncio
async def test_resolver_blocks_root_self_target_without_explicit_marker():
    root = FakeAgent("coordinator")
    resolver = TerrariumOutputWiringResolver(creatures={}, root_agent=root)

    await resolver.emit(
        source="coordinator",
        content="loop",
        source_event_type="test",
        turn_index=1,
        entries=[OutputWiringEntry(to="root")],
    )

    assert root.events == []


@pytest.mark.asyncio
async def test_resolver_allows_root_self_target_with_explicit_marker():
    root = FakeAgent("coordinator")
    resolver = TerrariumOutputWiringResolver(creatures={}, root_agent=root)

    await resolver.emit(
        source="coordinator",
        content="loop",
        source_event_type="test",
        turn_index=1,
        entries=[OutputWiringEntry(to="root", allow_self_trigger=True)],
    )
    await asyncio.sleep(0)

    assert len(root.events) == 1
