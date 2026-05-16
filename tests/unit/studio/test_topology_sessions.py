"""Unit tests for :mod:`kohakuterrarium.studio.sessions.topology`."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.studio.sessions import topology as topology_mod
from kohakuterrarium.terrarium.events import (
    ConnectionResult,
    DisconnectionResult,
)
from kohakuterrarium.terrarium.topology import (
    ChannelInfo,
    TopologyDelta,
)


class _FakeService:
    def __init__(
        self, *, add=None, remove=None, connect=None, disconnect=None, channels=None
    ):
        self._add = add or ChannelInfo(name="ch", description="d")
        self._remove = remove or TopologyDelta(kind="nothing")
        self._connect = connect or ConnectionResult(channel="ch", delta_kind="nothing")
        self._disconnect = disconnect or DisconnectionResult(
            channels=["ch"], delta_kind="nothing"
        )
        # ``{session_id: tuple[ChannelInfo, ...]}`` — the service's view
        # of channels per graph; an unknown session yields ``()``.
        self._channels = channels or {}
        self.calls = []

    async def add_channel(self, sid, name, description=""):
        self.calls.append(("add_channel", sid, name, description))
        return self._add

    async def list_channels(self, session_id):
        self.calls.append(("list_channels", session_id))
        return self._channels.get(session_id, ())

    async def remove_channel(self, sid, name):
        self.calls.append(("remove_channel", sid, name))
        return self._remove

    async def connect(self, sender, receiver, *, channel=None):
        self.calls.append(("connect", sender, receiver, channel))
        return self._connect

    async def disconnect(self, sender, receiver, *, channel=None):
        self.calls.append(("disconnect", sender, receiver, channel))
        return self._disconnect

    async def wire_creature(self, sid, cid, ch, direction, *, enabled):
        self.calls.append(("wire", sid, cid, ch, direction, enabled))


# ── service-mediated APIs ─────────────────────────────────────


class TestAddChannel:
    async def test_basic(self):
        svc = _FakeService()
        out = await topology_mod.add_channel(svc, "g1", "ch", description="d")
        assert out == {"name": "ch", "type": "broadcast", "description": "d"}

    async def test_passthrough(self):
        svc = _FakeService()
        await topology_mod.add_channel(
            svc, "g1", "ch", channel_type="queue", description="x"
        )
        assert svc.calls[0] == ("add_channel", "g1", "ch", "x")


class TestRemoveChannel:
    async def test_basic(self):
        delta = TopologyDelta(
            kind="split",
            old_graph_ids=["g1"],
            new_graph_ids=["g1", "g2"],
            affected_creatures={"a", "b"},
        )
        svc = _FakeService(remove=delta)
        out = await topology_mod.remove_channel(svc, "g1", "ch")
        assert out["removed"] == "ch"
        assert out["delta"]["kind"] == "split"
        assert out["delta"]["old_graph_ids"] == ["g1"]
        assert sorted(out["delta"]["affected"]) == ["a", "b"]


class TestConnect:
    async def test_basic(self):
        svc = _FakeService()
        out = await topology_mod.connect(svc, "a", "b", channel="ch")
        assert out["channel"] == "ch"

    async def test_passes_channel_type_through(self):
        svc = _FakeService()
        # channel_type ignored at this layer but accepted.
        await topology_mod.connect(svc, "a", "b", channel_type="queue")


class TestDisconnect:
    async def test_basic(self):
        svc = _FakeService()
        out = await topology_mod.disconnect(svc, "a", "b")
        assert out["channels"] == ["ch"]


class TestWireCreature:
    async def test_basic(self):
        svc = _FakeService()
        await topology_mod.wire_creature(svc, "g1", "c1", "ch", "listen")
        assert svc.calls[0] == ("wire", "g1", "c1", "ch", "listen", True)

    async def test_disable(self):
        svc = _FakeService()
        await topology_mod.wire_creature(svc, "g1", "c1", "ch", "send", enabled=False)
        assert svc.calls[0] == ("wire", "g1", "c1", "ch", "send", False)


# ── engine-direct APIs (list_channels, channel_info, send_to_channel) ─


class _FakeChannel:
    def __init__(self, *, name="ch", ch_type="broadcast", description="", qsize=0):
        self.name = name
        self.channel_type = ch_type
        self.description = description
        self.qsize = qsize
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)


class _FakeRegistry:
    def __init__(self, channels=None):
        self._channels = channels or {}

    def list_channels(self):
        return list(self._channels.keys())

    def get(self, name):
        return self._channels.get(name)

    def get_channel_info(self):
        return [
            {"name": c.name, "type": c.channel_type} for c in self._channels.values()
        ]


class _FakeEnv:
    def __init__(self, channels=None):
        self.shared_channels = _FakeRegistry(channels)


class _FakeEngine:
    def __init__(self, envs=None):
        self._environments = envs or {}


class TestListChannels:
    # ``list_channels`` is now async + service-routed: it asks the
    # service for the graph's channels, so a worker-hosted session's
    # channels are reachable. An unknown session yields ``()`` from the
    # service → an empty list (not a KeyError).
    async def test_session_missing_yields_empty(self):
        svc = _FakeService()
        assert await topology_mod.list_channels(svc, "ghost") == []

    async def test_success(self):
        svc = _FakeService(channels={"g1": (ChannelInfo(name="ch", description="d"),)})
        out = await topology_mod.list_channels(svc, "g1")
        assert out[0]["name"] == "ch"
        assert out[0]["description"] == "d"
        assert out[0]["type"] == "broadcast"


class TestChannelInfo:
    async def test_session_missing_returns_none(self):
        svc = _FakeService()
        assert await topology_mod.channel_info(svc, "ghost", "ch") is None

    async def test_channel_missing_returns_none(self):
        svc = _FakeService(channels={"g1": ()})
        assert await topology_mod.channel_info(svc, "g1", "ghost") is None

    async def test_success(self):
        svc = _FakeService(channels={"g1": (ChannelInfo(name="ch", description="d"),)})
        out = await topology_mod.channel_info(svc, "g1", "ch")
        assert out["name"] == "ch"
        assert out["description"] == "d"
        assert out["type"] == "broadcast"


class TestSendToChannel:
    async def test_session_missing(self):
        eng = _FakeEngine()
        with pytest.raises(KeyError):
            await topology_mod.send_to_channel(eng, "ghost", "ch", "hi")

    async def test_channel_missing(self):
        env = _FakeEnv()
        eng = _FakeEngine(envs={"g1": env})
        with pytest.raises(ValueError, match="Channel"):
            await topology_mod.send_to_channel(eng, "g1", "ghost", "hi")

    async def test_success(self):
        ch = _FakeChannel()
        env = _FakeEnv(channels={"ch": ch})
        eng = _FakeEngine(envs={"g1": env})
        msg_id = await topology_mod.send_to_channel(eng, "g1", "ch", "hi", "alice")
        assert ch.sent[0].content == "hi"
        assert isinstance(msg_id, str)


# ── helpers ────────────────────────────────────────────────────


class TestConnectionResultToDict:
    def test_basic(self):
        out = topology_mod._connection_result_to_dict(
            ConnectionResult(channel="ch", graph_id="g")
        )
        assert out["channel"] == "ch"
        assert out["graph_id"] == "g"

    def test_with_delta_kind(self):
        r = SimpleNamespace(channel="ch", delta_kind="merge")
        out = topology_mod._connection_result_to_dict(r)
        assert out["delta"] == {"kind": "merge"}

    def test_with_full_delta(self):
        delta = TopologyDelta(kind="merge", old_graph_ids=["a"], new_graph_ids=["a"])
        r = SimpleNamespace(channel="ch", delta=delta, graph_id="a")
        out = topology_mod._connection_result_to_dict(r)
        assert out["delta"]["kind"] == "merge"


class TestDisconnectionResultToDict:
    def test_basic(self):
        out = topology_mod._disconnection_result_to_dict(
            DisconnectionResult(channels=["a", "b"], delta_kind="split")
        )
        assert out["channels"] == ["a", "b"]
        assert out["delta"]["kind"] == "split"

    def test_missing_attrs(self):
        out = topology_mod._disconnection_result_to_dict(SimpleNamespace())
        assert out["channels"] == []
        assert out["delta"]["kind"] == "nothing"
