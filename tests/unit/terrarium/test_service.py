"""Unit tests for :mod:`kohakuterrarium.terrarium.service`.

Exercise the LocalTerrariumService Protocol surface against a real
Terrarium engine populated with ``_FakeAgent`` creatures via
``TestTerrariumBuilder``. No LLM is involved.
"""

import pytest

from kohakuterrarium.terrarium.events import EventFilter, EventKind
from kohakuterrarium.terrarium.service import (
    CreatureInfo,
    LocalTerrariumService,
    TerrariumService,
    creature_to_info,
)
from kohakuterrarium.testing.terrarium import _FakeAgent, TestTerrariumBuilder

# ── helpers ───────────────────────────────────────────────────────


async def _make_service():
    """Build a Terrarium with alice/bob in one graph + a 'chat' channel."""
    engine = await (
        TestTerrariumBuilder()
        .with_creature("alice", responses=["hello!"])
        .with_creature("bob")
        .with_channel("chat")
        .with_connection("alice", "bob", channel="chat")
        .build()
    )
    return LocalTerrariumService(engine)


async def _make_empty_service():
    """Service backed by an empty engine."""
    engine = await TestTerrariumBuilder().build()
    return LocalTerrariumService(engine)


# ── CreatureInfo / creature_to_info ─────────────────────────────


class TestCreatureInfoDataclass:
    def test_frozen(self):
        info = CreatureInfo(
            creature_id="c",
            name="n",
            graph_id="g",
            is_running=True,
            is_privileged=False,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )
        with pytest.raises(Exception):
            info.creature_id = "x"  # type: ignore


class TestCreatureToInfo:
    async def test_snapshot_matches_live(self):
        svc = await _make_service()
        try:
            engine_creature = svc.engine.get_creature("alice")
            info = creature_to_info(engine_creature)
            assert info.creature_id == "alice"
            assert info.is_running is True
        finally:
            await svc.shutdown()


# ── Protocol surface conformance ────────────────────────────────


class TestProtocol:
    async def test_is_runtime_checkable(self):
        svc = await _make_service()
        try:
            assert isinstance(svc, TerrariumService)
        finally:
            await svc.shutdown()


# ── Reads ────────────────────────────────────────────────────────


class TestReadOperations:
    async def test_node_id_default(self):
        svc = await _make_empty_service()
        try:
            assert svc.node_id == "_host"
        finally:
            await svc.shutdown()

    async def test_node_id_custom(self):
        engine = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(engine, node_id="worker-1")
        try:
            assert svc.node_id == "worker-1"
        finally:
            await svc.shutdown()

    async def test_engine_property(self):
        engine = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(engine)
        try:
            # The property exposes the exact engine the service wraps.
            assert svc.engine is engine
        finally:
            await svc.shutdown()

    async def test_list_creatures(self):
        svc = await _make_service()
        try:
            out = await svc.list_creatures()
            assert len(out) == 2
            names = {c.name for c in out}
            assert names == {"alice", "bob"}
        finally:
            await svc.shutdown()

    async def test_get_creature_info(self):
        svc = await _make_service()
        try:
            info = await svc.get_creature_info("alice")
            assert info is not None
            assert info.creature_id == "alice"
        finally:
            await svc.shutdown()

    async def test_get_creature_info_missing(self):
        svc = await _make_service()
        try:
            assert await svc.get_creature_info("ghost") is None
        finally:
            await svc.shutdown()

    async def test_list_graphs(self):
        svc = await _make_service()
        try:
            graphs = await svc.list_graphs()
            assert len(graphs) == 1
        finally:
            await svc.shutdown()

    async def test_get_graph(self):
        svc = await _make_service()
        try:
            graphs = await svc.list_graphs()
            g = await svc.get_graph(graphs[0].graph_id)
            # get_graph returns the same graph list_graphs reported, with
            # both creatures as members.
            assert g.graph_id == graphs[0].graph_id
            assert g.creature_ids == {"alice", "bob"}
        finally:
            await svc.shutdown()

    async def test_get_graph_missing(self):
        svc = await _make_empty_service()
        try:
            assert await svc.get_graph("nope") is None
        finally:
            await svc.shutdown()

    async def test_list_channels(self):
        svc = await _make_service()
        try:
            graphs = await svc.list_graphs()
            channels = await svc.list_channels(graphs[0].graph_id)
            names = {c.name for c in channels}
            assert "chat" in names
        finally:
            await svc.shutdown()

    async def test_list_channels_missing_graph_returns_empty(self):
        svc = await _make_empty_service()
        try:
            assert await svc.list_channels("ghost") == ()
        finally:
            await svc.shutdown()

    async def test_creature_status(self):
        svc = await _make_service()
        try:
            status = await svc.creature_status("alice")
            # creature_status mirrors Creature.get_status for that creature.
            assert status == svc.engine.get_creature("alice").get_status()
            assert status["creature_id"] == "alice"
        finally:
            await svc.shutdown()

    async def test_creature_status_missing(self):
        svc = await _make_service()
        try:
            assert await svc.creature_status("ghost") is None
        finally:
            await svc.shutdown()

    async def test_status_snapshot(self):
        svc = await _make_service()
        try:
            status = await svc.status_snapshot()
            # Roll-up lists every creature plus graph membership.
            assert status["running"] is True
            assert set(status["creatures"]) == {"alice", "bob"}
            assert len(status["graphs"]) == 1
            graph_entry = next(iter(status["graphs"].values()))
            assert graph_entry["creature_ids"] == ["alice", "bob"]
        finally:
            await svc.shutdown()


