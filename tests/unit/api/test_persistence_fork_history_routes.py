"""Unit tests for the persistence fork + history routes."""

import types
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.persistence import fork as fork_mod
from kohakuterrarium.api.routes.persistence import history as history_mod
from kohakuterrarium.session.store import SessionStore


def _app(router) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


class _FakeAgent:
    def __init__(self, job_ids):
        self._direct_job_meta = {jid: {} for jid in job_ids}


class _FakeCreature:
    def __init__(self, agent):
        self.agent = agent


class _FakeGraph:
    def __init__(self, creature_ids):
        self.creature_ids = list(creature_ids)


class _FakeEngine:
    """Minimal host engine: not a ``TerrariumService`` Protocol instance,
    so ``host_engine_or_none`` treats it as the engine directly."""

    def __init__(self, graph, creatures):
        self._graph = graph
        self._creatures = creatures

    def get_graph(self, session_name):
        return self._graph

    def get_creature(self, creature_id):
        return self._creatures[creature_id]


# ── fork ────────────────────────────────────────────────────────


class TestForkRoute:
    def test_session_missing(self, monkeypatch):
        monkeypatch.setattr(fork_mod, "resolve_session_path_default", lambda n: None)
        client = TestClient(_app(fork_mod.router))
        resp = client.post(
            "/api/ghost/fork",
            json={"at_event_id": 5},
        )
        assert resp.status_code == 404

    def test_success(self, monkeypatch):
        monkeypatch.setattr(
            fork_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )

        async def fake_fork(path, **kwargs):
            return {
                "session_id": "s-fork-1",
                "fork_point": kwargs["at_event_id"],
                "path": "/x/s-fork-1.kohakutr.v2",
            }

        monkeypatch.setattr(fork_mod, "fork_session_handler", fake_fork)
        client = TestClient(_app(fork_mod.router))
        resp = client.post(
            "/api/sess/fork",
            json={"at_event_id": 5, "name": "branch-x"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["session_id"] == "s-fork-1"
        assert body["fork_point"] == 5

    def test_live_fork_reuses_the_attached_store(self, monkeypatch, tmp_path):
        # Forking a LIVE session (by graph_id or file stem) must go
        # through the engine's open store — a second open of the
        # actively-written source IOERRs on POSIX. Both source-open
        # entry points are bombed; the REAL fork runs.
        from kohakuterrarium.studio.persistence import fork as fork_handler_mod

        store_path = tmp_path / "alice_3f2a9c11.kohakutr"
        store = SessionStore(str(store_path))
        store.init_meta("alice", "agent", "/p", "/w", ["alice"])
        store.append_event("alice", "user_message", {"content": "hi"})
        store.checkpoint()
        engine = _FakeEngine(graph=_FakeGraph([]), creatures={})
        engine._session_stores = {"graph_live1": store}

        def _bomb(*a, **k):
            raise AssertionError("live fork must not open the source session file")

        monkeypatch.setattr(fork_mod, "resolve_session_path_default", _bomb)
        monkeypatch.setattr(fork_handler_mod, "SessionStore", _bomb)

        app = _app(fork_mod.router)
        app.dependency_overrides[get_service] = lambda: engine
        client = TestClient(app)
        try:
            resp = client.post(
                "/api/alice_3f2a9c11/fork",
                json={"at_event_id": 1, "name": "pin-fork"},
            )
            assert resp.status_code == 201, resp.text
            assert resp.json()["fork_point"] == 1
            # The child landed on disk as a REAL fork of the live source.
            assert Path(resp.json()["path"]).exists()
        finally:
            store.close()


# ── history ─────────────────────────────────────────────────────


class TestHistoryRoutes:
    def test_index_missing(self, monkeypatch):
        monkeypatch.setattr(history_mod, "resolve_session_path_default", lambda n: None)
        client = TestClient(_app(history_mod.router))
        resp = client.get("/api/ghost/history")
        assert resp.status_code == 404

    def test_index_success(self, monkeypatch):
        monkeypatch.setattr(
            history_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )
        monkeypatch.setattr(
            history_mod,
            "history_index_payload",
            lambda p: {"session_name": "s", "targets": ["a", "b"]},
        )
        client = TestClient(_app(history_mod.router))
        resp = client.get("/api/sess/history")
        assert resp.status_code == 200
        assert resp.json()["targets"] == ["a", "b"]

    def test_target_missing(self, monkeypatch):
        monkeypatch.setattr(history_mod, "resolve_session_path_default", lambda n: None)
        client = TestClient(_app(history_mod.router))
        resp = client.get("/api/ghost/history/alice")
        assert resp.status_code == 404

    def test_target_success(self, monkeypatch):
        monkeypatch.setattr(
            history_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )
        monkeypatch.setattr(
            history_mod,
            "history_payload",
            lambda p, t, j=None: {"target": t, "events": []},
        )
        client = TestClient(_app(history_mod.router))
        resp = client.get("/api/sess/history/alice")
        assert resp.status_code == 200
        assert resp.json()["target"] == "alice"

    def test_target_unquoted(self, monkeypatch):
        monkeypatch.setattr(
            history_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )

        def fake_payload(p, t, j=None):
            return {"target": t, "events": []}

        monkeypatch.setattr(history_mod, "history_payload", fake_payload)
        client = TestClient(_app(history_mod.router))
        # URL-encoded "a:b" → "a%3Ab"
        resp = client.get("/api/sess/history/a%3Ab")
        assert resp.status_code == 200
        assert resp.json()["target"] == "a:b"

    def test_saved_target_passes_no_live_job_ids(self, monkeypatch):
        # A genuinely saved session (no live store) threads ``None`` so
        # the read-only interrupted-synthesis semantics are unchanged.
        monkeypatch.setattr(history_mod, "live_store_entry", lambda svc, n: None)
        monkeypatch.setattr(
            history_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )
        captured = {}

        def fake_payload(p, t, j=None):
            captured["live"] = j
            return {"target": t, "events": []}

        monkeypatch.setattr(history_mod, "history_payload", fake_payload)
        client = TestClient(_app(history_mod.router))
        resp = client.get("/api/sess/history/root")
        assert resp.status_code == 200
        assert captured["live"] is None

    def test_live_target_threads_running_job_ids(self, monkeypatch):
        # A live-resolved session gathers the still-running job ids from
        # the host engine's live agents and threads them into the payload
        # so an in-flight sub-agent isn't synthesised as interrupted
        # (Bug 2). Uses the REAL ``_live_job_ids_for_graph`` gather and
        # must build from the ENGINE'S store, named by its file stem.
        fake_store = types.SimpleNamespace(_path="/x/live.kohakutr")
        monkeypatch.setattr(
            history_mod, "live_store_entry", lambda svc, n: ("live_g", fake_store)
        )
        engine = _FakeEngine(
            graph=_FakeGraph(["root"]),
            creatures={"root": _FakeCreature(_FakeAgent(["job_abc"]))},
        )
        captured = {}

        def fake_from_store(store, name, target, j=None):
            captured["store"] = store
            captured["name"] = name
            captured["live"] = j
            return {"target": target, "events": []}

        monkeypatch.setattr(history_mod, "history_from_store", fake_from_store)
        app = _app(history_mod.router)
        app.dependency_overrides[get_service] = lambda: engine
        resp = TestClient(app).get("/api/live_g/history/root")
        assert resp.status_code == 200
        assert captured["live"] == {"job_abc"}
        assert captured["store"] is fake_store
        assert captured["name"] == "live"

    def test_live_history_never_reopens_the_store_file(self, monkeypatch, tmp_path):
        # THE CI bug (POSIX): a second SessionStore open of the live,
        # actively-written file fails with SQLITE_IOERR. While the
        # session is live, history — addressed by graph_id OR by the
        # store's file stem — must reuse the engine's open store, so
        # every disk-open entry point is bombed.
        store_path = tmp_path / "alice_3f2a9c11.kohakutr"
        store = SessionStore(str(store_path))
        store.init_meta("alice", "agent", "/p", "/w", ["alice"])
        store.checkpoint()
        engine = _FakeEngine(graph=_FakeGraph([]), creatures={})
        engine._session_stores = {"graph_live1": store}

        def _bomb(*a, **k):
            raise AssertionError("live history must not open the session file")

        monkeypatch.setattr(history_mod, "resolve_session_path_default", _bomb)
        monkeypatch.setattr(history_mod, "history_index_payload", _bomb)
        monkeypatch.setattr(history_mod, "history_payload", _bomb)

        app = _app(history_mod.router)
        app.dependency_overrides[get_service] = lambda: engine
        client = TestClient(app)
        try:
            for name in ("graph_live1", "alice_3f2a9c11"):
                index = client.get(f"/api/{name}/history")
                assert index.status_code == 200, (name, index.text)
                assert index.json()["session_name"] == "alice_3f2a9c11"
                target = client.get(f"/api/{name}/history/alice")
                assert target.status_code == 200, (name, target.text)
        finally:
            store.close()
