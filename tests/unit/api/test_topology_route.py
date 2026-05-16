"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2.topology`."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_engine, get_service
from kohakuterrarium.api.routes.sessions_v2 import topology as topology_mod


class _FakeService:
    """A service whose ``get_graph`` reports the session as present.

    ``list_session_channels`` / ``get_session_channel`` / ``merge_sessions``
    make an explicit ``get_graph`` resolution — a test that wants the
    "session missing" branch passes ``graph=None``; a test that needs
    distinct per-id graphs (merge) passes a ``graphs`` map.
    ``connect`` returns ``connect_result`` for the merge path.
    """

    def __init__(self, *, graph=object(), graphs=None, connect_result=None):
        self._graph = graph
        self._graphs = graphs
        self._connect_result = connect_result
        self.connect_calls: list[tuple[str, str]] = []

    async def get_graph(self, gid):
        if self._graphs is not None:
            return self._graphs.get(gid)
        return self._graph

    async def connect(self, sender_id, receiver_id, *, channel=None):
        self.connect_calls.append((sender_id, receiver_id))
        return self._connect_result


class _FakeGraph:
    def __init__(self, gid, creatures=()):
        self.graph_id = gid
        self.creature_ids = set(creatures)


class _FakeEngine:
    def __init__(self, graphs=None):
        self._graphs = graphs or []

    def list_graphs(self):
        return list(self._graphs)

    def get_graph(self, gid):
        for g in self._graphs:
            if g.graph_id == gid:
                return g
        raise KeyError(gid)


def _app(engine=None, service=None):
    app = FastAPI()
    app.dependency_overrides[get_engine] = lambda: engine or _FakeEngine()
    app.dependency_overrides[get_service] = lambda: service or _FakeService()
    app.include_router(topology_mod.router, prefix="/topology")
    return app


# ── list_session_channels ──────────────────────────────────────


class TestListChannels:
    def test_success(self, monkeypatch):
        # ``list_channels`` is now async + service-routed.
        async def fake_list(svc, sid):
            return [{"name": "ch"}]

        monkeypatch.setattr(topology_mod.topology_lib, "list_channels", fake_list)
        client = TestClient(_app())
        resp = client.get("/topology/g1/channels")
        assert resp.status_code == 200
        assert resp.json() == [{"name": "ch"}]

    def test_missing(self, monkeypatch):
        async def boom(svc, sid):
            raise KeyError("no")

        monkeypatch.setattr(topology_mod.topology_lib, "list_channels", boom)
        client = TestClient(_app())
        resp = client.get("/topology/ghost/channels")
        assert resp.status_code == 404

    def test_unknown_session_404(self):
        # ``list_channels`` itself returns ``()`` for any unknown id
        # (can't tell "no channels" from "no session"), so the route
        # makes the existence check explicit via ``get_graph``.
        client = TestClient(_app(service=_FakeService(graph=None)))
        resp = client.get("/topology/ghost/channels")
        assert resp.status_code == 404


# ── add_session_channel ────────────────────────────────────────


class TestAddChannel:
    def test_success(self, monkeypatch):
        async def fake_add(svc, sid, name, **kw):
            return {"name": name}

        monkeypatch.setattr(topology_mod.topology_lib, "add_channel", fake_add)
        client = TestClient(_app())
        resp = client.post(
            "/topology/g1/channels", json={"name": "ch", "description": "d"}
        )
        assert resp.status_code == 200
        assert resp.json()["channel"]["name"] == "ch"

    def test_validation_error(self, monkeypatch):
        async def boom(*a, **kw):
            raise ValueError("duplicate")

        monkeypatch.setattr(topology_mod.topology_lib, "add_channel", boom)
        client = TestClient(_app())
        resp = client.post("/topology/g1/channels", json={"name": "ch"})
        assert resp.status_code == 400


# ── get / send channel ─────────────────────────────────────────


class TestGetChannel:
    def test_success(self, monkeypatch):
        async def fake_info(svc, sid, ch):
            return {"name": ch, "listeners": ["alice"]}

        monkeypatch.setattr(topology_mod.topology_lib, "channel_info", fake_info)
        client = TestClient(_app())
        resp = client.get("/topology/g1/channels/ch1")
        assert resp.status_code == 200
        # Route returns the channel_info payload verbatim.
        assert resp.json() == {"name": "ch1", "listeners": ["alice"]}

    def test_session_missing(self, monkeypatch):
        async def boom(svc, sid, ch):
            raise KeyError("no")

        monkeypatch.setattr(topology_mod.topology_lib, "channel_info", boom)
        client = TestClient(_app())
        resp = client.get("/topology/ghost/channels/ch1")
        assert resp.status_code == 404

    def test_channel_missing(self, monkeypatch):
        async def fake_none(svc, sid, ch):
            return None

        monkeypatch.setattr(topology_mod.topology_lib, "channel_info", fake_none)
        client = TestClient(_app())
        resp = client.get("/topology/g1/channels/ghost")
        assert resp.status_code == 404

    def test_unknown_session_404(self):
        # Explicit ``get_graph`` existence check — unknown session 404s
        # before ``channel_info`` is consulted.
        client = TestClient(_app(service=_FakeService(graph=None)))
        resp = client.get("/topology/ghost/channels/ch1")
        assert resp.status_code == 404