# ── Lifecycle ────────────────────────────────────────────────────


class TestLifecycle:
    async def test_add_creature_wrong_node_raises(self):
        svc = await _make_empty_service()
        try:
            with pytest.raises(ValueError, match="mismatches"):
                await svc.add_creature(None, on_node="other")
        finally:
            await svc.shutdown()

    async def test_remove_creature(self):
        svc = await _make_service()
        try:
            await svc.remove_creature("alice")
            assert await svc.get_creature_info("alice") is None
        finally:
            await svc.shutdown()

    async def test_start_stop_creature(self):
        svc = await _make_service()
        try:
            await svc.stop_creature("alice")
            assert (await svc.get_creature_info("alice")).is_running is False
            await svc.start_creature("alice")
            assert (await svc.get_creature_info("alice")).is_running is True
        finally:
            await svc.shutdown()


# ── Channel ops ──────────────────────────────────────────────────


class TestChannelOps:
    async def test_add_channel(self):
        svc = await _make_service()
        try:
            graphs = await svc.list_graphs()
            ch = await svc.add_channel(graphs[0].graph_id, "extra", "x")
            assert ch.name == "extra"
            channels = await svc.list_channels(graphs[0].graph_id)
            assert "extra" in {c.name for c in channels}
        finally:
            await svc.shutdown()

    async def test_remove_channel(self):
        svc = await _make_service()
        try:
            graphs = await svc.list_graphs()
            await svc.add_channel(graphs[0].graph_id, "extra")
            delta = await svc.remove_channel(graphs[0].graph_id, "extra")
            # "extra" had no listeners/senders → removal can't split.
            assert delta.kind == "nothing"
            channels = await svc.list_channels(graphs[0].graph_id)
            assert "extra" not in {c.name for c in channels}
        finally:
            await svc.shutdown()


# ── Connect / disconnect ─────────────────────────────────────────


