"""Coverage tests for ``studio.sessions.wiring`` — secondary output sinks."""

from typing import Any

import pytest

import kohakuterrarium.studio.sessions.wiring as wiring_mod
from kohakuterrarium.modules.output.base import OutputModule
from kohakuterrarium.terrarium.engine import Terrarium

from tests.unit.studio_sessions._fakes import install_fake_creature


class _FakeSink(OutputModule):
    """Minimal ``OutputModule`` for sink-attach tests."""

    async def on_text(self, text: str, **_kwargs: Any) -> None:  # noqa: D401
        pass

    async def on_event(self, event_type: str, payload: dict, **_kwargs: Any) -> None:
        pass


@pytest.mark.asyncio
async def test_wire_output_returns_sink_id():
    engine = Terrarium()
    try:
        c = await install_fake_creature(engine, "alice")
        sink = _FakeSink()
        sink_id = await wiring_mod.wire_output(engine, "alice", sink)
        assert sink_id.startswith("sink_")
        assert sink in c.agent.output_router._secondary_outputs
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_unwire_output_removes_sink():
    engine = Terrarium()
    try:
        c = await install_fake_creature(engine, "alice")
        sink = _FakeSink()
        sink_id = await wiring_mod.wire_output(engine, "alice", sink)
        ok = await wiring_mod.unwire_output(engine, "alice", sink_id)
        assert ok is True
        assert sink not in c.agent.output_router._secondary_outputs
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_unwire_output_unknown_returns_false():
    engine = Terrarium()
    try:
        await install_fake_creature(engine, "alice")
        ok = await wiring_mod.unwire_output(engine, "alice", "sink_ghost")
        assert ok is False
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_wire_output_unknown_creature_raises():
    engine = Terrarium()
    try:
        with pytest.raises(KeyError):
            await wiring_mod.wire_output(engine, "ghost", _FakeSink())
    finally:
        await engine.shutdown()
