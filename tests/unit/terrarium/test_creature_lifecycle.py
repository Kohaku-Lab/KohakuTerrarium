"""Creature-lifecycle tests for the Terrarium engine.

Exercises ``Terrarium.add_creature`` / ``remove_creature`` /
``get_creature`` / ``list_creatures`` / pythonic accessors / status /
shutdown / context manager / ``with_creature``.

Uses a tiny in-test ``_FakeAgent`` so we don't pay for real LLM init —
the engine layer only touches a handful of agent attributes
(``start``, ``stop``, ``is_running``, ``set_output_handler``,
``inject_input``, ``_drive_input``) plus the ones
``Creature.get_status`` reads.
"""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.engine import Terrarium

# ---------------------------------------------------------------------------
# fake agent — minimal shape Creature touches
# ---------------------------------------------------------------------------


class _FakeAgent:
    """Stands in for ``core.agent.Agent`` in lifecycle tests.

    Provides only what ``Creature`` reaches into during start / stop /
    status / chat plumbing — *not* a runnable agent.  End-to-end tests
    use real agents.
    """

    def __init__(self, name: str = "fake", model: str = "test/model"):
        self.is_running = False
        self.config = SimpleNamespace(name=name, model=model, pwd=None)
        self.llm = SimpleNamespace(
            model=model,
            provider="test",
            api_key_env="",
            base_url="",
            _profile_max_context=8000,
        )
        self.compact_manager = None
        self.session_store = None
        self.executor = None
        self.tools: list[Any] = []
        self.subagents: list[Any] = []
        self._processing_task = None
        self.output_handlers: list[Any] = []
        self.injected: list[tuple[Any, str]] = []
        self.start_calls = 0
        self.stop_calls = 0
        self.drive_input_calls = 0
        self._drive_input_started: asyncio.Event = asyncio.Event()
        self._drive_input_stop: asyncio.Event = asyncio.Event()

    def set_output_handler(self, handler: Any, replace_default: bool = False):
        self.output_handlers.append(handler)

    def llm_identifier(self) -> str:
        return "test/model"

    async def start(self) -> None:
        self.is_running = True
        self.start_calls += 1
        # Reset the gate every start so the fake survives stop/start cycles.
        self._drive_input_stop.clear()
        self._drive_input_started.clear()

    async def stop(self) -> None:
        self.is_running = False
        self.stop_calls += 1
        # Unblock the background driver so ``Creature.stop`` can reap it.
        self._drive_input_stop.set()

    async def _drive_input(self) -> None:
        """Stand-in for ``Agent._drive_input`` — block until stop is set."""
        self.drive_input_calls += 1
        self._drive_input_started.set()
        await self._drive_input_stop.wait()

    async def inject_input(self, message, *, source: str = "chat") -> None:
        self.injected.append((message, source))


def _make_creature(name: str = "fake", **agent_kwargs) -> Creature:
    """Build a Creature wrapping a fake agent."""
    agent = _FakeAgent(name=name, **agent_kwargs)
    return Creature(creature_id=name, name=name, agent=agent)


# ---------------------------------------------------------------------------
# add / get / list / remove
# ---------------------------------------------------------------------------


class TestAddCreature:
    @pytest.mark.asyncio
    async def test_add_pre_built_creature(self):
        engine = Terrarium()
        c = _make_creature("alice")
        added = await engine.add_creature(c)
        assert added is c
        assert "alice" in engine
        assert len(engine) == 1
        assert added.graph_id  # assigned a graph
        assert added.is_running  # auto-start

    @pytest.mark.asyncio
    async def test_add_with_start_false(self):
        engine = Terrarium()
        c = _make_creature("alice")
        added = await engine.add_creature(c, start=False)
        assert not added.is_running
        assert added.agent.start_calls == 0

    @pytest.mark.asyncio
    async def test_duplicate_id_rejected(self):
        engine = Terrarium()
        await engine.add_creature(_make_creature("alice"))
        with pytest.raises(ValueError):
            await engine.add_creature(_make_creature("alice"))

    @pytest.mark.asyncio
    async def test_two_creatures_get_different_graphs_by_default(self):
        engine = Terrarium()
        a = await engine.add_creature(_make_creature("alice"))
        b = await engine.add_creature(_make_creature("bob"))
        assert a.graph_id != b.graph_id
        assert len(engine.list_graphs()) == 2

    @pytest.mark.asyncio
    async def test_add_to_existing_graph(self):
        engine = Terrarium()
        a = await engine.add_creature(_make_creature("alice"))
        b = await engine.add_creature(_make_creature("bob"), graph=a.graph_id)
        assert a.graph_id == b.graph_id
        assert len(engine.list_graphs()) == 1


