"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2.memory`."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.sessions_v2 import memory as mem_mod


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(mem_mod.router, prefix="/api")
    return app


class TestMemorySearchRoute:
    def test_session_missing(self, monkeypatch):
        monkeypatch.setattr(mem_mod, "resolve_session_path_default", lambda n: None)
        client = TestClient(_app())
        resp = client.get("/api/ghost/memory/search?q=hello")
        assert resp.status_code == 404

    def test_search_called(self, monkeypatch):
        monkeypatch.setattr(
            mem_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )

        captured = {}

        async def fake_search(path, *, q, mode, k, agent, engine):
            captured.update(
                {"path": path, "q": q, "mode": mode, "k": k, "agent": agent}
            )
            return {"hits": [{"snippet": "hello"}]}

        monkeypatch.setattr(mem_mod, "search_session_memory", fake_search)
        monkeypatch.setattr(mem_mod, "host_engine_or_none", lambda svc: None)

        client = TestClient(_app())
        resp = client.get("/api/sess/memory/search?q=hello&mode=fts&k=5&agent=alice")
        assert resp.status_code == 200
        assert resp.json()["hits"][0]["snippet"] == "hello"
        assert captured["q"] == "hello"
        assert captured["mode"] == "fts"
        assert captured["k"] == 5
        assert captured["agent"] == "alice"

    def test_default_mode(self, monkeypatch):
        monkeypatch.setattr(
            mem_mod,
            "resolve_session_path_default",
            lambda n: Path("/x/s.kohakutr"),
        )

        captured = {}

        async def fake_search(path, *, q, mode, k, agent, engine):
            captured["mode"] = mode
            captured["k"] = k
            return {}

        monkeypatch.setattr(mem_mod, "search_session_memory", fake_search)
        monkeypatch.setattr(mem_mod, "host_engine_or_none", lambda svc: None)

        client = TestClient(_app())
        resp = client.get("/api/sess/memory/search?q=x")
        assert resp.status_code == 200
        assert captured["mode"] == "auto"
        assert captured["k"] == 10
