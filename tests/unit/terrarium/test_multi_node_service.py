"""Unit tests for :mod:`kohakuterrarium.terrarium.multi_node_service`.

MultiNodeTerrariumService is a pure router over N RemoteTerrariumService
workers — the lab-host runs NO agents, so there is no host-local
service and no host engine.  We construct it via ``__new__`` so we can
substitute fake worker services without spinning up a real HostEngine /
StreamDemux.
"""

import pytest

from kohakuterrarium.terrarium.events import (
    ConnectionResult,
    DisconnectionResult,
)
from kohakuterrarium.terrarium.multi_node_service import (
    HOST_NODE,
    CrossNodeNotSupportedError,
    MultiNodeTerrariumService,
)
from kohakuterrarium.terrarium.service import CreatureInfo
from kohakuterrarium.terrarium.topology import ChannelInfo, GraphTopology

# ── fakes ─────────────────────────────────────────────────────────


class _FakeService:
    """Stand-in for LocalTerrariumService / RemoteTerrariumService."""

    def __init__(
        self,
        *,
        node_id="_host",
        creatures=(),
        graphs=(),
        channels_by_graph=None,
    ):
        self.node_id = node_id
        self.engine = object()
        self._creatures = list(creatures)
        self._graphs = list(graphs)
        self._channels_by_graph = channels_by_graph or {}
        self.calls: list[tuple] = []
        self._add_creature_response = None

    async def list_creatures(self):
        return tuple(self._creatures)

    async def get_creature_info(self, cid):
        for c in self._creatures:
            if c.creature_id == cid:
                return c
        return None

    async def list_graphs(self):
        return tuple(self._graphs)

    async def get_graph(self, gid):
        for g in self._graphs:
            if g.graph_id == gid:
                return g
        return None

    async def list_channels(self, gid):
        return tuple(self._channels_by_graph.get(gid, ()))

    async def creature_status(self, cid):
        self.calls.append(("creature_status", cid))
        for c in self._creatures:
            if c.creature_id == cid:
                return {"running": c.is_running}
        return None

    async def status_snapshot(self):
        return {"node": self.node_id}

    async def add_creature(self, *args, **kwargs):
        self.calls.append(("add_creature", args, kwargs))
        if self._add_creature_response is not None:
            return self._add_creature_response
        return CreatureInfo(
            creature_id="new-cid",
            name="new",
            graph_id="g",
            is_running=True,
            is_privileged=False,
            parent_creature_id=None,
            listen_channels=(),
            send_channels=(),
        )

    async def remove_creature(self, cid):
        self.calls.append(("remove_creature", cid))

    async def start_creature(self, cid):
        self.calls.append(("start_creature", cid))

    async def stop_creature(self, cid):
        self.calls.append(("stop_creature", cid))

    async def add_channel(self, gid, name, description=""):
        self.calls.append(("add_channel", gid, name, description))
        return ChannelInfo(name=name, description=description)

    async def remove_channel(self, gid, name):
        from kohakuterrarium.terrarium.topology import TopologyDelta

        self.calls.append(("remove_channel", gid, name))
        return TopologyDelta(kind="nothing")

    async def connect(self, sid, rid, *, channel=None):
        return ConnectionResult(channel=channel or "ch", delta_kind="nothing")

    async def disconnect(self, sid, rid, *, channel=None):
        return DisconnectionResult(channels=[channel or "ch"])

    async def inject_input(self, cid, msg, *, source="chat"):
        self.calls.append(("inject_input", cid, msg, source))

    async def shutdown(self):
        self.calls.append(("shutdown",))

    # Per-creature ops
    async def interrupt(self, cid):
        self.calls.append(("interrupt", cid))

    async def list_jobs(self, cid):
        return [{"id": "j1"}]

    async def stop_job(self, cid, jid):
        return True

    async def promote_job(self, cid, jid):
        return False

    async def chat_history(self, cid):
        return {"messages": []}

    async def chat_branches(self, cid):
        return [{"t": 1}]

    async def regenerate(self, cid, *, turn_index=None, branch_view=None):
        return {"ok": True}

    async def edit_message(self, cid, idx, content, **kw):
        return True

    async def rewind(self, cid, idx):
        self.calls.append(("rewind", cid, idx))

    async def get_scratchpad(self, cid):
        return {"k": "v"}

    async def patch_scratchpad(self, cid, updates):
        return updates

    async def list_triggers(self, cid):
        return [{"id": "t1"}]

    async def get_env(self, cid):
        return {"X": "1"}

    async def get_system_prompt(self, cid):
        return {"text": "sys"}

    async def get_working_dir(self, cid):
        return "/cwd"

    async def set_working_dir(self, cid, new_path):
        return new_path

    async def native_tool_inventory(self, cid):
        return []

    async def get_native_tool_options(self, cid):
        return {}

    async def set_native_tool_options(self, cid, tool, values):
        return values

    async def switch_model(self, cid, model):
        return model

    async def list_plugins(self, cid):
        return [{"name": "p"}]

    async def toggle_plugin(self, cid, name, enabled):
        return {"enabled": enabled}