class TestSendChannel:
    def test_success(self, monkeypatch):
        async def fake_send(eng, sid, ch, content, sender):
            return "msg-1"

        monkeypatch.setattr(topology_mod.topology_lib, "send_to_channel", fake_send)
        client = TestClient(_app())
        resp = client.post(
            "/topology/g1/channels/ch/send",
            json={"content": "hi", "sender": "alice"},
        )
        assert resp.status_code == 200
        assert resp.json()["message_id"] == "msg-1"

    def test_value_error(self, monkeypatch):
        async def boom(eng, sid, ch, content, sender):
            raise ValueError("bad payload")

        monkeypatch.setattr(topology_mod.topology_lib, "send_to_channel", boom)
        client = TestClient(_app())
        resp = client.post(
            "/topology/g1/channels/ch/send",
            json={"content": "hi"},
        )
        assert resp.status_code == 400


# ── merge_sessions ─────────────────────────────────────────────


class TestMergeSessions:
    # ``merge_sessions`` is now service-routed: it resolves both session
    # ids via ``service.get_graph`` (works for host-local AND
    # worker-hosted graphs) and bridges via ``service.connect``.  The
    # pre-fix route resolved against the host engine, which in lab-host
    # mode is the agent-free coordination engine with NO graphs — so
    # every cross-node merge 404'd.

    def test_missing_ids(self):
        client = TestClient(_app())
        # The route is /{a_session_id}/merge/{b_session_id}; empty path
        # segments leave no route to match → FastAPI 404 (not a 400 from
        # the handler, which never runs).
        resp = client.post("/topology//merge/")
        assert resp.status_code == 404

    def test_same_session(self):
        client = TestClient(_app())
        resp = client.post("/topology/g1/merge/g1")
        assert resp.status_code == 200
        assert resp.json() == {"session_id": "g1", "merged": False}

    def test_first_missing(self):
        svc = _FakeService(graphs={"g1": _FakeGraph("g1", ["c"])})
        client = TestClient(_app(service=svc))
        resp = client.post("/topology/ghost/merge/g1")
        assert resp.status_code == 404

    def test_second_missing(self):
        svc = _FakeService(graphs={"g1": _FakeGraph("g1", ["c"])})
        client = TestClient(_app(service=svc))
        resp = client.post("/topology/g1/merge/ghost")
        assert resp.status_code == 404

    def test_empty_graph(self):
        svc = _FakeService(graphs={"g1": _FakeGraph("g1"), "g2": _FakeGraph("g2")})
        client = TestClient(_app(service=svc))
        resp = client.post("/topology/g1/merge/g2")
        assert resp.status_code == 400

    def test_success_bridges_via_service_connect(self):
        from kohakuterrarium.terrarium.events import ConnectionResult

        svc = _FakeService(
            graphs={
                "g1": _FakeGraph("g1", ["a"]),
                "g2": _FakeGraph("g2", ["b"]),
            },
            connect_result=ConnectionResult(channel="ch", graph_id="g1"),
        )
        client = TestClient(_app(service=svc))
        resp = client.post("/topology/g1/merge/g2")
        assert resp.status_code == 200
        assert resp.json() == {"session_id": "g1", "merged": True}
        # The bridge went through ``service.connect`` — the only path
        # that works cross-node (host-local graph merge is impossible
        # across separate worker processes).
        assert svc.connect_calls == [("a", "b")]

    def test_cross_node_merge_routes_through_service(self):
        # Regression: a multi-node service (lab-host) — both sessions
        # live on workers.  The pre-fix route walked the empty host
        # coordination engine and 404'd; it must resolve both worker
        # graphs through the service and bridge them.
        from kohakuterrarium.terrarium.events import ConnectionResult

        svc = _FakeService(
            graphs={
                "graph_w1": _FakeGraph("graph_w1", ["alice"]),
                "graph_w2": _FakeGraph("graph_w2", ["bob"]),
            },
            connect_result=ConnectionResult(
                channel="alice_to_bob", delta_kind="cross_node"
            ),
        )
        client = TestClient(_app(service=svc))
        resp = client.post("/topology/graph_w1/merge/graph_w2")
        assert (
            resp.status_code == 200
        ), f"cross-node merge 404'd instead of bridging: {resp.text}"
        assert resp.json()["merged"] is True
        assert svc.connect_calls == [("alice", "bob")]


