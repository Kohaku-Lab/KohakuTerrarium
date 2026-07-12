"""R1-03: anonymous optional-L4 callers must NOT be elevated to operators.

``_actor_privilege(None)`` used to always mean "local operator". But ``None``
has two meanings: L4 disabled (trusted local console) vs. L4 optional +
anonymous. Only the former may receive operator/privilege elevation; an
anonymous optional-L4 caller is an unprivileged principal.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.db import (
    _reset_migration_state_for_tests,
    connection,
    ensure_migrated,
)
from kohakuterrarium.api.auth.dependencies import get_optional_user
from kohakuterrarium.api.auth.routes import router as auth_router
from kohakuterrarium.api.auth.users import create_user
from kohakuterrarium.api.app import drive_error_handler
from kohakuterrarium.api.deps import get_service, set_service
from kohakuterrarium.api.routes.sessions_v2 import drives as drives_mod
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.errors import DriveError
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)
_TEST_ROUNDS = 4


async def _service_with_graph():
    engine = Terrarium(
        drive_config=DriveRuntimeConfig(enabled=True),
        drive_registrations=default_registrations(),
    )
    await engine.__aenter__()
    root = Creature(
        creature_id="root",
        name="root",
        agent=_FakeAgent(name="root"),
        is_privileged=True,
    )
    await engine.add_creature(root)
    return LocalTerrariumService(engine), engine, root.graph_id


def _app(service, *, multi_user):
    app = FastAPI()
    app.add_exception_handler(DriveError, drive_error_handler)
    app.state.auth_config = AuthConfig(
        multi_user=multi_user, bcrypt_rounds=_TEST_ROUNDS
    )
    app.dependency_overrides[get_service] = lambda: service
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(drives_mod.router, prefix="/api/sessions")
    return app


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    _reset_migration_state_for_tests()
    ensure_migrated()
    set_service(None)
    yield
    set_service(None)
    _reset_migration_state_for_tests()


def _create_body():
    return {"kind": "generic", "title": "graph-scoped drive"}


async def test_l4_disabled_anonymous_is_operator(env):
    service, engine, gid = await _service_with_graph()
    try:
        app = _app(service, multi_user="off")
        # L4 disabled → get_optional_user always None → local operator console.
        app.dependency_overrides[get_optional_user] = lambda: None
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(f"/api/sessions/{gid}/drives", json=_create_body())
        assert r.status_code == 200, r.text
    finally:
        await engine.shutdown()


async def test_l4_optional_anonymous_is_not_operator(env):
    service, engine, gid = await _service_with_graph()
    try:
        app = _app(service, multi_user="optional")
        with TestClient(app, raise_server_exceptions=False) as client:
            # Anonymous under optional L4 must be denied the operator-only
            # graph-scoped create.
            r = client.post(f"/api/sessions/{gid}/drives", json=_create_body())
        assert r.status_code == 403, r.text
    finally:
        await engine.shutdown()


async def test_l4_optional_admin_and_user(env):
    service, engine, gid = await _service_with_graph()
    try:
        with connection() as conn:
            create_user(conn, "admin", "x", role="admin", bcrypt_rounds=_TEST_ROUNDS)
            create_user(conn, "bob", "x", bcrypt_rounds=_TEST_ROUNDS)
        app = _app(service, multi_user="optional")

        with TestClient(app, raise_server_exceptions=False) as client:
            client.post("/api/auth/login", json={"username": "admin", "password": "x"})
            r = client.post(f"/api/sessions/{gid}/drives", json=_create_body())
        assert r.status_code == 200, r.text

        with TestClient(app, raise_server_exceptions=False) as client:
            client.post("/api/auth/login", json={"username": "bob", "password": "x"})
            r = client.post(f"/api/sessions/{gid}/drives", json=_create_body())
        assert r.status_code == 403, r.text
    finally:
        await engine.shutdown()
