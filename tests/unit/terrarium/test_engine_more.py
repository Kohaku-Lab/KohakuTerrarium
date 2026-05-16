"""Extra branch coverage for :mod:`kohakuterrarium.terrarium.engine`.

Targets the construction classmethods, the additive ``add_creature``
flags, output-wiring passthroughs, and the defensive arms in
``shutdown`` / ``subscribe`` / ``attach_session`` that the happy-path
suite in ``test_engine.py`` does not reach.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from kohakuterrarium.terrarium import engine as engine_mod
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.events import EngineEvent, EventKind
from kohakuterrarium.testing.terrarium import _FakeAgent, TestTerrariumBuilder


def _creature(cid: str, *, privileged: bool = False) -> Creature:
    agent = _FakeAgent(name=cid)
    return Creature(
        creature_id=cid,
        name=cid,
        agent=agent,
        is_privileged=privileged,
    )


# ── construction classmethods ──────────────────────────────────


class TestConstructionClassmethods:
    async def test_from_recipe_delegates(self, monkeypatch):
        captured = {}

        async def fake_apply_recipe(self, recipe, *, pwd=None, **kw):
            captured["recipe"] = recipe
            captured["pwd"] = pwd
            return None

        monkeypatch.setattr(Terrarium, "apply_recipe", fake_apply_recipe)
        t = await Terrarium.from_recipe("/some/recipe.yaml", pwd="/cwd")
        try:
            assert isinstance(t, Terrarium)
            assert captured == {"recipe": "/some/recipe.yaml", "pwd": "/cwd"}
        finally:
            await t.shutdown()

    async def test_resume_delegates(self, monkeypatch):
        captured = {}

        async def fake_resume(engine, store, *, pwd=None, llm_override=None):
            captured["store"] = store
            captured["pwd"] = pwd
            captured["llm_override"] = llm_override
            return "graph-1"

        monkeypatch.setattr(engine_mod._resume, "resume_into_engine", fake_resume)
        t = await Terrarium.resume("s.kohakutr", pwd="/wd", llm_override="gpt")
        try:
            assert isinstance(t, Terrarium)
            assert t._running is True
            assert captured == {
                "store": "s.kohakutr",
                "pwd": "/wd",
                "llm_override": "gpt",
            }
        finally:
            await t.shutdown()

    async def test_adopt_session_delegates(self, monkeypatch):
        captured = {}

        async def fake_resume(engine, store, *, pwd=None, llm_override=None):
            captured["store"] = store
            return "graph-xyz"

        monkeypatch.setattr(engine_mod._resume, "resume_into_engine", fake_resume)
        t = Terrarium()
        try:
            gid = await t.adopt_session("saved.kohakutr", pwd="/p")
            assert gid == "graph-xyz"
            assert captured["store"] == "saved.kohakutr"
        finally:
            await t.shutdown()

    async def test_with_creature_classmethod(self):
        c = _creature("solo")
        t, creature = await Terrarium.with_creature(c)
        try:
            assert isinstance(t, Terrarium)
            assert creature is c
            assert "solo" in t
        finally:
            await t.shutdown()


# ── add_creature additive flags + build path ───────────────────


class TestAddCreatureBranches:
    async def test_build_creature_path(self, monkeypatch):
        built = _creature("built")

        def fake_build(
            config,
            *,
            creature_id=None,
            pwd=None,
            llm_override=None,
            suppress_io=False,
        ):
            assert config == "some-config-path"
            return built

        monkeypatch.setattr(engine_mod, "build_creature", fake_build)
        t = Terrarium()
        try:
            out = await t.add_creature("some-config-path")
            assert out is built
            assert "built" in t
        finally:
            await t.shutdown()

    async def test_creature_id_override(self):
        c = _creature("original")
        t = Terrarium()
        try:
            await t.add_creature(c, creature_id="renamed")
            assert c.creature_id == "renamed"
            assert "renamed" in t
            assert "original" not in t
        finally:
            await t.shutdown()

    async def test_duplicate_creature_id_raises(self):
        # creature_id is the dict key the engine indexes every creature
        # by; a silent overwrite would orphan the first (running) agent.
        # So a duplicate id MUST raise, and the original must survive
        # untouched.
        t = Terrarium()
        try:
            first = await t.add_creature(_creature("dup"))
            with pytest.raises(ValueError, match="already exists"):
                await t.add_creature(_creature("dup"))
            # The original creature is still the one the engine holds.
            assert t.get_creature("dup") is first
            assert len(t) == 1
        finally:
            await t.shutdown()

    async def test_is_privileged_elevation_registers_group_tools(self, monkeypatch):
        registered = []
        monkeypatch.setattr(
            engine_mod,
            "force_register_privileged_tools",
            lambda agent: registered.append(agent),
        )
        c = _creature("priv")
        t = Terrarium()
        try:
            await t.add_creature(c, is_privileged=True)
            assert c.is_privileged is True
            # Privileged creatures get the group_* surface registered.
            assert registered == [c.agent]
        finally:
            await t.shutdown()

    async def test_parent_creature_id_assigned(self):
        c = _creature("child")
        t = Terrarium()
        try:
            await t.add_creature(c, parent_creature_id="parent-1")
            assert c.parent_creature_id == "parent-1"
        finally:
            await t.shutdown()

    async def test_prebuilt_privileged_creature_registers_group_tools(
        self, monkeypatch
    ):
        # Pre-built creature already privileged; add_creature must not
        # demote it and must still register the privileged tools.
        registered = []
        monkeypatch.setattr(
            engine_mod,
            "force_register_privileged_tools",
            lambda agent: registered.append(agent),
        )
        c = _creature("born-priv", privileged=True)
        t = Terrarium()
        try:
            await t.add_creature(c, is_privileged=False)
            assert c.is_privileged is True
            assert registered == [c.agent]
        finally:
            await t.shutdown()


# ── output wiring passthroughs ─────────────────────────────────


class TestOutputWiring:
    async def test_wire_and_unwire_output_edge(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            events = []

            async def consume():
                async for ev in t.subscribe():
                    events.append(ev)

            task = asyncio.create_task(consume())
            await asyncio.sleep(0)

            edge_id = await t.wire_output("alice", "bob")
            # The new edge is actually present in the listing and points
            # at the requested target.
            edges = t.list_output_wiring("alice")
            match = [e for e in edges if e["id"] == edge_id]
            assert len(match) == 1
            assert match[0]["to"] == "bob"

            removed = await t.unwire_output("alice", edge_id)
            assert removed is True
            # The edge is gone after removal.
            assert t.list_output_wiring("alice") == []
            # Removing a non-existent edge returns False, no event.
            assert await t.unwire_output("alice", "wire_missing") is False

            await asyncio.sleep(0)
            task.cancel()
            # Exactly one add + one remove event, each carrying the edge id;
            # the failed removal emitted nothing.
            wire_events = [
                e
                for e in events
                if e.kind
                in (EventKind.OUTPUT_WIRE_ADDED, EventKind.OUTPUT_WIRE_REMOVED)
            ]
            assert [e.kind for e in wire_events] == [
                EventKind.OUTPUT_WIRE_ADDED,
                EventKind.OUTPUT_WIRE_REMOVED,
            ]
            for e in wire_events:
                assert e.creature_id == "alice"
                assert e.payload["edge_id"] == edge_id
        finally:
            await t.shutdown()

    async def test_wire_and_unwire_output_sink(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            sink = MagicMock()
            router = t.get_creature("alice").agent.output_router
            sink_id = await t.wire_output_sink("alice", sink)
            # The sink is actually attached to the creature's router.
            assert sink in router._secondary_outputs
            assert await t.unwire_output_sink("alice", sink_id) is True
            # Removal detaches it from the router.
            assert sink not in router._secondary_outputs
            # Second removal: already gone.
            assert await t.unwire_output_sink("alice", sink_id) is False
        finally:
            await t.shutdown()


# ── apply_recipe llm_override passthrough ──────────────────────


class TestApplyRecipeOverride:
    async def test_llm_override_forwarded(self, monkeypatch):
        captured = {}

        async def fake_apply(engine, recipe, **kwargs):
            captured.update(kwargs)
            return None

        monkeypatch.setattr(engine_mod._recipe, "apply_recipe", fake_apply)
        t = Terrarium()
        try:
            await t.apply_recipe("r.yaml", llm_override="claude")
            assert captured["llm_override"] == "claude"
        finally:
            await t.shutdown()


# ── lifecycle defensive arms ───────────────────────────────────


class TestLifecycleDefensive:
    async def test_stop_graph_unknown_is_noop(self):
        t = Terrarium()
        try:
            # Unknown graph id → silent return, nothing raised.
            await t.stop_graph("ghost-graph")
        finally:
            await t.shutdown()

    async def test_shutdown_early_return_when_idle(self):
        t = Terrarium()
        t._running = False
        # No creatures + not running → the early-return arm.
        await t.shutdown()
        assert t._running is False

    async def test_shutdown_swallows_creature_stop_error(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        c = t.get_creature("alice")

        async def boom():
            raise RuntimeError("stop blew up")

        c.stop = boom
        # Must not propagate — _shutdown_log_warning logs and moves on.
        await t.shutdown()
        assert t._running is False


# ── subscribe defensive arms ───────────────────────────────────


class TestSubscribeDefensive:
    async def test_none_sentinel_ends_iteration(self):
        t = Terrarium()
        try:
            received = []

            async def consume():
                async for ev in t.subscribe():
                    received.append(ev)

            task = asyncio.create_task(consume())
            await asyncio.sleep(0)
            # Push a real event, then the None sentinel to end the loop.
            sub = t._subscribers[0]
            sub.queue.put_nowait(
                EngineEvent(kind=EventKind.CREATURE_STARTED, creature_id="x")
            )
            sub.queue.put_nowait(None)
            await asyncio.wait_for(task, timeout=1.0)
            assert len(received) == 1
        finally:
            await t.shutdown()

    async def test_finally_handles_already_removed_subscriber(self):
        t = Terrarium()
        try:

            async def consume():
                async for _ in t.subscribe():
                    pass

            task = asyncio.create_task(consume())
            await asyncio.sleep(0)
            # Simulate the subscriber being dropped elsewhere — the
            # finally block's remove() must swallow the ValueError.
            t._subscribers.clear()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            await t.shutdown()


# ── attach_session branches ────────────────────────────────────


class TestAttachSessionBranches:
    async def test_retro_wires_channel_persistence(self, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        t = (
            await TestTerrariumBuilder()
            .with_creature("alice")
            .with_channel("chat")
            .build()
        )
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            gid = t.list_graphs()[0].graph_id
            # Channel registered before attach → the retro-wire loop runs.
            await t.attach_session(gid, store)
            assert t._session_stores[gid] is store
            # The pre-existing "chat" channel got persistence wired
            # retroactively — without this, sends before attach vanish.
            env = t._environments[gid]
            chat = env.shared_channels._channels["chat"]
            assert chat._terrarium_graph_id == gid
        finally:
            await t.shutdown()
            store.close()

    async def test_skips_missing_creature_in_topology(self, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        t = await TestTerrariumBuilder().with_creature("alice").build()
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            gid = t.list_graphs()[0].graph_id
            # Drop the creature handle but leave it in the topology so
            # the attach loop hits the ``c is None: continue`` arm.
            t._creatures.pop("alice")
            await t.attach_session(gid, store)
            assert t._session_stores[gid] is store
        finally:
            await t.shutdown()
            store.close()

    async def test_uses_attach_session_store_when_available(self, tmp_path):
        from kohakuterrarium.session.store import SessionStore

        t = await TestTerrariumBuilder().with_creature("alice").build()
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            c = t.get_creature("alice")
            hook = MagicMock()
            c.agent.attach_session_store = hook
            gid = t.list_graphs()[0].graph_id
            await t.attach_session(gid, store)
            hook.assert_called_once_with(store)
        finally:
            await t.shutdown()
            store.close()
