"""Recipe-loader tests for the Terrarium engine.

Uses fake creature builders so we exercise the loader's wiring logic
(channel declaration, auto-direct channels, listen/send edges, root
behaviour) without spinning up real LLM-backed agents.
"""

from pathlib import Path

import pytest

from kohakuterrarium.studio.sessions.lifecycle import find_creature
from kohakuterrarium.terrarium.config import (
    ChannelConfig,
    CreatureConfig,
    RootConfig,
    TerrariumConfig,
)
from kohakuterrarium.terrarium.engine import Terrarium
from tests.unit.terrarium._fakes import make_creature

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _fake_builder(cr_cfg, *, creature_id=None, pwd=None, llm_override=None):
    """Stand-in for ``terrarium.creature_host.build_creature``.

    Accepts ``CreatureConfig`` and returns a ``Creature`` wrapping a
    fake agent.  The recipe loader passes us each creature in the
    config; we mirror their names back so the loader's name-based
    lookups (e.g. ``engine.get_creature(name)``) resolve.
    """
    name = cr_cfg.name
    return make_creature(name=name)


def _basic_config() -> TerrariumConfig:
    """Two creatures, three channels, no root."""
    return TerrariumConfig(
        name="basic",
        creatures=[
            CreatureConfig(
                name="alice",
                config_data={"name": "alice"},
                base_dir=Path("/tmp"),
                listen_channels=["tasks"],
                send_channels=["results"],
            ),
            CreatureConfig(
                name="bob",
                config_data={"name": "bob"},
                base_dir=Path("/tmp"),
                listen_channels=["results", "team"],
                send_channels=["team"],
            ),
        ],
        channels=[
            ChannelConfig(name="tasks", channel_type="queue"),
            ChannelConfig(name="results", channel_type="queue"),
            ChannelConfig(
                name="team", channel_type="broadcast", description="team chat"
            ),
        ],
    )


# ---------------------------------------------------------------------------
# basic shape
# ---------------------------------------------------------------------------


class TestApplyRecipe:
    @pytest.mark.asyncio
    async def test_creatures_and_channels_land_in_one_graph(self):
        engine = Terrarium()
        graph = await engine.apply_recipe(
            _basic_config(), creature_builder=_fake_builder
        )
        # Single graph holding both creatures
        assert graph.creature_ids == {"alice", "bob"}
        assert engine.list_graphs() == [graph]
        # Declared channels + auto-direct channels (one per creature)
        assert {"tasks", "results", "team", "alice", "bob"}.issubset(graph.channels)

    @pytest.mark.asyncio
    async def test_listen_edges_inject_triggers(self):
        engine = Terrarium()
        await engine.apply_recipe(_basic_config(), creature_builder=_fake_builder)
        alice = engine.get_creature("alice")
        bob = engine.get_creature("bob")
        # alice listens on tasks (declared) + alice (auto-direct)
        assert "tasks" in alice.listen_channels
        assert "alice" in alice.listen_channels
        # her trigger_manager has the corresponding triggers
        triggers = alice.agent.trigger_manager._triggers
        assert "channel_alice_tasks" in triggers
        assert "channel_alice_alice" in triggers
        # bob listens on results, team, and his direct channel
        assert {"results", "team", "bob"}.issubset(bob.listen_channels)

    @pytest.mark.asyncio
    async def test_send_edges_recorded(self):
        engine = Terrarium()
        await engine.apply_recipe(_basic_config(), creature_builder=_fake_builder)
        alice = engine.get_creature("alice")
        bob = engine.get_creature("bob")
        assert "results" in alice.send_channels
        assert "team" in bob.send_channels

    @pytest.mark.asyncio
    async def test_creatures_are_started(self):
        engine = Terrarium()
        await engine.apply_recipe(_basic_config(), creature_builder=_fake_builder)
        for c in engine.list_creatures():
            assert c.is_running


class TestRootRecipe:
    @pytest.mark.asyncio
    async def test_root_adds_report_channel_and_send_edge(self):
        cfg = _basic_config()
        cfg = TerrariumConfig(
            name="rooted",
            creatures=cfg.creatures,
            channels=cfg.channels,
            root=RootConfig(config_data={"name": "root"}, base_dir=Path("/tmp")),
        )
        engine = Terrarium()
        graph = await engine.apply_recipe(cfg, creature_builder=_fake_builder)
        # report_to_root channel was auto-declared
        assert "report_to_root" in graph.channels
        # Every non-root creature got it added to send_channels; root listens there.
        for c in engine.list_creatures():
            if c.is_root:
                assert "report_to_root" in c.listen_channels
            else:
                assert "report_to_root" in c.send_channels
        root = engine.get_creature("root")
        assert root.is_root
        assert root.agent.environment is engine._environments[graph.graph_id]
        assert find_creature(engine, graph.graph_id, "root") is root


class TestEmptyRecipe:
    @pytest.mark.asyncio
    async def test_empty_recipe_creates_empty_graph(self):
        cfg = TerrariumConfig(name="empty", creatures=[], channels=[])
        engine = Terrarium()
        graph = await engine.apply_recipe(cfg, creature_builder=_fake_builder)
        assert graph.creature_ids == set()
        assert graph.channels == {}
        # Engine has the graph + an env for it.
        assert graph.graph_id in engine._environments


class TestFromRecipe:
    @pytest.mark.asyncio
    async def test_from_recipe_classmethod(self):
        cfg = _basic_config()
        # Patch the builder used inside apply_recipe by going via the
        # engine instance method — from_recipe doesn't take a builder,
        # so we apply manually via apply_recipe.
        engine = Terrarium()
        await engine.apply_recipe(cfg, creature_builder=_fake_builder)
        assert "alice" in engine
        assert "bob" in engine


# ---------------------------------------------------------------------------
# unknown channels in listen/send are tolerated (legacy behaviour)
# ---------------------------------------------------------------------------


class TestUnknownChannel:
    @pytest.mark.asyncio
    async def test_unknown_listen_channel_skipped(self):
        cfg = TerrariumConfig(
            name="loose",
            creatures=[
                CreatureConfig(
                    name="alice",
                    config_data={"name": "alice"},
                    base_dir=Path("/tmp"),
                    listen_channels=["nonexistent"],
                    send_channels=[],
                ),
            ],
            channels=[],
        )
        engine = Terrarium()
        await engine.apply_recipe(cfg, creature_builder=_fake_builder)
        alice = engine.get_creature("alice")
        # Unknown channel was silently skipped — no trigger injected.
        assert "channel_alice_nonexistent" not in (
            alice.agent.trigger_manager._triggers
        )
