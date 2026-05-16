"""Unit tests for :mod:`kohakuterrarium.api.routes.runtime_graph`."""

from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes import runtime_graph as rg_mod
from kohakuterrarium.terrarium.topology import ChannelInfo, GraphTopology

# ── helpers / fakes ────────────────────────────────────────────


class _FakeService:
    def __init__(self, snapshot=None):
        self._snapshot = snapshot or {"version": 0, "graphs": []}

    async def runtime_graph_snapshot(self):
        return self._snapshot


class _FakeCreature:
    def __init__(
        self,
        cid,
        *,
        is_privileged=False,
        parent=None,
        name=None,
        status=None,
    ):
        self.creature_id = cid
        self.is_privileged = is_privileged
        self.parent_creature_id = parent
        self.name = name or cid
        self._status = status or {"name": name or cid, "creature_id": cid}

    def get_status(self):
        return dict(self._status)


class _FakeRegistry:
    def __init__(self, channels=None):
        self._channels = channels or {}

    def list_channels(self):
        return list(self._channels.keys())

    def get(self, name):
        return self._channels.get(name)


class _FakeEnv:
    def __init__(self, channels=None):
        self.shared_channels = _FakeRegistry(channels)


class _FakeEngine:
    def __init__(self, graphs=None, creatures=None, envs=None, wiring=None):
        self._graphs_list = graphs or []
        self._creatures = creatures or {}
        self._environments = envs or {}
        self._wiring = wiring or {}

    def list_graphs(self):
        return list(self._graphs_list)

    def get_graph(self, gid):
        for g in self._graphs_list:
            if g.graph_id == gid:
                return g
        raise KeyError(gid)

    def get_creature(self, cid):
        if cid not in self._creatures:
            raise KeyError(cid)
        return self._creatures[cid]

    def list_output_wiring(self, cid):
        return list(self._wiring.get(cid, []))


# ── /graph route ───────────────────────────────────────────────


class TestRuntimeGraphRoute:
    def test_returns_service_snapshot(self):
        snap = {"version": 1, "graphs": [{"graph_id": "g"}]}
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: _FakeService(snap)
        app.include_router(rg_mod.router, prefix="/runtime")
        client = TestClient(app)
        resp = client.get("/runtime/graph")
        assert resp.status_code == 200
        assert resp.json() == snap


# ── build_runtime_graph_snapshot ───────────────────────────────


class TestBuildSnapshot:
    def test_orders_by_created_at(self, monkeypatch):
        # Two graphs with different created_at values.
        monkeypatch.setattr(
            rg_mod.lifecycle,
            "get_session_meta",
            lambda gid: {
                "g-late": {"created_at": "2025-12-31"},
                "g-early": {"created_at": "2025-01-01"},
            }.get(gid, {}),
        )
        graphs = [
            GraphTopology(graph_id="g-late", creature_ids=set()),
            GraphTopology(graph_id="g-early", creature_ids=set()),
        ]
        engine = _FakeEngine(graphs=graphs)
        out = rg_mod.build_runtime_graph_snapshot(engine)
        ids = [g["graph_id"] for g in out["graphs"]]
        # Oldest first.
        assert ids == ["g-early", "g-late"]

    def test_version_is_milliseconds(self, monkeypatch):
        monkeypatch.setattr(rg_mod.lifecycle, "get_session_meta", lambda gid: {})
        engine = _FakeEngine(graphs=[])
        out = rg_mod.build_runtime_graph_snapshot(engine)
        # Should be an int large enough to look like a ms timestamp.
        assert out["version"] > 10**12


# ── _graph_to_dict ─────────────────────────────────────────────


class TestGraphToDict:
    def test_includes_kind_and_creatures(self, monkeypatch):
        c = _FakeCreature("c1")
        graph = GraphTopology(graph_id="g1", creature_ids={"c1"})
        monkeypatch.setattr(
            rg_mod.lifecycle,
            "get_session_meta",
            lambda gid: {"kind": "creature", "name": "alice"},
        )
        engine = _FakeEngine(graphs=[graph], creatures={"c1": c})
        out = rg_mod._graph_to_dict(engine, graph)
        assert out["graph_id"] == "g1"
        assert out["kind"] == "creature"
        assert len(out["creatures"]) == 1

    def test_inferred_kind_from_creature_count(self, monkeypatch):
        graph = GraphTopology(graph_id="g1", creature_ids={"c1", "c2"})
        c1 = _FakeCreature("c1")
        c2 = _FakeCreature("c2")
        monkeypatch.setattr(rg_mod.lifecycle, "get_session_meta", lambda gid: {})
        engine = _FakeEngine(graphs=[graph], creatures={"c1": c1, "c2": c2})
        out = rg_mod._graph_to_dict(engine, graph)
        assert out["kind"] == "terrarium"


