"""Coverage tests for ``studio.sessions.wiring`` runtime output edges."""

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
async def test_wire_output_returns_edge_id():
    engine = Terrarium()
    try:
        c = await install_fake_creature(engine, "alice")
        edge_id = await wiring_mod.wire_output(engine, "alice", "bob")
        assert edge_id.startswith("wire_bob_")
        assert c.agent.config.output_wiring[0].to == "bob"
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_list_output_wiring_returns_edges():
    engine = Terrarium()
    try:
        await install_fake_creature(engine, "alice")
        edge_id = await wiring_mod.wire_output(
            engine,
            "alice",
            {"to": "bob", "with_content": False},
        )
        outputs = wiring_mod.list_output_wiring(engine, "alice")
        assert outputs == [
            {
                "id": edge_id,
                "to": "bob",
                "with_content": False,
                "prompt": None,
                "prompt_format": "simple",
                "allow_self_trigger": False,
            }
        ]
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_unwire_output_removes_edge():
    engine = Terrarium()
    try:
        c = await install_fake_creature(engine, "alice")
        edge_id = await wiring_mod.wire_output(engine, "alice", "bob")
        ok = await wiring_mod.unwire_output(engine, "alice", edge_id)
        assert ok is True
        assert c.agent.config.output_wiring == []
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_unwire_output_unknown_returns_false():
    engine = Terrarium()
    try:
        await install_fake_creature(engine, "alice")
        ok = await wiring_mod.unwire_output(engine, "alice", "wire_ghost")
        assert ok is False
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_wire_output_unknown_creature_raises():
    engine = Terrarium()
    try:
        with pytest.raises(KeyError):
            await wiring_mod.wire_output(engine, "ghost", "bob")
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_wire_output_sink_returns_sink_id():
    engine = Terrarium()
    try:
        c = await install_fake_creature(engine, "alice")
        sink = _FakeSink()
        sink_id = await wiring_mod.wire_output_sink(engine, "alice", sink)
        assert sink_id.startswith("sink_")
        assert sink in c.agent.output_router._secondary_outputs
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_unwire_output_sink_removes_sink():
    engine = Terrarium()
    try:
        c = await install_fake_creature(engine, "alice")
        sink = _FakeSink()
        sink_id = await wiring_mod.wire_output_sink(engine, "alice", sink)
        ok = await wiring_mod.unwire_output_sink(engine, "alice", sink_id)
        assert ok is True
        assert sink not in c.agent.output_router._secondary_outputs
    finally:
        await engine.shutdown()
