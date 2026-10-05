"""Unit tests for :mod:`kohakuterrarium.api.routes.sessions_v2.memory`."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from kohakuterrarium.terrarium import LocalTerrariumService, Terrarium
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.laboratory.config import HostConfig
from kohakuterrarium.session.embedding import NullEmbedder
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.sessions import memory_search as search_mod
from kohakuterrarium.terrarium.multi_node_service import MultiNodeTerrariumService
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.api.routes.sessions_v2 import memory as mem_mod
from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.transport_inproc import InProcTransport


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

    @pytest.mark.parametrize("mode", ["fts", "auto"])
    @pytest.mark.parametrize("custom_path", [False, True])
    async def test_live_graph_search_uses_attached_store(
        self, tmp_path, monkeypatch, mode, custom_path
    ):
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "sessions"))
        monkeypatch.setattr(search_mod, "create_embedder", lambda cfg: NullEmbedder())
        config = tmp_path / "creature"
        config.mkdir()
        (config / "config.yaml").write_text(
            "name: probe\ninput: {type: none}\noutput: {type: stdout}\n",
            encoding="utf-8",
        )
        async with Terrarium(session_dir=tmp_path / "sessions") as engine:
            creature = await engine.add_creature(
                str(config),
                llm=ScriptedLLM(["ok"]),
                io="headless",
                session=tmp_path / "custom.kohakutr" if custom_path else None,
            )
            store = creature.agent.session_store
            store.append_event("probe", "user_input", {"content": "marker live"})
            store.flush()
            assert Path(store._path).stem != creature.graph_id
            app = _app()
            service = LocalTerrariumService(engine)
            app.dependency_overrides[get_service] = lambda: service
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                url = f"/api/{creature.graph_id}/memory/search"
                response = await client.get(url, params={"q": "marker", "mode": mode})
                assert response.status_code == 200, response.text
                assert [r["content"] for r in response.json()["results"]] == [
                    "marker live"
                ]
                assert not store._closed

                stale = SessionStore(
                    tmp_path / "sessions" / f"{creature.graph_id}.kohakutr"
                )
                try:
                    stale.init_meta("stale", "agent", "", str(tmp_path), ["probe"])
                    stale.append_event(
                        "probe", "user_input", {"content": "marker stale"}
                    )
                finally:
                    stale.close()
                response = await client.get(url, params={"q": "marker", "mode": mode})
                assert response.status_code == 200, response.text
                assert [r["content"] for r in response.json()["results"]] == [
                    "marker live"
                ]

    async def test_saved_and_missing_session_search(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path))
        path = tmp_path / "saved.kohakutr"
        store = SessionStore(path)
        store.init_meta("saved", "agent", "", str(tmp_path), ["probe"])
        store.append_event("probe", "user_input", {"content": "marker saved"})
        store.close()
        async with Terrarium() as engine:
            engine._session_stores["graph_closed"] = store
            service = LocalTerrariumService(engine)
            app = _app()
            app.dependency_overrides[get_service] = lambda: service
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/saved/memory/search", params={"q": "marker", "mode": "fts"}
                )
                assert response.status_code == 200, response.text
                assert [r["content"] for r in response.json()["results"]] == [
                    "marker saved"
                ]
                before = sorted(tmp_path.glob("*.kohakutr*"))
                for name in ("graph_closed", "unknown"):
                    response = await client.get(
                        f"/api/{name}/memory/search",
                        params={"q": "marker", "mode": "fts"},
                    )
                    assert response.status_code == 404
                assert sorted(tmp_path.glob("*.kohakutr*")) == before

    async def test_cluster_mirror_search_from_either_member(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path))
        mirror = tmp_path / "mirror"
        mirror.mkdir()
        for sid, agent in (("graph_a", "alice"), ("graph_b", "bob")):
            store = SessionStore(mirror / f"{sid}.kohakutr")
            try:
                store.init_meta(sid, "agent", "", str(tmp_path), [agent])
                store.append_event(agent, "user_input", {"content": f"marker {agent}"})
            finally:
                store.close()
        host = HostEngine(HostConfig(), InProcTransport())
        service = MultiNodeTerrariumService(host=host)
        service._cluster_links = {
            frozenset({("w1", "graph_a"), ("w2", "graph_b")}),
            frozenset({("w2", "graph_b"), ("w3", "graph_missing")}),
        }
        app = _app()
        app.dependency_overrides[get_service] = lambda: service
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                for sid in ("graph_a", "graph_b"):
                    response = await client.get(
                        f"/api/{sid}/memory/search",
                        params={"q": "marker", "mode": "fts"},
                    )
                    assert response.status_code == 200, response.text
                    assert {r["content"] for r in response.json()["results"]} == {
                        "marker alice",
                        "marker bob",
                    }
                response = await client.get(
                    "/api/graph_b/memory/search",
                    params={"q": "marker", "mode": "fts", "agent": "bob", "k": 1},
                )
                assert response.status_code == 200, response.text
                assert [r["content"] for r in response.json()["results"]] == [
                    "marker bob"
                ]
        finally:
            await host.stop()