class TestGetAndListCreatures:
    @pytest.mark.asyncio
    async def test_get_creature_returns_added(self):
        engine = Terrarium()
        c = await engine.add_creature(_make_creature("alice"))
        assert engine.get_creature("alice") is c

    @pytest.mark.asyncio
    async def test_get_unknown_raises(self):
        engine = Terrarium()
        with pytest.raises(KeyError):
            engine.get_creature("ghost")

    @pytest.mark.asyncio
    async def test_list_creatures_returns_all(self):
        engine = Terrarium()
        await engine.add_creature(_make_creature("alice"))
        await engine.add_creature(_make_creature("bob"))
        names = {c.name for c in engine.list_creatures()}
        assert names == {"alice", "bob"}


class TestRemoveCreature:
    @pytest.mark.asyncio
    async def test_remove_stops_and_drops(self):
        engine = Terrarium()
        c = await engine.add_creature(_make_creature("alice"))
        await engine.remove_creature("alice")
        assert "alice" not in engine
        assert len(engine) == 0
        assert c.agent.stop_calls == 1
        assert not c.is_running
        # graph dropped along with its only creature
        assert engine.list_graphs() == []

    @pytest.mark.asyncio
    async def test_remove_unknown_raises(self):
        engine = Terrarium()
        with pytest.raises(KeyError):
            await engine.remove_creature("ghost")

    @pytest.mark.asyncio
    async def test_remove_accepts_creature_handle(self):
        engine = Terrarium()
        c = await engine.add_creature(_make_creature("alice"))
        await engine.remove_creature(c)
        assert "alice" not in engine


# ---------------------------------------------------------------------------
# pythonic accessors
# ---------------------------------------------------------------------------


class TestPythonicAccessors:
    @pytest.mark.asyncio
    async def test_getitem(self):
        engine = Terrarium()
        c = await engine.add_creature(_make_creature("alice"))
        assert engine["alice"] is c

    @pytest.mark.asyncio
    async def test_contains(self):
        engine = Terrarium()
        await engine.add_creature(_make_creature("alice"))
        assert "alice" in engine
        assert "bob" not in engine

    @pytest.mark.asyncio
    async def test_iter(self):
        engine = Terrarium()
        await engine.add_creature(_make_creature("alice"))
        await engine.add_creature(_make_creature("bob"))
        assert sorted(c.name for c in engine) == ["alice", "bob"]

    @pytest.mark.asyncio
    async def test_len(self):
        engine = Terrarium()
        assert len(engine) == 0
        await engine.add_creature(_make_creature("alice"))
        assert len(engine) == 1


# ---------------------------------------------------------------------------
# start / stop / shutdown
# ---------------------------------------------------------------------------


class TestStartStopShutdown:
    @pytest.mark.asyncio
    async def test_explicit_start_after_deferred_add(self):
        engine = Terrarium()
        c = await engine.add_creature(_make_creature("alice"), start=False)
        await engine.start("alice")
        assert c.is_running

    @pytest.mark.asyncio
    async def test_stop_keeps_creature_in_engine(self):
        engine = Terrarium()
        c = await engine.add_creature(_make_creature("alice"))
        await engine.stop(c)
        assert not c.is_running
        assert "alice" in engine  # still listed

    @pytest.mark.asyncio
    async def test_start_drives_input_loop_and_stop_reaps_it(self):
        """Regression for headless configured-IO mode (Discord bot, …).

        ``Creature.start`` must spawn ``Agent._drive_input`` so the
        configured input module's polling loop runs without
        ``Agent.run`` being called. ``Creature.stop`` must reap the
        background task.
        """
        engine = Terrarium()
        c = await engine.add_creature(_make_creature("alice"))
        # Driver task spawned and actually running.
        assert c._input_task is not None
        await asyncio.wait_for(c.agent._drive_input_started.wait(), timeout=1.0)
        assert c.agent.drive_input_calls == 1
        assert not c._input_task.done()
        # Stop reaps the task.
        await engine.stop(c)
        assert c._input_task is None
        assert c.agent.stop_calls == 1

    @pytest.mark.asyncio
    async def test_input_loop_exit_flips_creature_running(self):
        """Done-callback path: when the loop exits on its own (e.g.
        ``exit_requested``) the creature must mark itself stopped so
        external lifecycle drivers (``kt run`` sleep loop) notice."""
        engine = Terrarium()
        c = await engine.add_creature(_make_creature("alice"))
        await asyncio.wait_for(c.agent._drive_input_started.wait(), timeout=1.0)
        # Simulate the input loop exiting on its own (mirrors what
        # ``Agent._drive_input`` does on ``exit_requested`` from CLI / TUI).
        c.agent._drive_input_stop.set()
        for _ in range(50):
            if not c.is_running:
                break
            await asyncio.sleep(0.01)
        assert not c.is_running
        # Subsequent stop is still safe.
        await engine.stop(c)

    @pytest.mark.asyncio
    async def test_shutdown_stops_every_creature(self):
        engine = Terrarium()
        a = await engine.add_creature(_make_creature("alice"))
        b = await engine.add_creature(_make_creature("bob"))
        await engine.shutdown()
        assert a.agent.stop_calls == 1
        assert b.agent.stop_calls == 1
        assert not a.is_running
        assert not b.is_running

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self):
        engine = Terrarium()
        a = await engine.add_creature(_make_creature("alice"))
        await engine.shutdown()
        await engine.shutdown()  # second call must not double-stop
        assert a.agent.stop_calls == 1


