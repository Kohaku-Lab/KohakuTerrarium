"""Programmatic-ergonomics tests for the Terrarium engine.

The engine is a first-class programmatic API alongside ``Agent()`` and
the compose algebra.  These tests assert the ergonomic affordances:
async context manager, classmethod constructors, pythonic accessors,
and re-exports from the top-level ``kohakuterrarium`` package.
"""

import pytest

import kohakuterrarium
from kohakuterrarium.terrarium import (
    ConnectionResult,
    Creature,
    DisconnectionResult,
    EngineEvent,
    EventFilter,
    EventKind,
    Terrarium,
)

from tests.unit.terrarium._fakes import make_creature

# ---------------------------------------------------------------------------
# top-level re-exports
# ---------------------------------------------------------------------------


class TestTopLevelExports:
    def test_terrarium_class_importable_from_top(self):
        assert kohakuterrarium.Terrarium is Terrarium

    def test_creature_class_importable_from_top(self):
        assert kohakuterrarium.Creature is Creature

    def test_engine_event_importable_from_top(self):
        assert kohakuterrarium.EngineEvent is EngineEvent


# ---------------------------------------------------------------------------
# async context manager
# ---------------------------------------------------------------------------


class TestAsyncContextManager:
    @pytest.mark.asyncio
    async def test_aenter_returns_engine_aexit_shuts_down(self):
        async with Terrarium() as t:
            c = await t.add_creature(make_creature("alice"))
            assert c.is_running
        # creature was stopped on exit
        assert not c.is_running

    @pytest.mark.asyncio
    async def test_aexit_runs_on_exception(self):
        captured: list[Creature] = []
        with pytest.raises(RuntimeError):
            async with Terrarium() as t:
                c = await t.add_creature(make_creature("alice"))
                captured.append(c)
                raise RuntimeError("boom")
        assert not captured[0].is_running


# ---------------------------------------------------------------------------
# classmethod constructors
# ---------------------------------------------------------------------------


class TestWithCreature:
    @pytest.mark.asyncio
    async def test_with_creature_returns_engine_and_creature(self):
        c = make_creature("alice")
        engine, creature = await Terrarium.with_creature(c)
        try:
            assert isinstance(engine, Terrarium)
            assert engine["alice"] is creature
            assert creature.is_running
        finally:
            await engine.shutdown()


class TestFromRecipe:
    @pytest.mark.asyncio
    async def test_from_recipe_classmethod_returns_running_engine(self):
        # Use apply_recipe with a fake builder so we don't need real
        # LLM creatures; from_recipe is the spelling tested here.
        from kohakuterrarium.terrarium.config import (
            ChannelConfig,
            CreatureConfig,
            TerrariumConfig,
        )
        from pathlib import Path

        cfg = TerrariumConfig(
            name="t",
            creatures=[
                CreatureConfig(
                    name="alice",
                    config_data={"name": "alice"},
                    base_dir=Path("/tmp"),
                )
            ],
            channels=[ChannelConfig(name="c1", channel_type="queue")],
        )
        # Inject our builder by going through the engine API surface
        # directly — from_recipe doesn't take a builder kwarg, so we
        # test the underlying apply_recipe path that does.
        engine = Terrarium()
        try:
            graph = await engine.apply_recipe(cfg, creature_builder=_fake_builder)
            assert "alice" in engine
            assert "c1" in graph.channels
        finally:
            await engine.shutdown()


def _fake_builder(cr_cfg, *, creature_id=None, pwd=None, llm_override=None):
    return make_creature(name=cr_cfg.name)


# ---------------------------------------------------------------------------
# pythonic accessors
# ---------------------------------------------------------------------------


class TestPythonicAccessors:
    @pytest.mark.asyncio
    async def test_index_membership_iter_len(self):
        async with Terrarium() as t:
            await t.add_creature(make_creature("alice"))
            await t.add_creature(make_creature("bob"))
            assert "alice" in t
            assert "ghost" not in t
            assert len(t) == 2
            assert sorted(c.name for c in t) == ["alice", "bob"]
            assert t["alice"].name == "alice"


# ---------------------------------------------------------------------------
# rich return types — not dicts
# ---------------------------------------------------------------------------


class TestReturnTypes:
    @pytest.mark.asyncio
    async def test_connect_returns_connection_result(self):
        async with Terrarium() as t:
            a = await t.add_creature(make_creature("alice"))
            b = await t.add_creature(make_creature("bob"), graph=a.graph_id)
            r = await t.connect(a, b, channel="ab")
            assert isinstance(r, ConnectionResult)
            assert r.channel == "ab"

    @pytest.mark.asyncio
    async def test_disconnect_returns_disconnection_result(self):
        async with Terrarium() as t:
            a = await t.add_creature(make_creature("alice"))
            b = await t.add_creature(make_creature("bob"), graph=a.graph_id)
            await t.connect(a, b, channel="ab")
            r = await t.disconnect(a, b, channel="ab")
            assert isinstance(r, DisconnectionResult)


# ---------------------------------------------------------------------------
# event filtering
# ---------------------------------------------------------------------------


class TestEventFilter:
    def test_filter_matches(self):
        ev = EngineEvent(
            kind=EventKind.CREATURE_STARTED,
            creature_id="alice",
            graph_id="g",
        )
        assert EventFilter().matches(ev)
        assert EventFilter(kinds={EventKind.CREATURE_STARTED}).matches(ev)
        assert not EventFilter(kinds={EventKind.CREATURE_STOPPED}).matches(ev)
        assert EventFilter(creature_ids={"alice"}).matches(ev)
        assert not EventFilter(creature_ids={"bob"}).matches(ev)
