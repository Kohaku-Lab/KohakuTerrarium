"""Unit tests for :mod:`kohakuterrarium.laboratory.adapters.terrarium_runtime`.

Tests dispatch the adapter against a real :class:`Terrarium` engine
populated via ``TestTerrariumBuilder``; the lab transport is replaced
by a fake ``LabRegistrar`` so the test never touches a real socket.
"""

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.terrarium_runtime import (
    TerrariumRuntimeAdapter,
    _NotHostedHere,
)
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class _FakeNode:
    def __init__(self, client_id=None):
        self.client_id = client_id
        self.registered = {}
        self.unregistered = []

    def register_app_extension(self, ns, handler):
        self.registered[ns] = handler

    def unregister_app_extension(self, ns):
        self.unregistered.append(ns)
        self.registered.pop(ns, None)


def _msg(type_, body=None, sender="ctrl") -> AppMessage:
    return AppMessage(
        namespace=TerrariumRuntimeAdapter.NAMESPACE,
        type=type_,
        body=body or {},
        sender_node=sender,
        request_id=None,
        in_reply_to=None,
    )


async def _make_adapter():
    engine = await (
        TestTerrariumBuilder()
        .with_creature("alice", responses=["hi"])
        .with_creature("bob")
        .with_channel("chat")
        .with_connection("alice", "bob", channel="chat")
        .build()
    )
    return TerrariumRuntimeAdapter(engine, _FakeNode())


# ── construction ────────────────────────────────────────────────


class TestConstruction:
    async def test_init_registers(self):
        adapter = await _make_adapter()
        try:
            assert TerrariumRuntimeAdapter.NAMESPACE in adapter._node.registered
        finally:
            await adapter._engine.shutdown()

    async def test_detach(self):
        adapter = await _make_adapter()
        try:
            adapter.detach()
            assert TerrariumRuntimeAdapter.NAMESPACE in adapter._node.unregistered
        finally:
            await adapter._engine.shutdown()

    async def test_node_id_default_host(self):
        adapter = await _make_adapter()
        try:
            assert adapter.node_id == "_host"
        finally:
            await adapter._engine.shutdown()

    async def test_node_id_from_client(self):
        engine = await TestTerrariumBuilder().build()
        adapter = TerrariumRuntimeAdapter(engine, _FakeNode(client_id="w1"))
        assert adapter.node_id == "w1"
        await engine.shutdown()

    async def test_node_id_explicit(self):
        engine = await TestTerrariumBuilder().build()
        adapter = TerrariumRuntimeAdapter(engine, _FakeNode(), node_id="explicit")
        assert adapter.node_id == "explicit"
        await engine.shutdown()


# ── error mapping ───────────────────────────────────────────────


class TestErrorMapping:
    async def test_not_hosted_here(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("remove_creature", {"creature_id": "ghost"})
            )
            assert out["error"]["kind"] == "creature_not_hosted"
        finally:
            await adapter._engine.shutdown()

    async def test_unknown_type_silent(self):
        adapter = await _make_adapter()
        try:
            # Unknown types surface as a structured ``unknown_type`` error
            # rather than crashing or returning a bare success.
            out = await adapter._dispatch(_msg("not-a-real-type"))
            assert out["error"]["kind"] == "unknown_type"
            assert "not-a-real-type" in out["error"]["message"]
        finally:
            await adapter._engine.shutdown()

    async def test_key_error_to_not_found(self):
        # The engine's start() raises KeyError for unknown creatures.
        adapter = await _make_adapter()
        try:
            # Bypass _require_hosted by directly calling _engine.start
            # — what we really want is the dispatch translation, so
            # exercise it via a path that goes through Engine internally.
            out = await adapter._dispatch(
                _msg("creature_status", {"creature_id": "ghost"})
            )
            # creature_status returns status: None for missing.
            assert out == {"status": None}
        finally:
            await adapter._engine.shutdown()


# ── topology reads ──────────────────────────────────────────────


