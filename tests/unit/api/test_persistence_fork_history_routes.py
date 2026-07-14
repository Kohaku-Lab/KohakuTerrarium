"""Unit tests for the persistence fork + history routes."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.routes.persistence import fork as fork_mod
from kohakuterrarium.api.routes.persistence import history as history_mod


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
        monkeypatch.setattr(history_mod, "live_store_path", lambda svc, n: None)
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
        # (Bug 2). Uses the REAL ``_live_job_ids_for_session`` gather.
        monkeypatch.setattr(
            history_mod, "live_store_path", lambda svc, n: Path("/x/live.kohakutr")
        )
        engine = _FakeEngine(
            graph=_FakeGraph(["root"]),
            creatures={"root": _FakeCreature(_FakeAgent(["job_abc"]))},
        )
        captured = {}

        def fake_payload(p, t, j=None):
            captured["live"] = j
            return {"target": t, "events": []}

        monkeypatch.setattr(history_mod, "history_payload", fake_payload)
        app = _app(history_mod.router)
        app.dependency_overrides[get_service] = lambda: engine
        resp = TestClient(app).get("/api/live_g/history/root")
        assert resp.status_code == 200
        assert captured["live"] == {"job_abc"}