# ── _creatures_for_graph ───────────────────────────────────────


class TestCreaturesForGraph:
    def test_picks_root_by_creature_id(self):
        graph = GraphTopology(graph_id="g", creature_ids={"root", "alice"})
        root = _FakeCreature("root", is_privileged=True)
        alice = _FakeCreature("alice", is_privileged=True)
        engine = _FakeEngine(creatures={"root": root, "alice": alice})
        out = rg_mod._creatures_for_graph(engine, graph)
        root_entry = next(c for c in out if c["creature_id"] == "root")
        alice_entry = next(c for c in out if c["creature_id"] == "alice")
        assert root_entry["is_root"] is True
        assert alice_entry["is_root"] is False

    def test_picks_root_by_name(self):
        graph = GraphTopology(graph_id="g", creature_ids={"cid-1"})
        c = _FakeCreature("cid-1", is_privileged=True, name="root")
        engine = _FakeEngine(creatures={"cid-1": c})
        # ``engine._creatures`` is the same dict used by ``get_creature``.
        out = rg_mod._creatures_for_graph(engine, graph)
        assert out[0]["is_root"] is True

    def test_falls_back_to_lowest_privileged(self):
        graph = GraphTopology(graph_id="g", creature_ids={"c2", "c1"})
        c1 = _FakeCreature("c1", is_privileged=True)
        c2 = _FakeCreature("c2", is_privileged=True)
        engine = _FakeEngine(creatures={"c1": c1, "c2": c2})
        out = rg_mod._creatures_for_graph(engine, graph)
        # Sorted creatures: c1 (lowest); it's the picked root.
        c1_entry = next(c for c in out if c["creature_id"] == "c1")
        assert c1_entry["is_root"] is True

    def test_no_privileged(self):
        graph = GraphTopology(graph_id="g", creature_ids={"c1"})
        c = _FakeCreature("c1", is_privileged=False)
        engine = _FakeEngine(creatures={"c1": c})
        out = rg_mod._creatures_for_graph(engine, graph)
        assert out[0]["is_root"] is False

    def test_missing_creature_skipped(self):
        # Graph references a creature the engine no longer holds.
        graph = GraphTopology(graph_id="g", creature_ids={"c1"})
        engine = _FakeEngine(creatures={})  # no c1
        out = rg_mod._creatures_for_graph(engine, graph)
        assert out == []


# ── _channels_for_graph ────────────────────────────────────────


class _FakeChannel:
    def __init__(
        self,
        *,
        channel_type="broadcast",
        description="",
        qsize=0,
        history=None,
    ):
        self.channel_type = channel_type
        self.description = description
        self.qsize = qsize
        self.history = history or []


class TestChannelsForGraph:
    def test_with_topology_only(self):
        graph = GraphTopology(
            graph_id="g",
            channels={"chat": ChannelInfo(name="chat", description="x")},
        )
        engine = _FakeEngine(graphs=[graph])
        out = rg_mod._channels_for_graph(engine, graph)
        assert out[0]["name"] == "chat"
        assert out[0]["description"] == "x"

    def test_with_runtime_channel(self):
        graph = GraphTopology(
            graph_id="g",
            channels={"chat": ChannelInfo(name="chat")},
        )
        env = _FakeEnv(channels={"chat": _FakeChannel(channel_type="queue")})
        engine = _FakeEngine(graphs=[graph], envs={"g": env})
        out = rg_mod._channels_for_graph(engine, graph)
        assert out[0]["type"] == "queue"

    def test_with_history(self):
        from datetime import datetime as _dt

        class _Msg:
            message_id = "m1"
            sender = "alice"
            content = "hi"
            timestamp = _dt(2025, 1, 1, 0, 0, 0)
            metadata = {"k": "v"}
            reply_to = None

        graph = GraphTopology(graph_id="g", channels={"chat": ChannelInfo(name="chat")})
        env = _FakeEnv(channels={"chat": _FakeChannel(history=[_Msg()])})
        engine = _FakeEngine(graphs=[graph], envs={"g": env})
        out = rg_mod._channels_for_graph(engine, graph)
        last = out[0]["last_message"]
        assert last["message_id"] == "m1"
        assert "1" in last["content_preview"] or "hi" in last["content_preview"]


# ── _output_edges_for_graph ────────────────────────────────────