class TestTopologyReads:
    async def test_node_id(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(_msg("node_id"))
            assert out == {"node_id": "_host"}
        finally:
            await adapter._engine.shutdown()

    async def test_list_creatures(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(_msg("list_creatures"))
            names = {c["name"] for c in out["creatures"]}
            assert names == {"alice", "bob"}
        finally:
            await adapter._engine.shutdown()

    async def test_get_creature_info_known(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("get_creature_info", {"creature_id": "alice"})
            )
            assert out["creature_info"] is not None
            assert out["creature_info"]["name"] == "alice"
        finally:
            await adapter._engine.shutdown()

    async def test_get_creature_info_missing_returns_null(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("get_creature_info", {"creature_id": "ghost"})
            )
            assert out == {"creature_info": None}
        finally:
            await adapter._engine.shutdown()

    async def test_list_graphs(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(_msg("list_graphs"))
            assert len(out["graphs"]) == 1
        finally:
            await adapter._engine.shutdown()

    async def test_get_graph_known(self):
        adapter = await _make_adapter()
        try:
            lg = await adapter._dispatch(_msg("list_graphs"))
            gid = lg["graphs"][0]["graph_id"]
            out = await adapter._dispatch(_msg("get_graph", {"graph_id": gid}))
            assert out["graph"]["graph_id"] == gid
        finally:
            await adapter._engine.shutdown()

    async def test_get_graph_missing(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("get_graph", {"graph_id": "no-such-graph"})
            )
            assert out == {"graph": None}
        finally:
            await adapter._engine.shutdown()

    async def test_list_channels(self):
        adapter = await _make_adapter()
        try:
            lg = await adapter._dispatch(_msg("list_graphs"))
            gid = lg["graphs"][0]["graph_id"]
            out = await adapter._dispatch(_msg("list_channels", {"graph_id": gid}))
            names = {c["name"] for c in out["channels"]}
            assert "chat" in names
        finally:
            await adapter._engine.shutdown()

    async def test_list_channels_unknown_graph(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(_msg("list_channels", {"graph_id": "ghost"}))
            assert out == {"channels": []}
        finally:
            await adapter._engine.shutdown()

    async def test_creature_status(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("creature_status", {"creature_id": "alice"})
            )
            assert out["status"] is not None
        finally:
            await adapter._engine.shutdown()

    async def test_status_snapshot(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(_msg("status_snapshot"))
            status = out["status"]
            # The snapshot reflects the real engine topology: the running
            # flag is set and both seeded creatures appear.
            assert status["running"] is True
            assert set(status["creatures"]) == {"alice", "bob"}
            # The single graph carries both creatures and the seeded channel.
            graph = next(iter(status["graphs"].values()))
            assert set(graph["creature_ids"]) == {"alice", "bob"}
            assert "chat" in graph["channels"]
        finally:
            await adapter._engine.shutdown()


# ── lifecycle ───────────────────────────────────────────────────


class TestLifecycleOps:
    async def test_remove_creature(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("remove_creature", {"creature_id": "alice"})
            )
            assert out == {}
        finally:
            await adapter._engine.shutdown()

    async def test_start_stop_creature(self):
        adapter = await _make_adapter()
        try:
            await adapter._dispatch(_msg("stop_creature", {"creature_id": "alice"}))
            await adapter._dispatch(_msg("start_creature", {"creature_id": "alice"}))
        finally:
            await adapter._engine.shutdown()

    async def test_start_unknown(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("start_creature", {"creature_id": "ghost"})
            )
            assert out["error"]["kind"] == "creature_not_hosted"
        finally:
            await adapter._engine.shutdown()


# ── channels ────────────────────────────────────────────────────


class TestChannelOps:
    async def test_add_channel(self):
        adapter = await _make_adapter()
        try:
            lg = await adapter._dispatch(_msg("list_graphs"))
            gid = lg["graphs"][0]["graph_id"]
            out = await adapter._dispatch(
                _msg(
                    "add_channel",
                    {"graph_id": gid, "name": "extra", "description": "d"},
                )
            )
            assert out["channel"]["name"] == "extra"
        finally:
            await adapter._engine.shutdown()

    async def test_remove_channel(self):
        adapter = await _make_adapter()
        try:
            lg = await adapter._dispatch(_msg("list_graphs"))
            gid = lg["graphs"][0]["graph_id"]
            await adapter._dispatch(
                _msg("add_channel", {"graph_id": gid, "name": "extra"})
            )
            out = await adapter._dispatch(
                _msg("remove_channel", {"graph_id": gid, "name": "extra"})
            )
            assert "delta" in out
        finally:
            await adapter._engine.shutdown()


# ── interaction ─────────────────────────────────────────────────


class TestInjectInput:
    async def test_inject(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("inject_input", {"creature_id": "alice", "message": "hi"})
            )
            assert out == {}
        finally:
            await adapter._engine.shutdown()

    async def test_inject_unknown(self):
        adapter = await _make_adapter()
        try:
            out = await adapter._dispatch(
                _msg("inject_input", {"creature_id": "ghost", "message": "x"})
            )
            assert out["error"]["kind"] == "creature_not_hosted"
        finally:
            await adapter._engine.shutdown()


# ── shutdown ────────────────────────────────────────────────────


class TestShutdown:
    async def test_shutdown(self):
        adapter = await _make_adapter()
        out = await adapter._dispatch(_msg("shutdown"))
        assert out == {}


# ── _NotHostedHere ──────────────────────────────────────────────


class TestNotHostedHere:
    def test_is_key_error(self):
        assert issubclass(_NotHostedHere, KeyError)