def _info(cid, name="x", graph_id="g") -> CreatureInfo:
    return CreatureInfo(
        creature_id=cid,
        name=name,
        graph_id=graph_id,
        is_running=True,
        is_privileged=False,
        parent_creature_id=None,
        listen_channels=(),
        send_channels=(),
    )


def _make_service(remote_specs=None) -> MultiNodeTerrariumService:
    """Construct a service without running the real __init__.

    The lab-host runs no agents — there is no ``_local`` service.  Every
    test wires its creatures onto worker nodes via ``remote_specs``.
    """
    svc = MultiNodeTerrariumService.__new__(MultiNodeTerrariumService)
    svc._host = None
    svc._coordination_engine = None
    svc._demux = None
    svc._remotes = {}
    svc._home = {}
    svc._creature_name_cache = {}
    svc._cross_subs = {}
    svc._cluster_links = set()
    svc._runtime_graph_meta_lookup = None
    for node_id, creatures in (remote_specs or {}).items():
        svc._remotes[node_id] = _FakeService(node_id=node_id, creatures=creatures)
    return svc


# ── basic properties ───────────────────────────────────────────


class TestProperties:
    def test_node_id_is_host(self):
        svc = _make_service()
        assert svc.node_id == HOST_NODE

    def test_engine_raises(self):
        # The lab-host runs no agent engine — reaching for one is the
        # dual local/remote mixing the redo removed.
        svc = _make_service()
        with pytest.raises(RuntimeError, match="no host agent engine"):
            _ = svc.engine

    def test_connected_nodes_is_workers_only(self):
        # The host is not a node anything is routed to — only workers.
        svc = _make_service(remote_specs={"w1": [], "w2": []})
        assert HOST_NODE not in svc.connected_nodes()
        assert set(svc.connected_nodes()) == {"w1", "w2"}


class TestServiceFor:
    def test_host_raises(self):
        # ``service_for("_host")`` is a KeyError — the host runs no agents.
        svc = _make_service()
        with pytest.raises(KeyError):
            svc.service_for(HOST_NODE)

    def test_remote_lookup(self):
        svc = _make_service(remote_specs={"w1": []})
        assert svc.service_for("w1") is svc._remotes["w1"]

    def test_unknown_remote_raises(self):
        svc = _make_service()
        with pytest.raises(KeyError):
            svc.service_for("ghost")


# ── membership management ──────────────────────────────────────


