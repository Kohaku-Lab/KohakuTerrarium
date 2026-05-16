"""Unit tests for :mod:`kohakuterrarium.api.routes.attach.*`."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_engine, get_service
from kohakuterrarium.api.routes.attach import (
    files as attach_files_mod,
    policies as attach_policies_mod,
)

# ── attach/files ──────────────────────────────────────────────


def _files_client() -> TestClient:
    app = FastAPI()
    app.include_router(attach_files_mod.router, prefix="/x")
    return TestClient(app)


class TestAttachFilesRoute:
    def test_get_tree(self, tmp_path):
        (tmp_path / "f.txt").write_text("hi")
        r = _files_client().get(f"/x/tree?root={tmp_path}")
        assert r.status_code == 200
        body = r.json()
        # Root directory node with the one file as a child.
        assert body["type"] == "directory"
        assert body["path"] == str(tmp_path)
        children = {c["name"]: c for c in body["children"]}
        assert children["f.txt"]["type"] == "file"
        assert children["f.txt"]["size"] == 2

    def test_browse_directories(self, tmp_path):
        (tmp_path / "sub").mkdir()
        r = _files_client().get(f"/x/browse?path={tmp_path}")
        assert r.status_code == 200
        body = r.json()
        assert body["current"]["path"] == str(tmp_path)
        assert body["parent"] == str(tmp_path.parent)
        dir_names = {d["name"] for d in body["directories"]}
        assert "sub" in dir_names

    def test_read_file(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("body")
        r = _files_client().get(f"/x/read?path={f}")
        assert r.status_code == 200
        body = r.json()
        assert body["content"] == "body"
        assert body["size"] == 4
        assert body["path"] == str(f)

    def test_write_file(self, tmp_path):
        f = tmp_path / "w.txt"
        r = _files_client().post("/x/write", json={"path": str(f), "content": "hello"})
        assert r.status_code == 200
        assert f.read_text() == "hello"

    def test_rename_file(self, tmp_path):
        a = tmp_path / "a.txt"
        a.write_text("x")
        b = tmp_path / "b.txt"
        r = _files_client().post(
            "/x/rename", json={"old_path": str(a), "new_path": str(b)}
        )
        assert r.status_code == 200
        assert b.exists()

    def test_delete_file(self, tmp_path):
        f = tmp_path / "del.txt"
        f.write_text("x")
        r = _files_client().post("/x/delete", json={"path": str(f)})
        assert r.status_code == 200
        assert not f.exists()

    def test_mkdir(self, tmp_path):
        d = tmp_path / "new" / "nested"
        r = _files_client().post("/x/mkdir", json={"path": str(d)})
        assert r.status_code == 200
        assert d.is_dir()


# ── attach/policies ──────────────────────────────────────────


class _FakeEngine:
    def __init__(self, creatures=None, graphs=None):
        self._creatures = creatures or {}
        self._graphs = graphs or {}

    def get_creature(self, cid):
        if cid not in self._creatures:
            raise KeyError(cid)
        return self._creatures[cid]

    def get_graph(self, gid):
        if gid not in self._graphs:
            raise KeyError(gid)
        return self._graphs[gid]


def _policies_client(engine, service) -> TestClient:
    app = FastAPI()
    app.include_router(attach_policies_mod.router, prefix="/x")
    # The route now reads the host engine off the service's ``engine``
    # attribute (single-host) or via ``connected_nodes`` (multi-node).
    # Stamp ``engine`` onto a single-host SimpleNamespace fake so the
    # local-path branch finds it.
    if not hasattr(service, "connected_nodes") and not hasattr(service, "engine"):
        service.engine = engine
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_service] = lambda: service
    return TestClient(app)


class TestAttachPoliciesRoute:
    def test_creature_not_found_no_multi_node(self):
        engine = _FakeEngine()
        service = SimpleNamespace()  # no _home
        r = _policies_client(engine, service).get("/x/policies/ghost")
        assert r.status_code == 404

    def test_creature_not_found_multi_node_no_route(self):
        engine = _FakeEngine()

        async def policies_fn(cid):
            raise KeyError(cid)

        service = SimpleNamespace(_home={}, attach_policies=policies_fn)
        r = _policies_client(engine, service).get("/x/policies/ghost")
        assert r.status_code == 404

    def test_creature_multi_node_routed(self):
        engine = _FakeEngine()

        async def policies_fn(cid):
            return ["log", "trace"]

        service = SimpleNamespace(
            _home={"cid": "worker-1"}, attach_policies=policies_fn
        )
        r = _policies_client(engine, service).get("/x/policies/cid")
        assert r.status_code == 200
        assert r.json() == {"policies": ["log", "trace"]}

    def test_session_not_found_no_multi_node(self):
        engine = _FakeEngine()
        service = SimpleNamespace()
        r = _policies_client(engine, service).get("/x/session_policies/ghost")
        assert r.status_code == 404

    async def test_creature_local_hit_returns_policy_codes(self):
        # The creature lives on the host engine → the route returns the
        # local policy_lib codes, never touching the service fallback.
        from kohakuterrarium.testing.terrarium import TestTerrariumBuilder

        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            cid = t.get_creature("alice").creature_id
            r = _policies_client(t, SimpleNamespace()).get(f"/x/policies/{cid}")
            assert r.status_code == 200
            # LOG + TRACE are the documented baseline every creature has.
            assert {"log", "trace"} <= set(r.json()["policies"])
        finally:
            await t.shutdown()

    def test_session_not_found_multi_node_no_route(self):
        engine = _FakeEngine()

        async def session_policies_fn(sid):
            raise KeyError(sid)

        service = SimpleNamespace(_home={}, session_attach_policies=session_policies_fn)
        r = _policies_client(engine, service).get("/x/session_policies/ghost")
        assert r.status_code == 404

    async def test_session_local_hit_returns_policy_codes(self):
        from kohakuterrarium.testing.terrarium import TestTerrariumBuilder

        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            gid = t.get_creature("alice").graph_id
            r = _policies_client(t, SimpleNamespace()).get(f"/x/session_policies/{gid}")
            assert r.status_code == 200
            # Sessions always advertise log + observer + trace.
            assert {"log", "observer", "trace"} <= set(r.json()["policies"])
        finally:
            await t.shutdown()

    def test_session_multi_node_routed(self):
        engine = _FakeEngine()

        async def session_policies_fn(sid):
            return ["log", "observer", "trace"]

        service = SimpleNamespace(_home={}, session_attach_policies=session_policies_fn)
        r = _policies_client(engine, service).get("/x/session_policies/sid-1")
        assert r.status_code == 200
        assert r.json() == {"policies": ["log", "observer", "trace"]}

    def test_policies_route_resolves_a_session_id(self):
        # Regression: the frontend Inspector hits
        # ``/api/attach/policies/<id>`` where ``<id>`` is a session /
        # graph id, not a creature id. In lab-host mode the host engine
        # has neither, so the route 404s — the reported
        # ``GET /api/attach/policies/graph_... 404``. The route must
        # fall back to session-policy resolution for a graph id rather
        # than dead-ending on the creature lookup.
        engine = _FakeEngine()

        async def creature_policies_fn(cid):
            raise KeyError(cid)  # it's not a creature id

        async def session_policies_fn(sid):
            return ["log", "observer", "trace"]  # it IS a known graph

        service = SimpleNamespace(
            _home={},
            attach_policies=creature_policies_fn,
            session_attach_policies=session_policies_fn,
        )
        r = _policies_client(engine, service).get("/x/policies/graph_abc123")
        assert r.status_code == 200, (
            f"/policies/<session_id> returned {r.status_code} — the "
            "reported attach/policies 404 for a live worker session"
        )
        assert r.json() == {"policies": ["log", "observer", "trace"]}
