"""Cross-node connect / disconnect coverage for
:mod:`kohakuterrarium.terrarium.multi_node_service`.

The lab-host runs no agents — a *cross-node* link is between two
**workers**.  These tests put alice on worker-1 and bob on worker-2,
give the host's coordination engine a fake ``_broadcast_adapter``, and
drive the cross-node branches.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.terrarium.events import ConnectionResult, DisconnectionResult

from tests.unit.terrarium.test_multi_node_service import (
    _info,
    _make_service,
)


@pytest.fixture
def cross_svc():
    """Service with alice on worker-1, bob on worker-2."""
    svc = _make_service(
        remote_specs={
            "w1": [_info("alice", graph_id="g-w1")],
            "w2": [_info("bob", graph_id="g-w2")],
        }
    )
    # Prime the home registry.
    svc._home["alice"] = "w1"
    svc._home["bob"] = "w2"

    # _FakeService doesn't ship wire_creature; both sides need it for
    # cross-node connect/disconnect tests.
    async def _noop_wire(gid, cid, channel, direction, *, enabled=True):
        return None

    svc._remotes["w1"].wire_creature = _noop_wire
    svc._remotes["w2"].wire_creature = _noop_wire
    return svc


class _FakeBroadcast:
    def __init__(self):
        self.subscribed = []
        self.unsubscribed = []

    async def proxy_subscribe(self, *, proxy_node, peer_node, graph_id, channel):
        self.subscribed.append((proxy_node, peer_node, graph_id, channel))

    async def proxy_unsubscribe(self, *, proxy_node, peer_node, graph_id, channel):
        self.unsubscribed.append((proxy_node, peer_node, graph_id, channel))


class TestCrossNodeConnect:
    async def test_connect_explicit_channel(self, cross_svc):
        cross_svc._coordination_engine = SimpleNamespace(
            _broadcast_adapter=_FakeBroadcast()
        )
        result = await cross_svc.connect("alice", "bob", channel="chat")
        assert isinstance(result, ConnectionResult)
        assert result.delta_kind == "cross_node"
        assert result.channel == "chat"
        # Broadcast subscription recorded both sides.
        bcast = cross_svc._coordination_engine._broadcast_adapter
        assert bcast.subscribed

    async def test_connect_auto_channel_name(self, cross_svc):
        cross_svc._coordination_engine = SimpleNamespace(
            _broadcast_adapter=_FakeBroadcast()
        )
        result = await cross_svc.connect("alice", "bob")
        # Auto-generated channel name: <send_name>_to_<recv_name>.
        assert "_to_" in result.channel

    async def test_connect_no_broadcast_adapter(self, cross_svc):
        # _broadcast_adapter is None → cross-sub branch is skipped.
        cross_svc._coordination_engine = SimpleNamespace(_broadcast_adapter=None)
        result = await cross_svc.connect("alice", "bob", channel="chat")
        assert result.delta_kind == "cross_node"

    async def test_connect_no_coordination_engine(self, cross_svc):
        # No coordination engine at all → cross-sub branch is skipped.
        cross_svc._coordination_engine = None
        result = await cross_svc.connect("alice", "bob", channel="chat")
        assert result.delta_kind == "cross_node"

    async def test_connect_broadcast_subscribe_failure_swallowed(self, cross_svc):
        class _BadBroadcast:
            async def proxy_subscribe(self, **kw):
                raise RuntimeError("bad")

        cross_svc._coordination_engine = SimpleNamespace(
            _broadcast_adapter=_BadBroadcast()
        )
        # Should not raise — subscription failure is logged then swallowed.
        result = await cross_svc.connect("alice", "bob", channel="chat")
        assert result.delta_kind == "cross_node"

    async def test_connect_add_channel_failure_tolerated(self, cross_svc):
        """add_channel raising on either side is logged + ignored."""
        cross_svc._coordination_engine = SimpleNamespace(_broadcast_adapter=None)

        async def _bad_add(gid, name, description=""):
            raise RuntimeError("already exists")

        cross_svc._remotes["w1"].add_channel = _bad_add
        result = await cross_svc.connect("alice", "bob", channel="chat")
        # Still completes the wiring.
        assert result.delta_kind == "cross_node"


class TestCrossNodeDisconnect:
    async def test_disconnect_explicit_channel(self, cross_svc):
        cross_svc._coordination_engine = SimpleNamespace(
            _broadcast_adapter=_FakeBroadcast()
        )
        # Pre-record a cross-sub so disconnect can drop it.  The key is
        # (receiver_home, sender_home, sender_graph_id, channel).
        cross_svc._record_cross_sub("w2", "w1", "g-w1", "chat")
        result = await cross_svc.disconnect("alice", "bob", channel="chat")
        assert isinstance(result, DisconnectionResult)
        assert result.delta_kind == "cross_node"
        bcast = cross_svc._coordination_engine._broadcast_adapter
        assert bcast.unsubscribed

    async def test_disconnect_no_channel_raises(self, cross_svc):
        with pytest.raises(ValueError, match="explicit channel"):
            await cross_svc.disconnect("alice", "bob")

    async def test_disconnect_no_broadcast_adapter(self, cross_svc):
        cross_svc._coordination_engine = SimpleNamespace(_broadcast_adapter=None)
        result = await cross_svc.disconnect("alice", "bob", channel="chat")
        assert result.delta_kind == "cross_node"

    async def test_disconnect_unsubscribe_failure_swallowed(self, cross_svc):
        class _BadBroadcast:
            async def proxy_unsubscribe(self, **kw):
                raise RuntimeError("bad")

        cross_svc._coordination_engine = SimpleNamespace(
            _broadcast_adapter=_BadBroadcast()
        )
        result = await cross_svc.disconnect("alice", "bob", channel="chat")
        assert result.delta_kind == "cross_node"

    async def test_disconnect_unknown_sender(self, cross_svc):
        with pytest.raises(KeyError):
            await cross_svc.disconnect("ghost", "bob", channel="chat")

    async def test_disconnect_unknown_receiver(self, cross_svc):
        with pytest.raises(KeyError):
            await cross_svc.disconnect("alice", "ghost", channel="chat")


class TestCrossConnectMissingInfo:
    async def test_sender_info_missing(self, cross_svc):
        # Sender exists in _home but get_creature_info returns None.
        async def _miss(cid):
            return None

        cross_svc._remotes["w1"].get_creature_info = _miss
        with pytest.raises(KeyError):
            await cross_svc.connect("alice", "bob", channel="chat")

    async def test_receiver_info_missing(self, cross_svc):
        async def _miss(cid):
            return None

        # Replace worker-2's lookup so bob disappears.
        cross_svc._remotes["w2"].get_creature_info = _miss
        with pytest.raises(KeyError):
            await cross_svc.connect("alice", "bob", channel="chat")
