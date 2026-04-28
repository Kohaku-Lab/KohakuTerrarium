"""Coverage tests for ``studio.sessions.topology``.

Exercises ``add_channel`` / ``list_channels`` / ``channel_info`` /
``send_to_channel`` / ``connect`` / ``disconnect`` / ``wire_creature``,
plus the result-to-dict normalisers.

Uses the in-tree fakes — engine layer is real but the agents inside
each creature are stubbed.
"""

from types import SimpleNamespace

import pytest

import kohakuterrarium.studio.sessions.topology as topo_mod
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.topology import ChannelKind, TopologyDelta

from tests.unit.studio_sessions._fakes import install_fake_creature

# ---------------------------------------------------------------------------
# add_channel / list_channels / channel_info
# ---------------------------------------------------------------------------


class TestChannelOps:
    @pytest.mark.asyncio
    async def test_add_channel_queue(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            info = await topo_mod.add_channel(
                engine,
                c.graph_id,
                "tasks",
                channel_type="queue",
                description="task queue",
            )
            assert info["name"] == "tasks"
            assert info["type"] in ("queue", "QUEUE", "ChannelKind.QUEUE")
            assert info["description"] == "task queue"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_add_channel_broadcast(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            info = await topo_mod.add_channel(
                engine, c.graph_id, "team", channel_type="broadcast"
            )
            assert info["name"] == "team"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_list_channels(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            await topo_mod.add_channel(
                engine, c.graph_id, "tasks", channel_type="queue"
            )
            channels = topo_mod.list_channels(engine, c.graph_id)
            names = {ch["name"] for ch in channels}
            assert "tasks" in names
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_list_channels_unknown_session(self):
        engine = Terrarium()
        try:
            with pytest.raises(KeyError):
                topo_mod.list_channels(engine, "ghost")
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_channel_info_hit(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            await topo_mod.add_channel(
                engine, c.graph_id, "tasks", description="task queue"
            )
            info = topo_mod.channel_info(engine, c.graph_id, "tasks")
            assert info is not None
            assert info["name"] == "tasks"
            assert info["scope"] == "shared"
            assert "qsize" in info
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_channel_info_missing_channel(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            assert topo_mod.channel_info(engine, c.graph_id, "ghost") is None
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_channel_info_unknown_session(self):
        engine = Terrarium()
        try:
            with pytest.raises(KeyError):
                topo_mod.channel_info(engine, "ghost", "tasks")
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# send_to_channel
# ---------------------------------------------------------------------------


class TestSendToChannel:
    @pytest.mark.asyncio
    async def test_send_returns_message_id(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            await topo_mod.add_channel(engine, c.graph_id, "tasks")
            mid = await topo_mod.send_to_channel(
                engine, c.graph_id, "tasks", "hello", sender="user"
            )
            assert mid  # non-empty message id
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_send_unknown_session(self):
        engine = Terrarium()
        try:
            with pytest.raises(KeyError):
                await topo_mod.send_to_channel(engine, "ghost", "ch", "hi")
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_send_unknown_channel(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            with pytest.raises(ValueError, match="not found"):
                await topo_mod.send_to_channel(engine, c.graph_id, "ghost", "hi")
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# connect / disconnect / wire_creature
# ---------------------------------------------------------------------------


class TestConnectDisconnect:
    @pytest.mark.asyncio
    async def test_connect_returns_dict(self, monkeypatch):
        engine = Terrarium()
        try:
            await install_fake_creature(engine, "alice")
            await install_fake_creature(engine, "bob")

            async def _fake_connect(self, sender, receiver, *, channel=None, kind=None):
                return SimpleNamespace(
                    channel=channel or "auto",
                    delta=TopologyDelta(
                        kind="merge",
                        old_graph_ids=["g_a", "g_b"],
                        new_graph_ids=["g_a"],
                        affected_creatures={"alice", "bob"},
                    ),
                    graph_id="g_a",
                )

            monkeypatch.setattr(Terrarium, "connect", _fake_connect)
            out = await topo_mod.connect(
                engine, "alice", "bob", channel="tasks", channel_type="queue"
            )
            assert out["channel"] == "tasks"
            assert out["graph_id"] == "g_a"
            assert out["delta"]["kind"] == "merge"
            assert "alice" in out["delta"]["affected"]
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_connect_broadcast_kind(self, monkeypatch):
        captured = {}

        async def _spy_connect(self, sender, receiver, *, channel=None, kind=None):
            captured["kind"] = kind
            return SimpleNamespace(channel="x", delta=None, graph_id="g_a")

        monkeypatch.setattr(Terrarium, "connect", _spy_connect)
        engine = Terrarium()
        try:
            await topo_mod.connect(
                engine, "a", "b", channel="x", channel_type="broadcast"
            )
            assert captured["kind"] == ChannelKind.BROADCAST
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_disconnect_returns_dict(self, monkeypatch):
        async def _fake_disconnect(self, sender, receiver, *, channel=None):
            return SimpleNamespace(
                removed_channel="tasks",
                delta=TopologyDelta(
                    kind="split",
                    old_graph_ids=["g_a"],
                    new_graph_ids=["g_a1", "g_a2"],
                    affected_creatures={"alice", "bob"},
                ),
            )

        monkeypatch.setattr(Terrarium, "disconnect", _fake_disconnect)
        engine = Terrarium()
        try:
            out = await topo_mod.disconnect(engine, "alice", "bob", channel="tasks")
            assert out["removed_channel"] == "tasks"
            assert out["delta"]["kind"] == "split"
            assert "alice" in out["delta"]["affected"]
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# wire_creature
# ---------------------------------------------------------------------------


class TestWireCreature:
    @pytest.mark.asyncio
    async def test_wire_listen(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            await topo_mod.add_channel(engine, c.graph_id, "tasks")
            await topo_mod.wire_creature(engine, c.graph_id, "alice", "tasks", "listen")
            graph = engine.get_graph(c.graph_id)
            assert "tasks" in graph.listen_edges.get("alice", set())
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_wire_send(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            await topo_mod.add_channel(engine, c.graph_id, "out")
            await topo_mod.wire_creature(engine, c.graph_id, "alice", "out", "send")
            graph = engine.get_graph(c.graph_id)
            assert "out" in graph.send_edges.get("alice", set())
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_wire_creature_invalid_direction(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            await topo_mod.add_channel(engine, c.graph_id, "ch")
            with pytest.raises(ValueError, match="direction must be"):
                await topo_mod.wire_creature(
                    engine, c.graph_id, "alice", "ch", "wibble"
                )
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_wire_root_alias_resolves(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "rootcr")
            c.is_root = True
            await topo_mod.add_channel(engine, c.graph_id, "tasks")
            await topo_mod.wire_creature(engine, c.graph_id, "root", "tasks", "listen")
            graph = engine.get_graph(c.graph_id)
            assert "tasks" in graph.listen_edges.get("rootcr", set())
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_wire_root_alias_no_root(self):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")
            with pytest.raises(KeyError, match="no root"):
                await topo_mod.wire_creature(engine, c.graph_id, "root", "x", "listen")
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_wire_root_skips_missing_creatures_during_scan(self, monkeypatch):
        engine = Terrarium()
        try:
            c = await install_fake_creature(engine, "alice")

            def _flaky(cid):
                raise KeyError(cid)

            monkeypatch.setattr(engine, "get_creature", _flaky)
            with pytest.raises(KeyError, match="no root"):
                await topo_mod.wire_creature(engine, c.graph_id, "root", "x", "listen")
        finally:
            await engine.shutdown()


# ---------------------------------------------------------------------------
# helper normalisers — exercise the missing-attribute branches
# ---------------------------------------------------------------------------


class TestResultToDict:
    def test_connection_result_with_no_delta(self):
        result = SimpleNamespace(channel="x", graph_id="g")
        out = topo_mod._connection_result_to_dict(result)
        assert out["channel"] == "x"
        assert out["graph_id"] == "g"
        # Without a delta the dict has no "delta" key
        assert "delta" not in out

    def test_connection_result_with_partial_delta(self):
        delta = SimpleNamespace(
            kind="merge",
            old_graph_ids=["g1", "g2"],
            new_graph_ids=["g1"],
            affected_creatures={"alice"},
        )
        result = SimpleNamespace(channel="x", graph_id="g", delta=delta)
        out = topo_mod._connection_result_to_dict(result)
        assert out["delta"]["kind"] == "merge"
        assert out["delta"]["affected"] == ["alice"]

    def test_disconnection_result_no_delta(self):
        result = SimpleNamespace(removed_channel=None, delta=None)
        out = topo_mod._disconnection_result_to_dict(result)
        assert out["removed_channel"] is None
        assert out["delta"]["kind"] == "nothing"

    def test_resolve_kind_helper(self):
        assert topo_mod._resolve_kind("broadcast") == ChannelKind.BROADCAST
        assert topo_mod._resolve_kind("queue") == ChannelKind.QUEUE
        # Anything non-broadcast falls through to QUEUE
        assert topo_mod._resolve_kind("anything") == ChannelKind.QUEUE
