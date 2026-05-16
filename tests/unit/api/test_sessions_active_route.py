"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2.active`."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.sessions_v2 import active as active_mod
from kohakuterrarium.studio.sessions.handles import Session, SessionListing

_SENTINEL = object()


def _session(
    *,
    session_id="s1",
    name="alice",
    creatures=_SENTINEL,
    channels=None,
    has_root=False,
):
    if creatures is _SENTINEL:
        creatures = [{"creature_id": "cid-1", "name": "alice"}]
    return Session(
        session_id=session_id,
        name=name,
        creatures=creatures,
        channels=channels or [],
        has_root=has_root,
    )


def _afind(val):
    """Async stub for the now-async ``find_session_for_creature``."""

    async def _f(svc, cid):
        return val

    return _f


class _FakeService:
    pass


def _client(monkeypatch_responses=None):
    """Build a TestClient with active_mod.router mounted; install fake
    lifecycle responses via the returned monkeypatch helper."""
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: _FakeService()
    app.include_router(active_mod.router, prefix="")
    return TestClient(app)


# ── create_creature_session ────────────────────────────────────


class TestCreateCreatureSession:
    def test_success(self, monkeypatch):
        async def fake_start_creature(service, **kw):
            return _session()

        monkeypatch.setattr(active_mod.lifecycle, "start_creature", fake_start_creature)
        # The router has no prefix; the @router.post('/creature')
        # endpoint sits at "/creature".
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: _FakeService()
        app.include_router(active_mod.router, prefix="/active")
        client = TestClient(app)
        resp = client.post("/active/creature", json={"config_path": "/x"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "running"

    def test_value_error(self, monkeypatch):
        async def boom(service, **kw):
            raise ValueError("bad config")

        monkeypatch.setattr(active_mod.lifecycle, "start_creature", boom)
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: _FakeService()
        app.include_router(active_mod.router, prefix="/active")
        client = TestClient(app)
        resp = client.post("/active/creature", json={"config_path": "/x"})
        assert resp.status_code == 400


# ── create_terrarium_session ───────────────────────────────────


class TestCreateTerrariumSession:
    def _setup_app(self):
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: _FakeService()
        app.include_router(active_mod.router, prefix="/active")
        return app

    def test_success(self, monkeypatch):
        async def fake(service, **kw):
            return _session(session_id="s1", name="alice")

        monkeypatch.setattr(active_mod.lifecycle, "start_terrarium", fake)
        client = TestClient(self._setup_app())
        resp = client.post("/active/terrarium", json={"config_path": "/x"})
        assert resp.status_code == 200
        body = resp.json()
        # Returns the session dict with status=running spliced in.
        assert body["status"] == "running"
        assert body["session_id"] == "s1"
        assert body["name"] == "alice"

    def test_remote_node_not_implemented(self):
        client = TestClient(self._setup_app())
        resp = client.post(
            "/active/terrarium",
            json={"config_path": "/x", "on_node": "worker-1"},
        )
        assert resp.status_code == 501

    def test_value_error(self, monkeypatch):
        async def boom(service, **kw):
            raise ValueError("bad recipe")

        monkeypatch.setattr(active_mod.lifecycle, "start_terrarium", boom)
        client = TestClient(self._setup_app())
        resp = client.post("/active/terrarium", json={"config_path": "/x"})
        assert resp.status_code == 400


# ── legacy creation aliases ────────────────────────────────────


class TestLegacyCreateAgent:
    def _app(self):
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: _FakeService()
        app.include_router(active_mod.router, prefix="/active")
        return app

    def test_success(self, monkeypatch):
        async def fake(service, **kw):
            return _session()

        monkeypatch.setattr(active_mod.lifecycle, "start_creature", fake)
        client = TestClient(self._app())
        resp = client.post("/active/agents", json={"config_path": "/x"})
        assert resp.status_code == 200
        assert resp.json()["agent_id"] == "cid-1"

    def test_empty_creatures(self, monkeypatch):
        async def fake(service, **kw):
            return _session(creatures=[])

        monkeypatch.setattr(active_mod.lifecycle, "start_creature", fake)
        client = TestClient(self._app())
        resp = client.post("/active/agents", json={"config_path": "/x"})
        assert resp.status_code == 200
        assert resp.json()["agent_id"] == ""

    def test_value_error(self, monkeypatch):
        async def boom(service, **kw):
            raise ValueError("bad config")

        monkeypatch.setattr(active_mod.lifecycle, "start_creature", boom)
        client = TestClient(self._app())
        resp = client.post("/active/agents", json={"config_path": "/x"})
        assert resp.status_code == 400


class TestLegacyCreateTerrarium:
    def _app(self):
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: _FakeService()
        app.include_router(active_mod.router, prefix="/active")
        return app

    def test_success(self, monkeypatch):
        async def fake(service, **kw):
            return _session(session_id="g1")

        monkeypatch.setattr(active_mod.lifecycle, "start_terrarium", fake)
        client = TestClient(self._app())
        resp = client.post("/active/terrariums", json={"config_path": "/x"})
        assert resp.status_code == 200
        assert resp.json()["terrarium_id"] == "g1"

    def test_remote_node_not_implemented(self):
        client = TestClient(self._app())
        resp = client.post(
            "/active/terrariums",
            json={"config_path": "/x", "on_node": "worker-1"},
        )
        assert resp.status_code == 501

    def test_value_error(self, monkeypatch):
        async def boom(service, **kw):
            raise ValueError("bad")

        monkeypatch.setattr(active_mod.lifecycle, "start_terrarium", boom)
        client = TestClient(self._app())
        resp = client.post("/active/terrariums", json={"config_path": "/x"})
        assert resp.status_code == 400


# ── rename ──────────────────────────────────────────────────────


class TestRenameRoutes:
    def _app(self):
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: _FakeService()
        app.include_router(active_mod.router, prefix="/active")
        return app

    def test_rename_agent_success(self, monkeypatch):
        monkeypatch.setattr(
            active_mod.lifecycle,
            "rename_creature",
            lambda svc, cid, name: {"creature_id": cid, "name": name},
        )
        client = TestClient(self._app())
        resp = client.post("/active/agents/cid-1/rename", json={"name": "new"})
        assert resp.status_code == 200
        # Route returns the lifecycle result verbatim — the rename took
        # effect on the targeted creature id.
        assert resp.json() == {"creature_id": "cid-1", "name": "new"}

    def test_rename_agent_missing(self, monkeypatch):
        def boom(svc, cid, name):
            raise KeyError("not found")

        monkeypatch.setattr(active_mod.lifecycle, "rename_creature", boom)
        client = TestClient(self._app())
        resp = client.post("/active/agents/ghost/rename", json={"name": "x"})
        assert resp.status_code == 404

    def test_rename_agent_value_error(self, monkeypatch):
        def boom(svc, cid, name):
            raise ValueError("bad name")

        monkeypatch.setattr(active_mod.lifecycle, "rename_creature", boom)
        client = TestClient(self._app())
        resp = client.post("/active/agents/cid-1/rename", json={"name": ""})
        assert resp.status_code == 400

    def test_rename_terrarium(self, monkeypatch):
        monkeypatch.setattr(
            active_mod.lifecycle,
            "rename_session",
            lambda svc, sid, name: _session(session_id=sid, name=name),
        )
        client = TestClient(self._app())
        resp = client.post("/active/terrariums/g1/rename", json={"name": "renamed"})
        assert resp.status_code == 200
        # Route projects the renamed session to {session_id, name}.
        assert resp.json() == {"session_id": "g1", "name": "renamed"}

    def test_rename_terrarium_missing(self, monkeypatch):
        def boom(svc, sid, name):
            raise KeyError()

        monkeypatch.setattr(active_mod.lifecycle, "rename_session", boom)
        client = TestClient(self._app())
        resp = client.post("/active/terrariums/ghost/rename", json={"name": "x"})
        assert resp.status_code == 404

    def test_rename_terrarium_value_error(self, monkeypatch):
        def boom(svc, sid, name):
            raise ValueError("name already taken")

        monkeypatch.setattr(active_mod.lifecycle, "rename_session", boom)
        client = TestClient(self._app())
        resp = client.post("/active/terrariums/g1/rename", json={"name": "dup"})
        assert resp.status_code == 400

    def test_rename_session_creature(self, monkeypatch):
        monkeypatch.setattr(
            active_mod.lifecycle,
            "rename_creature",
            lambda svc, cid, name: {"creature_id": cid, "name": name},
        )
        client = TestClient(self._app())
        resp = client.post("/active/g1/creatures/cid-1/rename", json={"name": "x"})
        assert resp.status_code == 200
        assert resp.json() == {"creature_id": "cid-1", "name": "x"}

    def test_rename_session_creature_missing(self, monkeypatch):
        def boom(svc, cid, name):
            raise KeyError("gone")

        monkeypatch.setattr(active_mod.lifecycle, "rename_creature", boom)
        client = TestClient(self._app())
        resp = client.post("/active/g1/creatures/ghost/rename", json={"name": "x"})
        assert resp.status_code == 404

    def test_rename_session_creature_value_error(self, monkeypatch):
        def boom(svc, cid, name):
            raise ValueError("empty name")

        monkeypatch.setattr(active_mod.lifecycle, "rename_creature", boom)
        client = TestClient(self._app())
        resp = client.post("/active/g1/creatures/cid-1/rename", json={"name": ""})
        assert resp.status_code == 400


# ── delete / stop ───────────────────────────────────────────────


class TestStopRoutes:
    def _app(self):
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: _FakeService()
        app.include_router(active_mod.router, prefix="/active")
        return app

    def test_stop_creature_unknown(self, monkeypatch):
        monkeypatch.setattr(
            active_mod.lifecycle, "find_session_for_creature", _afind(None)
        )
        client = TestClient(self._app())
        resp = client.delete("/active/agents/ghost")
        assert resp.status_code == 404

    def test_stop_creature_success(self, monkeypatch):
        monkeypatch.setattr(
            active_mod.lifecycle, "find_session_for_creature", _afind("g1")
        )
        stopped = []

        async def fake_stop(svc, sid):
            stopped.append(sid)
            return None

        monkeypatch.setattr(active_mod.lifecycle, "stop_session", fake_stop)
        client = TestClient(self._app())
        resp = client.delete("/active/agents/cid-1")
        assert resp.status_code == 200
        assert resp.json() == {"status": "stopped"}
        # The creature id was resolved to its session, which was stopped.
        assert stopped == ["g1"]

    def test_stop_terrarium(self, monkeypatch):
        stopped = []

        async def fake_stop(svc, sid):
            stopped.append(sid)
            return None

        monkeypatch.setattr(active_mod.lifecycle, "stop_session", fake_stop)
        client = TestClient(self._app())
        resp = client.delete("/active/terrariums/g1")
        assert resp.status_code == 200
        assert resp.json() == {"status": "stopped"}
        assert stopped == ["g1"]

    def test_stop_terrarium_missing(self, monkeypatch):
        async def boom(svc, sid):
            raise KeyError("nope")

        monkeypatch.setattr(active_mod.lifecycle, "stop_session", boom)
        client = TestClient(self._app())
        resp = client.delete("/active/terrariums/ghost")
        assert resp.status_code == 404

    def test_stop_creature_resolved_but_stop_raises(self, monkeypatch):
        # The creature resolves to a session, but stop_session then
        # raises KeyError (a removal race) → 404, not a 500.
        monkeypatch.setattr(
            active_mod.lifecycle, "find_session_for_creature", _afind("g1")
        )

        async def boom(svc, sid):
            raise KeyError("gone")

        monkeypatch.setattr(active_mod.lifecycle, "stop_session", boom)
        client = TestClient(self._app())
        resp = client.delete("/active/agents/cid-1")
        assert resp.status_code == 404

    def test_stop_session_unified(self, monkeypatch):
        stopped = []

        async def fake_stop(svc, sid):
            stopped.append(sid)
            return None

        monkeypatch.setattr(active_mod.lifecycle, "stop_session", fake_stop)
        client = TestClient(self._app())
        resp = client.delete("/active/some-id")
        assert resp.status_code == 200
        assert resp.json() == {"status": "stopped"}
        assert stopped == ["some-id"]

    def test_stop_session_unified_missing(self, monkeypatch):
        async def boom(svc, sid):
            raise KeyError("nope")

        monkeypatch.setattr(active_mod.lifecycle, "stop_session", boom)
        client = TestClient(self._app())
        resp = client.delete("/active/ghost-id")
        assert resp.status_code == 404


# ── list / get sessions ────────────────────────────────────────


class TestListGetSessions:
    def _app(self):
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: _FakeService()
        app.include_router(active_mod.router, prefix="/active")
        return app

    def test_list_agents_legacy(self, monkeypatch):
        # A 1-creature session shows in the legacy /agents list.
        listing = SessionListing(session_id="g1", name="x", creatures=1)
        monkeypatch.setattr(
            active_mod.lifecycle, "list_sessions", lambda svc: [listing]
        )
        monkeypatch.setattr(
            active_mod.lifecycle,
            "get_session",
            lambda svc, sid: _session(session_id=sid),
        )
        client = TestClient(self._app())
        resp = client.get("/active/agents")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["graph_id"] == "g1"
        assert body[0]["agent_id"] == "cid-1"

    def test_list_terrariums_legacy(self, monkeypatch):
        # A 3-creature session shows in the legacy /terrariums list and
        # NOT in /agents (creatures != 1).
        listing = SessionListing(session_id="g1", name="x", creatures=3)
        monkeypatch.setattr(
            active_mod.lifecycle, "list_sessions", lambda svc: [listing]
        )
        monkeypatch.setattr(
            active_mod.lifecycle,
            "get_session",
            lambda svc, sid: _session(
                session_id=sid,
                creatures=[
                    {"creature_id": "c1", "name": "alice"},
                    {"creature_id": "c2", "name": "bob"},
                    {"creature_id": "c3", "name": "carol"},
                ],
            ),
        )
        client = TestClient(self._app())
        resp = client.get("/active/terrariums")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["terrarium_id"] == "g1"
        assert set(body[0]["creatures"]) == {"alice", "bob", "carol"}
        # The same multi-creature session is excluded from /agents.
        assert client.get("/active/agents").json() == []

    def test_list_terrariums_excludes_solo_sessions(self, monkeypatch):
        # A 1-creature session is skipped by the /terrariums legacy
        # list (the ``creatures < 2`` continue) — only multi-creature
        # sessions survive.
        solo = SessionListing(session_id="solo", name="s", creatures=1)
        multi = SessionListing(session_id="multi", name="m", creatures=2)
        monkeypatch.setattr(
            active_mod.lifecycle, "list_sessions", lambda svc: [solo, multi]
        )
        monkeypatch.setattr(
            active_mod.lifecycle,
            "get_session",
            lambda svc, sid: _session(
                session_id=sid,
                creatures=[
                    {"creature_id": "c1", "name": "alice"},
                    {"creature_id": "c2", "name": "bob"},
                ],
            ),
        )
        client = TestClient(self._app())
        body = client.get("/active/terrariums").json()
        ids = {t["terrarium_id"] for t in body}
        assert ids == {"multi"}

    def test_get_agent_status_success(self, monkeypatch):
        monkeypatch.setattr(
            active_mod.lifecycle, "get_session", lambda svc, sid: _session()
        )
        client = TestClient(self._app())
        resp = client.get("/active/agents/cid-1")
        assert resp.status_code == 200
        body = resp.json()
        # Legacy agent shape: primary creature fields + agent_id +
        # graph roster.
        assert body["agent_id"] == "cid-1"
        assert body["graph_id"] == "s1"
        assert body["graph_creature_count"] == 1

    def test_get_agent_status_via_creature_id(self, monkeypatch):
        # First lookup raises; resolver falls back via find_session_for_creature.
        def fake_get(svc, sid):
            if sid == "g1":
                return _session()
            raise KeyError(sid)

        monkeypatch.setattr(active_mod.lifecycle, "get_session", fake_get)
        monkeypatch.setattr(
            active_mod.lifecycle,
            "find_session_for_creature",
            _afind("g1"),
        )
        client = TestClient(self._app())
        resp = client.get("/active/agents/cid-1")
        assert resp.status_code == 200

    def test_get_agent_status_not_found(self, monkeypatch):
        def boom(svc, sid):
            raise KeyError()

        monkeypatch.setattr(active_mod.lifecycle, "get_session", boom)
        monkeypatch.setattr(
            active_mod.lifecycle,
            "find_session_for_creature",
            _afind(None),
        )
        client = TestClient(self._app())
        resp = client.get("/active/agents/ghost")
        assert resp.status_code == 404

    def test_get_terrarium_session(self, monkeypatch):
        monkeypatch.setattr(
            active_mod.lifecycle, "get_session", lambda svc, sid: _session()
        )
        client = TestClient(self._app())
        resp = client.get("/active/terrariums/g1")
        assert resp.status_code == 200
        body = resp.json()
        # Legacy terrarium shape: keyed by terrarium_id + running flag.
        assert body["terrarium_id"] == "s1"
        assert body["running"] is True
        assert body["name"] == "alice"

    def test_get_terrarium_session_not_found(self, monkeypatch):
        def boom(svc, sid):
            raise KeyError(sid)

        monkeypatch.setattr(active_mod.lifecycle, "get_session", boom)
        monkeypatch.setattr(
            active_mod.lifecycle, "find_session_for_creature", _afind(None)
        )
        client = TestClient(self._app())
        resp = client.get("/active/terrariums/ghost")
        assert resp.status_code == 404

    def test_get_session_unified_not_found(self, monkeypatch):
        def boom(svc, sid):
            raise KeyError(sid)

        monkeypatch.setattr(active_mod.lifecycle, "get_session", boom)
        monkeypatch.setattr(
            active_mod.lifecycle, "find_session_for_creature", _afind(None)
        )
        client = TestClient(self._app())
        resp = client.get("/active/ghost")
        assert resp.status_code == 404

    def test_list_active_sessions(self, monkeypatch):
        listing = SessionListing(session_id="g1", name="x", creatures=1)
        monkeypatch.setattr(
            active_mod.lifecycle, "list_sessions", lambda svc: [listing]
        )
        client = TestClient(self._app())
        resp = client.get("/active")
        assert resp.status_code == 200
        # Canonical list returns each listing's to_dict().
        assert resp.json() == [listing.to_dict()]

    def test_get_session_unified(self, monkeypatch):
        monkeypatch.setattr(
            active_mod.lifecycle, "get_session", lambda svc, sid: _session()
        )
        client = TestClient(self._app())
        resp = client.get("/active/g1")
        assert resp.status_code == 200
        # Canonical getter returns the session's to_dict() verbatim.
        assert resp.json() == _session().to_dict()


# ── per-session creatures CRUD ─────────────────────────────────


class TestSessionCreatureCrud:
    def _app(self):
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: _FakeService()
        app.include_router(active_mod.router, prefix="/active")
        return app

    def test_list_creatures(self, monkeypatch):
        monkeypatch.setattr(
            active_mod.lifecycle,
            "list_creatures",
            lambda svc, sid: [{"creature_id": "c1"}],
        )
        client = TestClient(self._app())
        resp = client.get("/active/g1/creatures")
        assert resp.status_code == 200
        # Route returns the lifecycle creature list verbatim.
        assert resp.json() == [{"creature_id": "c1"}]

    def test_list_creatures_missing(self, monkeypatch):
        def boom(svc, sid):
            raise KeyError("no")

        monkeypatch.setattr(active_mod.lifecycle, "list_creatures", boom)
        client = TestClient(self._app())
        resp = client.get("/active/ghost/creatures")
        assert resp.status_code == 404

    def test_add_creature(self, monkeypatch):
        # Regression test for B-active-1 (fixed): the route used to
        # build CreatureConfig(config_path=...) — a field that does not
        # exist — and 500 with a TypeError before reaching lifecycle.
        # It now builds a valid CreatureConfig(config_data, base_dir).
        captured = {}

        async def fake_add(svc, sid, cfg):
            captured["cfg"] = cfg
            return "new-cid"

        monkeypatch.setattr(active_mod.lifecycle, "add_creature", fake_add)
        client = TestClient(self._app())
        resp = client.post(
            "/active/g1/creatures",
            json={"name": "alice", "config_path": "/x"},
        )
        assert resp.status_code == 200
        assert resp.json()["creature_id"] == "new-cid"
        # The route handed lifecycle a well-formed CreatureConfig: the
        # request path is carried as a base_config reference.
        cfg = captured["cfg"]
        assert cfg.name == "alice"
        assert cfg.config_data["base_config"] == "/x"

    def test_add_creature_value_error(self, monkeypatch):
        async def boom(svc, sid, cfg):
            raise ValueError("bad")

        monkeypatch.setattr(active_mod.lifecycle, "add_creature", boom)
        client = TestClient(self._app())
        resp = client.post(
            "/active/g1/creatures",
            json={"name": "alice", "config_path": "/x"},
        )
        assert resp.status_code == 400

    def test_remove_creature_success(self, monkeypatch):
        removed = []

        async def fake_remove(svc, sid, cid):
            removed.append((sid, cid))
            return True

        monkeypatch.setattr(active_mod.lifecycle, "remove_creature", fake_remove)
        client = TestClient(self._app())
        resp = client.delete("/active/g1/creatures/cid-1")
        assert resp.status_code == 200
        assert resp.json() == {"status": "removed"}
        # The (session, creature) pair was forwarded to the lifecycle op.
        assert removed == [("g1", "cid-1")]

    def test_remove_creature_not_removed(self, monkeypatch):
        async def fake_remove(svc, sid, cid):
            return False

        monkeypatch.setattr(active_mod.lifecycle, "remove_creature", fake_remove)
        client = TestClient(self._app())
        resp = client.delete("/active/g1/creatures/ghost")
        assert resp.status_code == 404

    def test_remove_creature_key_error(self, monkeypatch):
        async def boom(svc, sid, cid):
            raise KeyError("no")

        monkeypatch.setattr(active_mod.lifecycle, "remove_creature", boom)
        client = TestClient(self._app())
        resp = client.delete("/active/g1/creatures/cid")
        assert resp.status_code == 404


# ── private helpers ────────────────────────────────────────────


class TestPrivateHelpers:
    def test_session_legacy_agent_response_basic(self):
        sess = _session()
        out = active_mod._session_legacy_agent_response(sess)
        assert out["agent_id"] == "cid-1"
        assert out["graph_creature_count"] == 1

    def test_session_legacy_agent_response_with_root(self):
        sess = _session(has_root=True)
        out = active_mod._session_legacy_agent_response(sess)
        assert out["has_root"] is True

    def test_session_legacy_terrarium_response_with_root(self):
        sess = _session(
            has_root=True,
            creatures=[
                {"name": "root", "model": "x", "pwd": "/p"},
                {"name": "alice"},
            ],
        )
        out = active_mod._session_legacy_terrarium_response(sess)
        assert out["root_model"] == "x"

    async def test_resolve_session(self, monkeypatch):
        monkeypatch.setattr(
            active_mod.lifecycle, "get_session", lambda svc, sid: _session()
        )
        out = await active_mod._resolve_session(_FakeService(), "g1")
        assert out.session_id == "s1"

    async def test_resolve_session_via_creature(self, monkeypatch):
        def fake_get(svc, sid):
            if sid == "g1":
                return _session()
            raise KeyError(sid)

        monkeypatch.setattr(active_mod.lifecycle, "get_session", fake_get)
        monkeypatch.setattr(
            active_mod.lifecycle,
            "find_session_for_creature",
            _afind("g1"),
        )
        out = await active_mod._resolve_session(_FakeService(), "cid-1")
        assert out.session_id == "s1"

    async def test_resolve_session_not_found(self, monkeypatch):
        def boom(svc, sid):
            raise KeyError(sid)

        monkeypatch.setattr(active_mod.lifecycle, "get_session", boom)
        monkeypatch.setattr(
            active_mod.lifecycle,
            "find_session_for_creature",
            _afind(None),
        )
        with pytest.raises(KeyError):
            await active_mod._resolve_session(_FakeService(), "ghost")