class TestOutputEdges:
    def test_basic(self):
        graph = GraphTopology(graph_id="g", creature_ids={"c1"})
        wiring = {"c1": [{"edge_id": "e1", "to": "c2"}]}
        engine = _FakeEngine(
            graphs=[graph],
            wiring=wiring,
        )
        # ``_output_edges_for_graph`` reads creature data already serialised.
        creatures = [{"creature_id": "c1", "name": "alice"}]
        out = rg_mod._output_edges_for_graph(engine, graph, creatures)
        assert out[0]["edge_id"] == "e1"
        assert out[0]["from"] == "c1"

    def test_no_creature_id_skipped(self):
        graph = GraphTopology(graph_id="g")
        engine = _FakeEngine(graphs=[graph], wiring={})
        out = rg_mod._output_edges_for_graph(engine, graph, [{}])
        assert out == []

    def test_engine_exception_swallowed(self):
        graph = GraphTopology(graph_id="g")

        class _Boomgine(_FakeEngine):
            def list_output_wiring(self, cid):
                raise RuntimeError("boom")

        engine = _Boomgine(graphs=[graph])
        creatures = [{"creature_id": "c1"}]
        out = rg_mod._output_edges_for_graph(engine, graph, creatures)
        assert out == []


# ── _resolve_target_creature_id ────────────────────────────────


class TestResolveTargetCreatureId:
    def test_empty(self):
        graph = GraphTopology(graph_id="g")
        assert rg_mod._resolve_target_creature_id(graph, [], "") == ""

    def test_exact_id_match(self):
        graph = GraphTopology(graph_id="g")
        creatures = [{"creature_id": "c1", "name": "alice"}]
        out = rg_mod._resolve_target_creature_id(graph, creatures, "c1")
        assert out == "c1"

    def test_name_match(self):
        graph = GraphTopology(graph_id="g")
        creatures = [{"creature_id": "c1", "name": "alice"}]
        out = rg_mod._resolve_target_creature_id(graph, creatures, "alice")
        assert out == "c1"

    def test_root_target(self):
        graph = GraphTopology(graph_id="g")
        creatures = [
            {"creature_id": "cid-1", "name": "alice", "is_root": True},
        ]
        out = rg_mod._resolve_target_creature_id(graph, creatures, "root")
        assert out == "cid-1"

    def test_in_graph_creature_ids(self):
        graph = GraphTopology(graph_id="g", creature_ids={"hidden-id"})
        out = rg_mod._resolve_target_creature_id(graph, [], "hidden-id")
        assert out == "hidden-id"

    def test_unknown_returns_empty(self):
        graph = GraphTopology(graph_id="g")
        out = rg_mod._resolve_target_creature_id(graph, [], "ghost")
        assert out == ""


# ── _message_to_dict / _jsonable / _preview / _timestamp ──────


class TestMessageToDict:
    def test_basic(self):
        class _Msg:
            message_id = "m1"
            sender = "alice"
            content = "hi"
            timestamp = datetime(2025, 1, 1)
            metadata = {}
            reply_to = None

        out = rg_mod._message_to_dict(_Msg())
        assert out["message_id"] == "m1"
        assert out["sender"] == "alice"

    def test_no_attrs(self):
        out = rg_mod._message_to_dict(object())
        assert out["message_id"] == ""
        assert out["sender"] == ""


class TestJsonable:
    def test_serializable(self):
        assert rg_mod._jsonable({"a": 1}) == {"a": 1}

    def test_unserializable_falls_back_to_str(self):
        out = rg_mod._jsonable({1, 2, 3})  # sets aren't JSON
        assert isinstance(out, str)


class TestPreview:
    def test_string(self):
        assert rg_mod._preview("hello") == "hello"

    def test_long_truncated(self):
        out = rg_mod._preview("x" * 500)
        assert out.endswith("…")

    def test_newlines_collapsed(self):
        out = rg_mod._preview("a\nb")
        assert "\n" not in out

    def test_non_string_falls_back(self):
        out = rg_mod._preview({"k": "v"})
        assert "k" in out

    def test_unserializable_uses_str(self):
        out = rg_mod._preview({1, 2})
        assert isinstance(out, str)


class TestTimestampToString:
    def test_none(self):
        assert rg_mod._timestamp_to_string(None) == ""

    def test_iso(self):
        d = datetime(2025, 1, 1)
        out = rg_mod._timestamp_to_string(d)
        assert out.startswith("2025-01-01")

    def test_no_isoformat_uses_str(self):
        class _T:
            pass

        out = rg_mod._timestamp_to_string(_T())
        assert isinstance(out, str)
