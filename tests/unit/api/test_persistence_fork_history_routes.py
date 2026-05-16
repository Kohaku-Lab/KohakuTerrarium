"""Unit tests for the persistence fork + history routes."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.persistence import fork as fork_mod
from kohakuterrarium.api.routes.persistence import history as history_mod


def _app(router) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


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
            lambda p, t: {"target": t, "events": []},
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

        def fake_payload(p, t):
            return {"target": t, "events": []}

        monkeypatch.setattr(history_mod, "history_payload", fake_payload)
        client = TestClient(_app(history_mod.router))
        # URL-encoded "a:b" → "a%3Ab"
        resp = client.get("/api/sess/history/a%3Ab")
        assert resp.status_code == 200
        assert resp.json()["target"] == "a:b"