class TestMembership:
    def test_drop_remote_removes_homes(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        svc._home["c1"] = "w1"
        svc.drop_remote("w1")
        assert "w1" not in svc._remotes
        assert "c1" not in svc._home

    def test_drop_unknown_remote_silent(self):
        svc = _make_service()
        svc.drop_remote("ghost")  # no raise


# ── list_creatures (global read) ───────────────────────────────


class TestListCreatures:
    async def test_aggregates_across_nodes(self):
        svc = _make_service(
            remote_specs={"w1": [_info("c-w1")], "w2": [_info("c-w2")]},
        )
        all_creatures = await svc.list_creatures()
        cids = {c.creature_id for c in all_creatures}
        assert cids == {"c-w1", "c-w2"}

    async def test_populates_home_registry(self):
        svc = _make_service(
            remote_specs={"w1": [_info("c-w1")], "w2": [_info("c-w2")]},
        )
        await svc.list_creatures()
        assert svc._home["c-w1"] == "w1"
        assert svc._home["c-w2"] == "w2"

    async def test_populates_name_cache(self):
        svc = _make_service(remote_specs={"w1": [_info("c1", name="alice")]})
        await svc.list_creatures()
        assert svc._creature_name_cache["alice"] == ("w1", "c1")
        assert svc._creature_name_cache["c1"] == ("w1", "c1")

    async def test_remote_failure_swallowed(self):
        svc = _make_service(remote_specs={"w1": []})

        async def boom():
            raise RuntimeError("link dead")

        svc._remotes["w1"].list_creatures = boom
        out = await svc.list_creatures()
        # No raise; partial result.
        assert isinstance(out, tuple)


class TestListGraphs:
    async def test_unions_across_nodes(self):
        svc = _make_service()
        svc._remotes["w1"] = _FakeService(node_id="w1")
        svc._remotes["w1"]._graphs = [GraphTopology(graph_id="g-w1")]
        svc._remotes["w2"] = _FakeService(node_id="w2")
        svc._remotes["w2"]._graphs = [GraphTopology(graph_id="g-w2")]
        graphs = await svc.list_graphs()
        gids = {g.graph_id for g in graphs}
        assert gids == {"g-w1", "g-w2"}


class TestStatusSnapshot:
    async def test_per_node_dict(self):
        # Only worker nodes appear — the host runs no agents.
        svc = _make_service(remote_specs={"w1": []})
        snap = await svc.status_snapshot()
        assert HOST_NODE not in snap
        assert "w1" in snap

    async def test_remote_failure_returns_error_marker(self):
        svc = _make_service(remote_specs={"w1": []})

        async def boom():
            raise RuntimeError("dead")

        svc._remotes["w1"].status_snapshot = boom
        snap = await svc.status_snapshot()
        assert snap["w1"] == {"error": "unreachable"}


# ── get_creature_info / creature_status routing ────────────────


class TestPerCreatureReads:
    async def test_get_creature_info_routes_by_home(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        # Warm home registry.
        await svc.list_creatures()
        out = await svc.get_creature_info("c1")
        assert out is not None
        assert out.creature_id == "c1"

    async def test_get_creature_info_missing_returns_none(self):
        svc = _make_service()
        assert await svc.get_creature_info("ghost") is None

    async def test_creature_status_routes(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        await svc.list_creatures()
        out = await svc.creature_status("c1")
        assert out == {"running": True}

    async def test_creature_status_missing_returns_none(self):
        svc = _make_service()
        assert await svc.creature_status("ghost") is None


# ── lifecycle ──────────────────────────────────────────────────


class TestLifecycle:
    async def test_add_creature_routes_to_on_node(self):
        svc = _make_service(remote_specs={"w1": []})
        info = await svc.add_creature(None, on_node="w1")
        assert info.creature_id == "new-cid"
        # Home is recorded.
        assert svc._home["new-cid"] == "w1"

    async def test_add_creature_default_host_rejected(self):
        # The default ``on_node="_host"`` is rejected — the host runs
        # no agents, so there is no host service to route to.
        svc = _make_service()
        with pytest.raises(KeyError):
            await svc.add_creature(None)

    async def test_add_creature_unknown_node(self):
        svc = _make_service()
        with pytest.raises(KeyError):
            await svc.add_creature(None, on_node="ghost")

    async def test_remove_creature_drops_home(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        await svc.list_creatures()
        await svc.remove_creature("c1")
        assert "c1" not in svc._home

    async def test_start_stop(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        await svc.list_creatures()
        await svc.start_creature("c1")
        await svc.stop_creature("c1")

    async def test_shutdown_is_noop(self):
        # The host runs no agent engine — shutdown() is a no-op and
        # does NOT reach into workers (separate processes own their own
        # lifecycle).
        svc = _make_service(remote_specs={"w1": []})
        await svc.shutdown()
        assert ("shutdown",) not in svc._remotes["w1"].calls


# ── inject_input ──────────────────────────────────────────────


class TestInject:
    async def test_routes_by_home(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        await svc.list_creatures()
        await svc.inject_input("c1", "hello")
        # Should land on the remote service.
        assert ("inject_input", "c1", "hello", "chat") in svc._remotes["w1"].calls


# ── Channels — single-node graph (no cross-node) ─────────────


class TestSingleGraphChannels:
    async def test_add_channel_on_worker_graph(self):
        svc = _make_service(remote_specs={"w1": []})
        # The graph lives on worker-1.
        svc._remotes["w1"]._graphs = [GraphTopology(graph_id="g")]
        ch = await svc.add_channel("g", "my-channel")
        assert ch.name == "my-channel"

    async def test_remove_channel_on_worker_graph(self):
        svc = _make_service(remote_specs={"w1": []})
        svc._remotes["w1"]._graphs = [GraphTopology(graph_id="g")]
        out = await svc.remove_channel("g", "my-channel")
        assert out.kind == "nothing"


# ── connect / disconnect ──────────────────────────────────────


class TestConnect:
    async def test_same_worker_connect(self):
        # Both creatures on one worker — connect routes to that worker.
        svc = _make_service(remote_specs={"w1": [_info("a"), _info("b")]})
        await svc.list_creatures()
        out = await svc.connect("a", "b", channel="ch")
        assert out.channel == "ch"

    async def test_unknown_sender_raises(self):
        svc = _make_service(remote_specs={"w1": [_info("b")]})
        await svc.list_creatures()
        with pytest.raises(KeyError):
            await svc.connect("ghost", "b")

    async def test_unknown_receiver_raises(self):
        svc = _make_service(remote_specs={"w1": [_info("a")]})
        await svc.list_creatures()
        with pytest.raises(KeyError):
            await svc.connect("a", "ghost")


class TestDisconnect:
    async def test_same_worker_disconnect(self):
        svc = _make_service(remote_specs={"w1": [_info("a"), _info("b")]})
        await svc.list_creatures()
        out = await svc.disconnect("a", "b", channel="ch")
        assert out.channels == ["ch"]


# ── cross-sub bookkeeping ─────────────────────────────────────


class TestCrossSubBookkeeping:
    def test_record_drop(self):
        svc = _make_service()
        svc._record_cross_sub("a", "b", "g", "ch")
        svc._record_cross_sub("a", "b", "g", "ch")
        assert svc._cross_subs[("a", "b", "g", "ch")] == 2
        svc._drop_cross_sub("a", "b", "g", "ch")
        assert svc._cross_subs[("a", "b", "g", "ch")] == 1
        svc._drop_cross_sub("a", "b", "g", "ch")
        assert ("a", "b", "g", "ch") not in svc._cross_subs


# ── per-creature routes (smoke) ───────────────────────────────


class TestPerCreatureRoutes:
    async def test_each_method_routes(self):
        svc = _make_service(remote_specs={"w1": [_info("c1")]})
        await svc.list_creatures()
        # Call each method; they should all delegate to the worker.
        await svc.interrupt("c1")
        assert await svc.list_jobs("c1") == [{"id": "j1"}]
        assert await svc.stop_job("c1", "j1") is True
        assert await svc.promote_job("c1", "j1") is False
        assert (await svc.chat_history("c1"))["messages"] == []
        assert await svc.chat_branches("c1") == [{"t": 1}]
        assert (await svc.regenerate("c1"))["ok"] is True
        assert await svc.edit_message("c1", 0, "x") is True
        await svc.rewind("c1", 0)
        assert await svc.get_scratchpad("c1") == {"k": "v"}
        assert await svc.patch_scratchpad("c1", {"k": "v"}) == {"k": "v"}
        assert await svc.list_triggers("c1") == [{"id": "t1"}]
        assert await svc.get_env("c1") == {"X": "1"}
        assert (await svc.get_system_prompt("c1"))["text"] == "sys"
        assert await svc.get_working_dir("c1") == "/cwd"
        assert await svc.set_working_dir("c1", "/new") == "/new"
        assert await svc.native_tool_inventory("c1") == []
        assert await svc.get_native_tool_options("c1") == {}
        assert await svc.set_native_tool_options("c1", "t", {}) == {}
        assert await svc.switch_model("c1", "m") == "m"
        assert await svc.list_plugins("c1") == [{"name": "p"}]
        assert (await svc.toggle_plugin("c1", "p", True))["enabled"] is True


# ── Exception ──────────────────────────────────────────────────


class TestCrossNodeNotSupportedError:
    def test_is_runtime_error(self):
        assert issubclass(CrossNodeNotSupportedError, RuntimeError)
