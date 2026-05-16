"""Unit tests for the file-watch + PTY WebSocket endpoints."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_service
from kohakuterrarium.api.ws import files as files_mod
from kohakuterrarium.api.ws import pty as pty_mod
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder
from kohakuterrarium.terrarium.service import LocalTerrariumService


def _build_app(router, service):
    app = FastAPI()
    app.dependency_overrides[get_service] = lambda: service
    app.include_router(router)
    return app


# ── ws/files ──────────────────────────────────────────────────


class TestWsFiles:
    async def test_unknown_agent_sends_error(self):
        from fastapi import WebSocketDisconnect

        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)
        app = _build_app(files_mod.router, svc)
        try:
            with TestClient(app) as client:
                with client.websocket_connect("/ws/files/ghost") as ws:
                    msg = ws.receive_json()
                    assert msg == {
                        "type": "error",
                        "text": "Agent not found: ghost",
                    }
                    # The endpoint closes right after the error frame.
                    with pytest.raises(WebSocketDisconnect):
                        ws.receive_json()
        finally:
            await t.shutdown()

    async def test_creature_without_working_dir(self):
        from fastapi import WebSocketDisconnect

        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        app = _build_app(files_mod.router, svc)
        try:
            with TestClient(app) as client:
                with client.websocket_connect("/ws/files/alice") as ws:
                    msg = ws.receive_json()
                    # Creature exists but has no working dir → that
                    # exact error, then close.
                    assert msg == {
                        "type": "error",
                        "text": "Agent has no working directory",
                    }
                    with pytest.raises(WebSocketDisconnect):
                        ws.receive_json()
        finally:
            await t.shutdown()


# ── ws/pty ────────────────────────────────────────────────────


class TestWsPty:
    async def test_unknown_creature_sends_error(self):
        from fastapi import WebSocketDisconnect

        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)
        app = _build_app(pty_mod.router, svc)
        try:
            with TestClient(app) as client:
                with client.websocket_connect(
                    "/ws/sessions/sid-x/creatures/ghost/pty"
                ) as ws:
                    msg = ws.receive_json()
                    # Unknown creature, not remote-hosted → exact
                    # not-found error frame, then close.
                    assert msg == {
                        "type": "error",
                        "data": "creature 'ghost' not found",
                    }
                    with pytest.raises(WebSocketDisconnect):
                        ws.receive_json()
        finally:
            await t.shutdown()

    async def test_resolve_creature_home_no_resolver(self):
        svc = object()  # no _resolve_home attr
        out = await pty_mod._resolve_creature_home(svc, "cid")
        assert out == "_host"

    async def test_resolve_creature_home_calls_resolver(self):
        class _Svc:
            async def _resolve_home(self, cid):
                return "worker-1"

        out = await pty_mod._resolve_creature_home(_Svc(), "cid")
        assert out == "worker-1"

    async def test_resolve_creature_home_swallows_error(self):
        class _Svc:
            async def _resolve_home(self, cid):
                raise RuntimeError("bad")

        out = await pty_mod._resolve_creature_home(_Svc(), "cid")
        assert out is None