# ---------------------------------------------------------------------------
# async context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    @pytest.mark.asyncio
    async def test_aenter_aexit_calls_shutdown(self):
        async with Terrarium() as t:
            c = await t.add_creature(_make_creature("alice"))
            assert c.is_running
        # On exit, every creature has been stopped.
        assert not c.is_running
        assert c.agent.stop_calls == 1

    @pytest.mark.asyncio
    async def test_aexit_runs_on_exception(self):
        c_ref: list[Creature] = []
        with pytest.raises(RuntimeError):
            async with Terrarium() as t:
                c = await t.add_creature(_make_creature("alice"))
                c_ref.append(c)
                raise RuntimeError("boom")
        c = c_ref[0]
        assert c.agent.stop_calls == 1


# ---------------------------------------------------------------------------
# with_creature classmethod
# ---------------------------------------------------------------------------


class TestWithCreature:
    @pytest.mark.asyncio
    async def test_with_creature_returns_engine_and_creature(self):
        c = _make_creature("alice")
        engine, creature = await Terrarium.with_creature(c)
        try:
            assert engine["alice"] is creature
            assert creature.is_running
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# status — verify dict shape preserves frontend expectations
# ---------------------------------------------------------------------------


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_per_creature(self):
        engine = Terrarium()
        await engine.add_creature(_make_creature("alice"))
        s = engine.status("alice")
        assert s["agent_id"] == "alice"
        assert s["creature_id"] == "alice"
        assert s["name"] == "alice"
        assert s["model"] == "test/model"
        assert s["running"] is True
        # graph_id assigned
        assert s["graph_id"]

    @pytest.mark.asyncio
    async def test_status_rollup(self):
        engine = Terrarium()
        await engine.add_creature(_make_creature("alice"))
        await engine.add_creature(_make_creature("bob"))
        s = engine.status()
        assert s["running"] is True
        assert set(s["creatures"]) == {"alice", "bob"}
        # two singleton graphs
        assert len(s["graphs"]) == 2

    @pytest.mark.asyncio
    async def test_status_unknown_creature_raises(self):
        engine = Terrarium()
        with pytest.raises(KeyError):
            engine.status("ghost")


# ---------------------------------------------------------------------------
# graphs roll-up
# ---------------------------------------------------------------------------


class TestGraphs:
    @pytest.mark.asyncio
    async def test_singleton_graph_per_solo_creature(self):
        engine = Terrarium()
        await engine.add_creature(_make_creature("alice"))
        graphs = engine.list_graphs()
        assert len(graphs) == 1
        assert graphs[0].creature_ids == {"alice"}

    @pytest.mark.asyncio
    async def test_get_graph_lookup(self):
        engine = Terrarium()
        c = await engine.add_creature(_make_creature("alice"))
        g = engine.get_graph(c.graph_id)
        assert g.graph_id == c.graph_id
        assert "alice" in g.creature_ids

    @pytest.mark.asyncio
    async def test_get_graph_unknown_raises(self):
        engine = Terrarium()
        with pytest.raises(KeyError):
            engine.get_graph("ghost-graph")
