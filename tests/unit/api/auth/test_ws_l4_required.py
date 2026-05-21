"""Pin: WebSocket routes under ``multi_user="required"`` reject anonymous.

The re-audit flagged this as the one auth-layer surface without
explicit coverage.  HTTP routes pinned by ``test_required_mode_gating.py``;
this file closes the WS branch — anonymous WS handshake to a route
that takes ``Depends(get_service)`` must drop the connection rather
than open it with the shared engine.
"""

import pytest
from fastapi import APIRouter, Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.db import (
    ensure_migrated,
    _reset_migration_state_for_tests,
)
from kohakuterrarium.api.auth.engine_pool import EnginePool
from kohakuterrarium.api.deps import get_service, set_service


@pytest.fixture
def app(tmp_path, monkeypatch) -> FastAPI:
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    _reset_migration_state_for_tests()
    ensure_migrated()
    set_service(None)

    app = FastAPI()
    app.state.engine_pool = EnginePool(max_active=4, idle_timeout_s=0)
    app.state.auth_config = AuthConfig(multi_user="required", bcrypt_rounds=4)

    router = APIRouter()

    @router.websocket("/ws/dummy")
    async def dummy_ws(websocket: WebSocket, service=Depends(get_service)) -> None:
        # The dep chain raises 401 before this body runs in the
        # anonymous-required case; if it ever DOES run, fail loudly
        # so the test catches the regression.
        await websocket.accept()
        await websocket.send_json({"service": service is not None})
        await websocket.close()

    app.include_router(router)
    yield app
    set_service(None)
    _reset_migration_state_for_tests()


class TestWsL4Required:
    def test_anonymous_ws_handshake_rejected(self, app):
        # FastAPI translates the 401 raised in the dep chain into a
        # WebSocket close — the client sees WebSocketDisconnect with
        # the appropriate code (1008 / 4401 depending on stack).
        # We assert that the connection does NOT open into the
        # accepted state.
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/ws/dummy"):
                    pass

    def test_off_mode_ws_handshake_succeeds(self, app):
        # Sanity: with L4 off, anonymous WS works as today.
        app.state.auth_config = AuthConfig(multi_user="off")
        with TestClient(app) as client:
            with client.websocket_connect("/ws/dummy") as ws:
                msg = ws.receive_json()
                assert msg["service"] is True
