"""Unit tests for the open-conversation aggregation endpoint."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service, resolve_request_session_dir
from kohakuterrarium.api.routes.persistence import open_sessions
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.session_index import close_session_index
from kohakuterrarium.studio.sessions import lifecycle
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


def _saved_store(
    path,
    *,
    session_id: str,
    status: str = "paused",
    conversation_open: bool | None,
    config_type: str = "agent",
    terrarium_name: str | None = None,
    conversation_id: str | None = None,
):
    store = SessionStore(path)
    store.init_meta(
        session_id,
        config_type,
        "/cfg",
        str(path.parent),
        ["root", "worker"] if config_type == "terrarium" else ["alice"],
        terrarium_name=terrarium_name,
    )
    store.update_status(status)
    if conversation_id is not None:
        store.meta["conversation_id"] = conversation_id
    if conversation_open is None:
        store.meta.delete("conversation_open")
    else:
        store.set_conversation_open(conversation_open)
    store.close(update_status=False)


class TestOpenSessions:
    def test_saved_rows_require_explicit_open_marker_and_nonterminal_status(
        self, tmp_path
    ):
        service = LocalTerrariumService(Terrarium())
        _saved_store(
            tmp_path / "legacy.kohakutr",
            session_id="legacy",
            conversation_open=None,
        )
        _saved_store(
            tmp_path / "open-team.kohakutr",
            session_id="open-team",
            conversation_open=True,
            config_type="terrarium",
            terrarium_name="Pvpn",
        )
        _saved_store(
            tmp_path / "ended.kohakutr",
            session_id="ended",
            status="completed",
            conversation_open=True,
        )

        try:
            rows = open_sessions.build_open_session_rows(service, tmp_path)
            assert len(rows) == 1
            assert rows[0]["id"] == rows[0]["conversation_id"]
            assert rows[0]["id"] != "open-team"
            assert rows[0]["saved_name"] == "open-team"
            assert rows[0]["config_name"] == "Pvpn"
            assert rows[0]["type"] == "terrarium"
            assert rows[0]["is_live"] is False
        finally:
            close_session_index()

    def test_path_key_delegates_case_rules_to_the_platform(self, tmp_path, monkeypatch):
        monkeypatch.setattr(open_sessions.os.path, "normcase", lambda value: value)
        upper = open_sessions._path_key(tmp_path / "CaseSensitive")
        lower = open_sessions._path_key(tmp_path / "casesensitive")
        assert upper != lower

    def test_live_row_disappearing_during_snapshot_is_skipped(self, monkeypatch):
        listing = SimpleNamespace(session_id="stopping")
        monkeypatch.setattr(
            open_sessions.lifecycle, "list_sessions", lambda service: [listing]
        )

        def _stopped(service, session_id):
            raise KeyError(session_id)

        monkeypatch.setattr(open_sessions.lifecycle, "get_session", _stopped)
        assert open_sessions._live_rows(SimpleNamespace()) == ([], set(), set())

    async def test_live_store_wins_over_its_saved_index_row(self, tmp_path):
        engine = await (
            TestTerrariumBuilder()
            .with_creature("alice")
            .with_creature("bob")
            .with_separate_graphs()
            .build()
        )
        service = LocalTerrariumService(engine)
        creature = engine.get_creature("alice")
        session_id = creature.graph_id
        path = tmp_path / "active.kohakutr"
        store = SessionStore(path)
        store.init_meta(session_id, "agent", "/cfg", str(tmp_path), ["alice"])
        engine._session_stores[session_id] = store
        lifecycle.stores_for(service)[session_id] = store
        lifecycle.meta_for(service)[session_id] = {
            "name": "Active chat",
            "pwd": str(tmp_path),
        }
        bob_session_id = engine.get_creature("bob").graph_id
        lifecycle.meta_for(service)[bob_session_id] = {
            "name": "Unsaved chat",
            "pwd": str(tmp_path),
        }

        try:
            rows = open_sessions.build_open_session_rows(service, tmp_path)
            assert len(rows) == 2
            by_runtime = {row["runtime_id"]: row for row in rows}
            assert by_runtime[session_id]["id"] == store.meta["conversation_id"]
            assert (
                by_runtime[session_id]["conversation_id"]
                == store.meta["conversation_id"]
            )
            assert by_runtime[session_id]["saved_name"] == "active"
            assert by_runtime[session_id]["config_name"] == "Active chat"
            assert by_runtime[session_id]["is_live"] is True
            assert by_runtime[bob_session_id]["id"] == bob_session_id
            assert by_runtime[bob_session_id]["saved_name"] is None
            assert by_runtime[bob_session_id]["config_name"] == "Unsaved chat"
        finally:
            close_session_index()
            engine._session_stores.pop(session_id, None)
            lifecycle.stores_for(service).pop(session_id, None)
            store.close(update_status=False)
            await engine.shutdown()

    def test_route_returns_direct_array_from_request_scoped_directory(self, tmp_path):
        service = LocalTerrariumService(Terrarium())
        _saved_store(
            tmp_path / "open.kohakutr",
            session_id="open",
            conversation_open=True,
        )
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: service
        app.dependency_overrides[resolve_request_session_dir] = lambda: tmp_path
        app.include_router(open_sessions.router, prefix="/sessions")

        try:
            response = TestClient(app).get("/sessions/open")
            assert response.status_code == 200
            assert isinstance(response.json(), list)
            assert response.json()[0]["saved_name"] == "open"
        finally:
            close_session_index()

    def test_end_route_closes_dormant_marker_without_resuming(self, tmp_path):
        path = tmp_path / "dormant.kohakutr"
        _saved_store(
            path,
            session_id="dormant",
            conversation_open=True,
            conversation_id="conversation-dormant",
        )
        service = LocalTerrariumService(Terrarium())
        app = FastAPI()
        app.dependency_overrides[get_service] = lambda: service
        app.dependency_overrides[resolve_request_session_dir] = lambda: tmp_path
        app.include_router(open_sessions.router, prefix="/sessions")

        try:
            client = TestClient(app)
            response = client.post("/sessions/open/conversation-dormant/end")
            assert response.status_code == 200
            assert response.json() == {
                "status": "ended",
                "conversation_id": "conversation-dormant",
            }
            store = SessionStore.open_readonly(path)
            try:
                assert bool(store.meta["conversation_open"]) is False
                assert store.meta["status"] == "completed"
            finally:
                store.close(update_status=False)
            assert client.get("/sessions/open").json() == []
        finally:
            close_session_index()

    async def test_end_route_accepts_unsaved_live_row_id(self, tmp_path, monkeypatch):
        service = LocalTerrariumService(Terrarium())
        row = {
            "id": "runtime-only",
            "conversation_id": None,
            "runtime_id": "runtime-only",
            "saved_name": None,
        }
        monkeypatch.setattr(
            open_sessions,
            "build_open_session_rows",
            lambda _service, _session_dir: [row],
        )
        ended: list[str] = []

        async def _end_session(_service, runtime_id):
            ended.append(runtime_id)

        monkeypatch.setattr(open_sessions.lifecycle, "end_session", _end_session)

        response = await open_sessions.end_open_conversation(
            "runtime-only",
            service=service,
            session_dir=tmp_path,
        )

        assert response == {
            "status": "ended",
            "conversation_id": "runtime-only",
        }
        assert ended == ["runtime-only"]

    async def test_end_rejects_conflicting_inflight_resume(self, tmp_path, monkeypatch):
        path = tmp_path / "dormant.kohakutr"
        _saved_store(
            path,
            session_id="dormant",
            conversation_open=True,
            conversation_id="conversation-dormant",
        )
        service = LocalTerrariumService(Terrarium())
        row = {
            "conversation_id": "conversation-dormant",
            "runtime_id": None,
            "saved_name": "dormant",
        }
        monkeypatch.setattr(
            open_sessions,
            "build_open_session_rows",
            lambda _service, _session_dir: [row],
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def _resume():
            started.set()
            await release.wait()
            return "resumed"

        task = asyncio.create_task(
            open_sessions.resume_coordinator.run(
                open_sessions._path_key(path),
                _resume,
                intent="resume:_host",
            )
        )
        await started.wait()
        try:
            with pytest.raises(HTTPException) as exc:
                await open_sessions.end_open_conversation(
                    "conversation-dormant",
                    service=service,
                    session_dir=tmp_path,
                )
            assert exc.value.status_code == 409
        finally:
            release.set()
            assert await task == "resumed"
            close_session_index()
