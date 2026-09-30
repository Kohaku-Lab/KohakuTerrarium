"""Pin: the chat WebSocket serves each user from their own engine.

Under multi-user isolation a user's sessions live in that user's pooled engine,
so a chat socket that resolved the process-wide service could never find them,
and an anonymous socket in ``required`` mode must not open at all.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.db import (
    connection,
    ensure_migrated,
    _reset_migration_state_for_tests,
)
from kohakuterrarium.api.auth.engine_pool import EnginePool
from kohakuterrarium.api.auth.routes import router as auth_router
from kohakuterrarium.api.deps import set_service
from kohakuterrarium.api.auth.users import create_user
from kohakuterrarium.api.ws import io as io_mod

_ROUNDS = 4
CHAT_URL = "/ws/sessions/graph_1/creatures/alice/chat"


@pytest.fixture
def app(tmp_path, monkeypatch) -> FastAPI:
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    _reset_migration_state_for_tests()
    ensure_migrated()
    set_service(None)

    app = FastAPI()
    app.state.engine_pool = EnginePool(max_active=4)
    app.state.auth_config = AuthConfig(multi_user="required", bcrypt_rounds=_ROUNDS)
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(io_mod.router)
    yield app
    set_service(None)
    _reset_migration_state_for_tests()


@pytest.fixture
def attached(monkeypatch):
    """Record the service the chat socket hands to ``attach_io``."""
    seen: list = []

    async def fake_attach(websocket, service, session_id, creature_id):
        seen.append(service)
        await websocket.send_json({"type": "attached"})
        await websocket.close()

    monkeypatch.setattr(io_mod, "attach_io", fake_attach)
    return seen


def _login(client, name):
    r = client.post("/api/auth/login", json={"username": name, "password": "x"})
    assert r.status_code == 200


class TestChatSocketUnderMultiUser:
    def test_anonymous_socket_is_rejected_in_required_mode(self, app, attached):
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(CHAT_URL):
                    pass
        assert attached == []

    def test_each_user_is_served_from_their_own_engine(self, app, attached):
        with connection() as conn:
            create_user(conn, "alice", "x", bcrypt_rounds=_ROUNDS)
            create_user(conn, "bob", "x", bcrypt_rounds=_ROUNDS)
        engines = {}
        for name in ("alice", "bob"):
            with TestClient(app) as client:
                _login(client, name)
                with client.websocket_connect(CHAT_URL) as ws:
                    assert ws.receive_json() == {"type": "attached"}
            engines[name] = attached[-1]._engine

        assert engines["alice"] is not engines["bob"]
        pool = app.state.engine_pool
        live = set(pool.live_user_ids())
        assert len(live) == 2 and None not in live

    def test_without_user_isolation_the_shared_service_is_used(self, app, attached):
        app.state.auth_config = AuthConfig(multi_user="off")
        with TestClient(app) as client:
            with client.websocket_connect(CHAT_URL) as ws:
                assert ws.receive_json() == {"type": "attached"}
        assert len(attached) == 1
        assert app.state.engine_pool.live_user_ids() == []