class TestConnectDisconnect:
    async def test_connect_returns_result(self):
        svc = await _make_service()
        try:
            # Add a new creature carol that's in the same graph.
            engine = svc.engine
            from kohakuterrarium.terrarium.creature_host import Creature
            from kohakuterrarium.testing.terrarium import _FakeAgent

            graphs = await svc.list_graphs()
            agent = _FakeAgent(name="carol")
            creature = Creature(creature_id="carol", name="carol", agent=agent)
            await engine.add_creature(creature, graph=graphs[0].graph_id)
            result = await svc.connect("alice", "carol", channel="newch")
            assert result.channel == "newch"
            # Both already share a graph → no merge; edges are recorded.
            assert result.delta_kind == "nothing"
            graph = engine.get_graph(graphs[0].graph_id)
            assert "newch" in graph.send_edges["alice"]
            assert "newch" in graph.listen_edges["carol"]
        finally:
            await svc.shutdown()

    async def test_disconnect(self):
        svc = await _make_service()
        try:
            result = await svc.disconnect("alice", "bob", channel="chat")
            # "chat" was the only bridge → the graph splits and the
            # disconnected channel is reported back.
            assert "chat" in result.channels
            assert result.delta_kind == "split"
            assert (
                svc.engine.get_creature("alice").graph_id
                != svc.engine.get_creature("bob").graph_id
            )
        finally:
            await svc.shutdown()


# ── Per-creature reads ───────────────────────────────────────────


class TestPerCreatureReads:
    async def test_inject_input(self):
        svc = await _make_service()
        try:
            await svc.inject_input("alice", "hello")
            agent = svc.engine.get_creature("alice").agent
            assert agent.injected
        finally:
            await svc.shutdown()


# ── shutdown ─────────────────────────────────────────────────────


class TestShutdown:
    async def test_shutdown_stops_all(self):
        svc = await _make_service()
        await svc.shutdown()
        # After shutdown, creatures are stopped.
        out = await svc.list_creatures()
        for info in out:
            assert info.is_running is False


# ── runtime_graph_snapshot ───────────────────────────────────────


class TestRuntimeGraphSnapshot:
    async def test_returns_dict(self):
        svc = await _make_service()
        try:
            snap = await svc.runtime_graph_snapshot()
            # The snapshot describes the one real graph holding alice+bob.
            assert "version" in snap
            assert len(snap["graphs"]) == 1
            members = {c["creature_id"] for c in snap["graphs"][0]["creatures"]}
            assert members == {"alice", "bob"}
        finally:
            await svc.shutdown()


# ── subscribe ────────────────────────────────────────────────────


class TestSubscribe:
    async def test_returns_async_iterator(self):
        import asyncio

        from kohakuterrarium.terrarium.creature_host import Creature

        svc = await _make_service()
        try:
            received = []

            async def consume():
                async for ev in svc.subscribe():
                    received.append(ev)

            task = asyncio.create_task(consume())
            await asyncio.sleep(0)
            # A real engine mutation flows through the service stream.
            agent = _FakeAgent(name="dave")
            await svc.engine.add_creature(
                Creature(creature_id="dave", name="dave", agent=agent)
            )
            await asyncio.sleep(0)
            task.cancel()
            assert any(
                ev.kind == EventKind.CREATURE_STARTED and ev.creature_id == "dave"
                for ev in received
            )
        finally:
            await svc.shutdown()

    async def test_subscribe_with_filter(self):
        import asyncio

        from kohakuterrarium.terrarium.creature_host import Creature

        svc = await _make_service()
        try:
            received = []

            async def consume():
                async for ev in svc.subscribe(
                    EventFilter(kinds={EventKind.CREATURE_STOPPED})
                ):
                    received.append(ev)

            task = asyncio.create_task(consume())
            await asyncio.sleep(0)
            # add_creature emits CREATURE_STARTED — filtered out.
            agent = _FakeAgent(name="erin")
            await svc.engine.add_creature(
                Creature(creature_id="erin", name="erin", agent=agent)
            )
            # remove_creature emits CREATURE_STOPPED — passes the filter.
            await svc.engine.remove_creature("erin")
            await asyncio.sleep(0)
            task.cancel()
            assert [ev.kind for ev in received] == [EventKind.CREATURE_STOPPED]
            assert received[0].creature_id == "erin"
        finally:
            await svc.shutdown()
