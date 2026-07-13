"""Unit tests for :mod:`kohakuterrarium.terrarium.engine`.

We exercise the Terrarium engine using ``TestTerrariumBuilder`` to
populate it with ``_FakeAgent``-backed creatures. No real LLM or
session store is involved.
"""

import asyncio

import pytest

from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.events import EventFilter, EventKind
from kohakuterrarium.testing.terrarium import _FakeAgent, TestTerrariumBuilder


async def _wait_true(predicate, *, timeout: float = 5.0) -> None:
    """Yield to the loop until ``predicate()`` holds (barrier-gated async work)."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(0.005)


# ── construction / context manager ─────────────────────────────


class TestConstruction:
    def test_default_state(self):
        t = Terrarium()
        assert t._creatures == {}
        assert t._running is True

    async def test_async_context_manager(self):
        t = Terrarium()
        async with t as out:
            assert out is t
            assert t._running is True
        # __aexit__ runs shutdown(): the engine is no longer running.
        assert t._running is False


# ── add_creature / remove_creature ─────────────────────────────


class TestAddRemoveCreature:
    async def test_add_then_remove(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            assert "alice" in t
            assert len(t) == 1
            # alice lives in exactly one singleton graph.
            graphs = t.list_graphs()
            assert len(graphs) == 1
            assert graphs[0].creature_ids == {"alice"}
            await t.remove_creature("alice")
            assert "alice" not in t
            # Removing the only creature drops its graph entirely.
            assert t.list_graphs() == []
        finally:
            await t.shutdown()

    async def test_remove_unknown_raises(self):
        t = Terrarium()
        with pytest.raises(KeyError):
            await t.remove_creature("ghost")

    async def test_get_creature(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            c = t.get_creature("alice")
            assert c.creature_id == "alice"
        finally:
            await t.shutdown()

    async def test_get_creature_missing(self):
        t = Terrarium()
        with pytest.raises(KeyError):
            t.get_creature("ghost")

    async def test_list_creatures(self):
        t = (
            await TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .build()
        )
        try:
            out = t.list_creatures()
            names = {c.creature_id for c in out}
            assert names == {"alice", "bob"}
        finally:
            await t.shutdown()

    async def test_dunder_dict_protocols(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            # __getitem__ works
            assert t["alice"].creature_id == "alice"
            # __contains__
            assert "alice" in t
            assert "ghost" not in t
            # __iter__
            ids = [c.creature_id for c in t]
            assert "alice" in ids
            # __len__
            assert len(t) == 1
        finally:
            await t.shutdown()


# ── channels ───────────────────────────────────────────────────


class TestChannels:
    async def test_add_channel(self):
        t = (
            await TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat")
            .build()
        )
        try:
            graphs = t.list_graphs()
            assert len(graphs) == 1
            assert "chat" in graphs[0].channels
        finally:
            await t.shutdown()

    async def test_add_channel_to_unknown_graph(self):
        t = Terrarium()
        with pytest.raises(KeyError):
            await t.add_channel("ghost", "ch")

    async def test_remove_channel(self):
        t = (
            await TestTerrariumBuilder()
            .with_creature("alice")
            .with_channel("temp")
            .build()
        )
        try:
            graphs = t.list_graphs()
            gid = graphs[0].graph_id
            delta = await t.remove_channel(gid, "temp")
            # Unused channel in a singleton graph → no split.
            assert delta.kind == "nothing"
            assert "temp" not in t.get_graph(gid).channels
        finally:
            await t.shutdown()


# ── connect / disconnect ───────────────────────────────────────


class TestConnectDisconnect:
    async def test_connect_within_graph(self):
        t = (
            await TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_channel("chat")
            .build()
        )
        try:
            result = await t.connect("alice", "bob", channel="chat")
            assert result.channel == "chat"
            # Same graph already → no merge.
            assert result.delta_kind == "nothing"
            # The wiring is actually recorded in topology: alice sends,
            # bob listens, on the "chat" channel.
            graph = t.get_graph(t.get_creature("alice").graph_id)
            assert "chat" in graph.send_edges["alice"]
            assert "chat" in graph.listen_edges["bob"]
        finally:
            await t.shutdown()

    async def test_disconnect(self):
        t = (
            await TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_connection("alice", "bob", channel="chat")
            .build()
        )
        try:
            result = await t.disconnect("alice", "bob", channel="chat")
            assert "chat" in result.channels
            # "chat" was the only bridge → graph splits in two.
            assert result.delta_kind == "split"
            assert t.get_creature("alice").graph_id != t.get_creature("bob").graph_id
            assert len(t.list_graphs()) == 2
        finally:
            await t.shutdown()


# ── start / stop ───────────────────────────────────────────────


class TestStartStop:
    async def test_start_stop(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            await t.stop("alice")
            assert t.get_creature("alice").agent.is_running is False
            await t.start("alice")
            assert t.get_creature("alice").agent.is_running is True
        finally:
            await t.shutdown()

    async def test_stop_graph(self):
        t = (
            await TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .build()
        )
        try:
            gid = t.list_graphs()[0].graph_id
            await t.stop_graph(gid)
            # Both creatures stopped.
            assert t.get_creature("alice").agent.is_running is False
            assert t.get_creature("bob").agent.is_running is False
        finally:
            await t.shutdown()


# ── status ─────────────────────────────────────────────────────


class TestStatus:
    async def test_single_creature(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            out = t.status("alice")
            # Single-creature shape mirrors Creature.get_status — identity
            # fields must reflect the actual creature, not just "a dict".
            assert out == t.get_creature("alice").get_status()
            assert out["creature_id"] == "alice"
        finally:
            await t.shutdown()

    async def test_rollup(self):
        t = (
            await TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .build()
        )
        try:
            out = t.status()
            assert out["running"] is True
            assert "alice" in out["creatures"]
            assert "bob" in out["creatures"]
            assert len(out["graphs"]) == 1
        finally:
            await t.shutdown()


# ── shutdown ───────────────────────────────────────────────────


class TestShutdown:
    async def test_idempotent(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        await t.shutdown()
        assert t._running is False
        # Second shutdown is a no-op.
        await t.shutdown()
        assert t._running is False


# ── subscribe ──────────────────────────────────────────────────


class TestSubscribe:
    async def test_subscribe_then_emit(self):
        t = Terrarium()
        try:
            received = []

            async def consume():
                async for ev in t.subscribe():
                    received.append(ev)
                    if len(received) >= 1:
                        break

            task = asyncio.create_task(consume())
            await asyncio.sleep(0)
            # Trigger an event by adding a creature.
            agent = _FakeAgent(name="alice")
            creature = Creature(creature_id="alice", name="alice", agent=agent)
            await t.add_creature(creature)
            await asyncio.wait_for(task, timeout=1.0)
            # add_creature emits CREATURE_ADDED first (then STARTED,
            # which this single-event consumer doesn't wait for).
            assert len(received) == 1
            assert received[0].kind == EventKind.CREATURE_ADDED
            assert received[0].creature_id == "alice"
            assert received[0].graph_id == t.get_creature("alice").graph_id
        finally:
            await t.shutdown()

    async def test_add_creature_event_split_added_vs_started(self):
        # E12: ``start=False`` adds used to emit CREATURE_STARTED for an
        # agent that never started.  ADDED always fires; STARTED only
        # for actually-started creatures.
        from kohakuterrarium.terrarium.events import EngineEvent  # noqa: F401

        t = Terrarium()
        try:
            events: list = []
            t._emit = (lambda orig: lambda ev: (events.append(ev), orig(ev)))(t._emit)
            cold = Creature(
                creature_id="cold", name="cold", agent=_FakeAgent(name="cold")
            )
            await t.add_creature(cold, start=False)
            kinds = [ev.kind for ev in events if ev.creature_id == "cold"]
            assert EventKind.CREATURE_ADDED in kinds
            assert EventKind.CREATURE_STARTED not in kinds

            hot = Creature(creature_id="hot", name="hot", agent=_FakeAgent(name="hot"))
            await t.add_creature(hot, start=True)
            kinds = [ev.kind for ev in events if ev.creature_id == "hot"]
            assert kinds == [
                EventKind.CREATURE_ADDED,
                EventKind.CREATURE_STARTED,
            ]
        finally:
            await t.shutdown()

    async def test_add_prebuilt_creature_rejects_build_kwargs(self):
        # Build-time kwargs were silently ignored for pre-built
        # creatures — now a loud error.
        t = Terrarium()
        try:
            pre = Creature(creature_id="pre", name="pre", agent=_FakeAgent(name="pre"))
            with pytest.raises(ValueError, match="pre-built Creature"):
                await t.add_creature(pre, llm="some/selector")
        finally:
            await t.shutdown()

    async def test_subscribe_registers_eagerly(self):
        # E3 fix: events emitted between ``subscribe()`` and the first
        # ``await`` used to be lost (the async generator only registered
        # on first __anext__).
        from kohakuterrarium.terrarium.events import EngineEvent

        t = Terrarium()
        try:
            it = t.subscribe()
            # Emit BEFORE iteration starts.
            t._emit(EngineEvent(kind=EventKind.CHANNEL_MESSAGE, creature_id="pre"))
            ev = await asyncio.wait_for(it.__anext__(), timeout=1.0)
            assert ev.creature_id == "pre"
        finally:
            await t.shutdown()

    async def test_subscribe_terminates_on_shutdown(self):
        # ``async for ev in t.subscribe()`` used to hang forever after
        # shutdown; it now ends cleanly.
        t = Terrarium()
        received = []

        async def consume():
            async for ev in t.subscribe():
                received.append(ev)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        await t.shutdown()
        await asyncio.wait_for(task, timeout=1.0)
        assert task.done()

    async def test_subscribe_with_filter(self):
        from kohakuterrarium.terrarium.events import EngineEvent

        t = Terrarium()
        try:
            received = []

            async def consume():
                async for ev in t.subscribe(
                    EventFilter(kinds={EventKind.CREATURE_STARTED})
                ):
                    received.append(ev)

            task = asyncio.create_task(consume())
            await asyncio.sleep(0)
            # Emit one non-matching and one matching event; the filtered
            # subscriber must only see the matching kind.
            t._emit(EngineEvent(kind=EventKind.CREATURE_STOPPED, creature_id="x"))
            t._emit(EngineEvent(kind=EventKind.CREATURE_STARTED, creature_id="y"))
            await asyncio.sleep(0)
            task.cancel()
            assert [ev.kind for ev in received] == [EventKind.CREATURE_STARTED]
            assert received[0].creature_id == "y"
        finally:
            await t.shutdown()


# ── helpers ────────────────────────────────────────────────────


class TestResolvers:
    async def test_resolve_creature_id_from_str(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            assert t._resolve_creature_id("alice") == "alice"
        finally:
            await t.shutdown()

    async def test_resolve_creature_id_from_handle(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            c = t.get_creature("alice")
            assert t._resolve_creature_id(c) == "alice"
        finally:
            await t.shutdown()

    async def test_resolve_graph_id_from_str(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            gid = t.list_graphs()[0].graph_id
            assert t._resolve_graph_id(gid) == gid
        finally:
            await t.shutdown()

    async def test_resolve_graph_id_from_handle(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            g = t.list_graphs()[0]
            assert t._resolve_graph_id(g) == g.graph_id
        finally:
            await t.shutdown()

    async def test_get_graph_by_id(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            gid = t.list_graphs()[0].graph_id
            assert t.get_graph(gid).graph_id == gid
        finally:
            await t.shutdown()

    async def test_get_graph_unknown(self):
        t = Terrarium()
        with pytest.raises(KeyError):
            t.get_graph("ghost")


# ── attach_session ─────────────────────────────────────────────


class TestAttachSession:
    async def test_attaches_to_creatures(self, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        t = await TestTerrariumBuilder().with_creature("alice").build()
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            gid = t.list_graphs()[0].graph_id
            await t.attach_session(gid, store)
            assert t._session_stores[gid] is store
        finally:
            await t.shutdown()
            store.close()

    async def test_attach_unknown_graph(self, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        t = Terrarium()
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            # No graph yet → resolver returns the same string;
            # the method silently records but won't crash since
            # graph lookup is None-tolerant after recording.
            await t.attach_session("ghost", store)
            assert "ghost" in t._session_stores
        finally:
            await t.shutdown()
            store.close()


# ── apply_recipe ──────────────────────────────────────────────


class TestApplyRecipe:
    async def test_delegates_to_recipe_module(self, monkeypatch):
        captured = {}

        async def fake_apply(
            engine,
            recipe,
            *,
            graph=None,
            pwd=None,
            llm=None,
            strict=True,
            start=True,
            creature_builder=None,
            created_ids=None,
        ):
            captured["recipe"] = recipe
            captured["pwd"] = pwd
            captured["start"] = start
            return None

        from kohakuterrarium.terrarium import engine as engine_mod

        monkeypatch.setattr(engine_mod._recipe, "apply_recipe", fake_apply)
        t = Terrarium()
        try:
            await t.apply_recipe("/some/recipe.yaml", pwd="/cwd")
            assert captured["recipe"] == "/some/recipe.yaml"
            assert captured["pwd"] == "/cwd"
            assert captured["start"] is True
        finally:
            await t.shutdown()


# ── output wiring routing ─────────────────────────────────────


class TestOutputWiring:
    async def test_list_empty_no_config(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            # A freshly-added creature has declared no output-wiring edges.
            assert t.list_output_wiring("alice") == []
        finally:
            await t.shutdown()


# ── environment() / channel() public accessors ─────────────────


class TestEnvironmentChannelAccessors:
    async def test_environment_returns_live_env(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            gid = t.get_creature("alice").graph_id
            env = t.environment(gid)
            # The SAME object the engine wires creatures into — not a
            # copy. (Scripts used to reach engine._environments.)
            assert env is t._environments[gid]
        finally:
            await t.shutdown()

    async def test_environment_unknown_graph_raises(self):
        t = Terrarium()
        with pytest.raises(KeyError):
            t.environment("no_such_graph")

    async def test_channel_returns_live_channel_and_none(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            gid = t.get_creature("alice").graph_id
            await t.add_channel(gid, "tasks")
            ch = t.channel(gid, "tasks")
            assert ch is not None
            assert t.channel(gid, "nonexistent") is None
        finally:
            await t.shutdown()


# ── add_creature(tools=, plugins=) threading ────────────────────


class TestAddCreatureExtensionInjection:
    async def test_tools_reach_the_agent_registry(self, tmp_path):
        from kohakuterrarium import tool
        from kohakuterrarium.core.config_types import (
            AgentConfig,
            InputConfig,
            OutputConfig,
        )
        from kohakuterrarium.testing.llm import ScriptedLLM

        @tool
        def lookup(key: str) -> str:
            """Find a value."""
            return key

        cfg = AgentConfig(
            name="tooled",
            system_prompt="x",
            agent_path=tmp_path,
            input=InputConfig(type="none"),
            output=OutputConfig(type="none"),
            include_hints_in_prompt=False,
        )
        t = Terrarium()
        try:
            creature = await t.add_creature(
                cfg, start=False, llm=ScriptedLLM(["ok"]), tools=[lookup]
            )
            # The injected instance is registered AND in the prompt —
            # the same contract as Agent.build(tools=[...]).
            assert "lookup" in creature.agent.registry.list_tools()
            assert "lookup" in creature.agent._controller_config.system_prompt
        finally:
            await t.shutdown()

    async def test_prebuilt_creature_rejects_tools_kwarg(self):
        from kohakuterrarium import tool

        @tool
        def x() -> str:
            """No-op."""
            return ""

        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            prebuilt = Creature(
                creature_id="bob",
                name="bob",
                agent=_FakeAgent("bob"),
                graph_id="",
            )
            with pytest.raises(ValueError, match="tools"):
                await t.add_creature(prebuilt, tools=[x])
        finally:
            await t.shutdown()


class TestAttachSessionReplace:
    """Re-attaching a store to a graph closes the previous one, releasing
    its native handles + writer lock (otherwise an autosession-minted,
    writer-locked store leaks its lock and blocks later resume)."""

    async def test_replacing_store_closes_previous_and_frees_lock(self, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        t = Terrarium()
        p = tmp_path / "g.kohakutr"
        first = SessionStore(p, writer_lock=True)
        await t.attach_session("g1", first)

        second = SessionStore(p)  # no lock; replaces the first
        await t.attach_session("g1", second)

        # Previous store closed + dropped from the engine map.
        assert first._closed is True
        assert t._session_stores["g1"] is second

        # The replaced store's writer lock was released: a fresh
        # writer-locked open on the same file now succeeds.
        third = SessionStore(p, writer_lock=True)
        third.close()
        second.close()

    async def test_same_store_reattach_is_noop(self, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        t = Terrarium()
        s = SessionStore(tmp_path / "g.kohakutr", writer_lock=True)
        await t.attach_session("g1", s)
        await t.attach_session("g1", s)  # same object — must NOT close it
        assert getattr(s, "_closed", False) is False
        s.close()


# ── Drive runtime (Phase E) ────────────────────────────────────


class TestDriveRuntime:
    """Default-on Drive behavior, explicit opt-out, and shutdown drain."""

    def _enabled(self, **over):
        from kohakuterrarium.terrarium.drive.config import (
            DriveRuntimeConfig,
            default_registrations,
        )

        return dict(
            drive_config=DriveRuntimeConfig(enabled=True, **over),
            drive_registrations=default_registrations(),
        )

    def test_default_engine_has_runtime(self):
        runtime = Terrarium().drives
        assert runtime is not None
        assert [item.descriptor.name for item in runtime.snapshot.entries] == [
            "generic",
            "goal",
        ]

    def test_enabled_empty_registrations_rejected_at_construction(self):
        from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig
        from kohakuterrarium.terrarium.drive.errors import DriveValidationError

        with pytest.raises(DriveValidationError):
            Terrarium(
                drive_config=DriveRuntimeConfig(enabled=True),
                drive_registrations=[],
            )

    def test_disabled_config_builds_no_runtime(self):
        from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig

        assert Terrarium(drive_config=DriveRuntimeConfig(enabled=False)).drives is None

    async def test_default_creature_gets_drive_service(self):
        from kohakuterrarium.terrarium.channels import DRIVE_SERVICE_KEY

        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            env = t._environments[t.get_creature("alice").graph_id]
            assert env.get(DRIVE_SERVICE_KEY) is t.drives
        finally:
            await t.shutdown()

    async def test_drive_enabled_registers_service_and_starts_dispatcher(self):
        from kohakuterrarium.terrarium.channels import DRIVE_SERVICE_KEY

        t = Terrarium(**self._enabled())
        async with t:
            c = Creature(creature_id="w", name="w", agent=_FakeAgent(name="w"))
            await t.add_creature(c)
            env = t._environments[c.graph_id]
            assert env.get(DRIVE_SERVICE_KEY) is t.drives
            # Dispatcher start is barrier-gated (design §6.5): it starts once the
            # creature crosses the restoration barrier (async), not eagerly on add.
            await _wait_true(lambda: t.drives.manager.dispatcher._task is not None)
        # __aexit__ -> shutdown drained + stopped the dispatcher.
        assert t.drives.manager.dispatcher._task is None

    async def test_shutdown_drains_before_stopping_creatures(self):
        t = Terrarium(**self._enabled())
        await t.__aenter__()
        c = Creature(creature_id="w", name="w", agent=_FakeAgent(name="w"))
        await t.add_creature(c)
        # Barrier-gated start (design §6.5): wait for the reconcile to start it.
        await _wait_true(lambda: t.drives.manager.dispatcher._task is not None)
        await t.shutdown()
        assert t.drives.manager.dispatcher._task is None

    async def test_reconfigure_on_disabled_engine_raises(self):
        from kohakuterrarium.terrarium.drive.config import DriveRuntimeConfig

        with pytest.raises(RuntimeError):
            Terrarium(
                drive_config=DriveRuntimeConfig(enabled=False)
            ).reconfigure_drives([])

    async def test_reconfigure_delegates_to_runtime(self):
        from kohakuterrarium.terrarium.drive.config import default_registrations
        from kohakuterrarium.terrarium.drive.runtime import APPLIED_LIVE

        t = Terrarium(**self._enabled())
        async with t:
            # Re-applying the same set is a live no-op-shaped apply.
            assert t.reconfigure_drives(default_registrations()) == APPLIED_LIVE

    async def test_from_recipe_forwards_drive_args(self):
        # Constructor forwarding (design §8.3): the recipe itself stays
        # Drive-unaware, but the engine it builds is Drive-enabled.
        from kohakuterrarium.terrarium.config import TerrariumConfig

        recipe = TerrariumConfig(name="t", creatures=[], channels=[])
        t = await Terrarium.from_recipe(recipe, **self._enabled())
        try:
            assert t.drives is not None
        finally:
            await t.shutdown()

    async def test_with_creature_forwards_drive_args(self):
        c = Creature(creature_id="w", name="w", agent=_FakeAgent(name="w"))
        t, creature = await Terrarium.with_creature(c, **self._enabled())
        try:
            assert t.drives is not None
            # The creature's graph got a manager + started dispatcher.
            assert t.drives.peek_manager(creature.graph_id) is not None
        finally:
            await t.shutdown()

    async def test_two_disconnected_graphs_have_isolated_managers(self):
        # Per-graph partitioning (design §3.1): each disconnected graph owns
        # its own manager + repository.
        t = Terrarium(**self._enabled())
        async with t:
            a = await t.add_creature(
                Creature(creature_id="a", name="a", agent=_FakeAgent(name="a"))
            )
            b = await t.add_creature(
                Creature(creature_id="b", name="b", agent=_FakeAgent(name="b"))
            )
            assert a.graph_id != b.graph_id
            ma = t.drives.peek_manager(a.graph_id)
            mb = t.drives.peek_manager(b.graph_id)
            assert ma is not None and mb is not None
            assert ma is not mb
            assert ma.repository is not mb.repository