# ── connect / disconnect ───────────────────────────────────────


class TestConnectDisconnect:
    def test_connect_success(self, monkeypatch):
        captured = {}

        async def fake_connect(svc, s, r, **kw):
            captured["sender"] = s
            captured["receiver"] = r
            return {"channel": "ch", "delta_kind": "nothing"}

        monkeypatch.setattr(topology_mod.topology_lib, "connect", fake_connect)
        client = TestClient(_app())
        resp = client.post(
            "/topology/g1/connect", json={"sender": "a", "receiver": "b"}
        )
        assert resp.status_code == 200
        # Route forwards sender/receiver and returns the lib result verbatim.
        assert captured == {"sender": "a", "receiver": "b"}
        assert resp.json() == {"channel": "ch", "delta_kind": "nothing"}

    def test_connect_value_error(self, monkeypatch):
        async def boom(*a, **kw):
            raise ValueError("bad")

        monkeypatch.setattr(topology_mod.topology_lib, "connect", boom)
        client = TestClient(_app())
        resp = client.post(
            "/topology/g1/connect", json={"sender": "a", "receiver": "b"}
        )
        assert resp.status_code == 400

    def test_disconnect_success(self, monkeypatch):
        async def fake(svc, s, r, **kw):
            return {"channels": ["ch"]}

        monkeypatch.setattr(topology_mod.topology_lib, "disconnect", fake)
        client = TestClient(_app())
        resp = client.post(
            "/topology/g1/disconnect",
            json={"sender": "a", "receiver": "b"},
        )
        assert resp.status_code == 200
        # Route returns the disconnect lib result verbatim.
        assert resp.json() == {"channels": ["ch"]}

    def test_disconnect_value_error(self, monkeypatch):
        async def boom(svc, s, r, **kw):
            raise ValueError("no such channel")

        monkeypatch.setattr(topology_mod.topology_lib, "disconnect", boom)
        client = TestClient(_app())
        resp = client.post(
            "/topology/g1/disconnect", json={"sender": "a", "receiver": "b"}
        )
        assert resp.status_code == 400

    def test_disconnect_key_error(self, monkeypatch):
        async def boom(svc, s, r, **kw):
            raise KeyError("creature gone")

        monkeypatch.setattr(topology_mod.topology_lib, "disconnect", boom)
        client = TestClient(_app())
        resp = client.post(
            "/topology/g1/disconnect", json={"sender": "a", "receiver": "b"}
        )
        assert resp.status_code == 400


# ── wire / unwire ──────────────────────────────────────────────


class TestWireRoutes:
    def test_wire_success(self, monkeypatch):
        async def fake_wire(svc, sid, cid, ch, direction, *, enabled):
            return None

        monkeypatch.setattr(topology_mod.topology_lib, "wire_creature", fake_wire)
        client = TestClient(_app())
        resp = client.post(
            "/topology/g1/creatures/c1/wire",
            json={"channel": "ch", "direction": "listen"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "wired"}

    def test_wire_value_error(self, monkeypatch):
        async def boom(*a, **kw):
            raise ValueError("bad")

        monkeypatch.setattr(topology_mod.topology_lib, "wire_creature", boom)
        client = TestClient(_app())
        resp = client.post(
            "/topology/g1/creatures/c1/wire",
            json={"channel": "ch", "direction": "listen"},
        )
        assert resp.status_code == 400

    def test_unwire_success(self, monkeypatch):
        async def fake(svc, sid, cid, ch, direction, *, enabled):
            return None

        monkeypatch.setattr(topology_mod.topology_lib, "wire_creature", fake)
        client = TestClient(_app())
        resp = client.request(
            "DELETE",
            "/topology/g1/creatures/c1/wire",
            json={"channel": "ch", "direction": "send"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "unwired"}

    def test_unwire_value_error(self, monkeypatch):
        async def boom(*a, **kw):
            raise ValueError("not wired")

        monkeypatch.setattr(topology_mod.topology_lib, "wire_creature", boom)
        client = TestClient(_app())
        resp = client.request(
            "DELETE",
            "/topology/g1/creatures/c1/wire",
            json={"channel": "ch", "direction": "send"},
        )
        assert resp.status_code == 400

    def test_unwire_key_error(self, monkeypatch):
        async def boom(*a, **kw):
            raise KeyError("creature gone")

        monkeypatch.setattr(topology_mod.topology_lib, "wire_creature", boom)
        client = TestClient(_app())
        resp = client.request(
            "DELETE",
            "/topology/g1/creatures/c1/wire",
            json={"channel": "ch", "direction": "send"},
        )
        assert resp.status_code == 400
