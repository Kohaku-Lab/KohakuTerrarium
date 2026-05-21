"""Pin: ``multi_user="required"`` actually blocks anonymous engine routes.

The audit caught that ``get_service`` previously fell through to the
shared anonymous engine when no user was present — even in
``required`` mode — because ``Depends(get_optional_user)`` returns
``None`` for anonymous and the route handler never consulted the
strict variant.

These tests pin the fixed semantics: an anonymous request to a route
that calls ``Depends(get_service)`` returns 401 with the
``user``-flavoured auth challenge.  Routes that explicitly want
anonymous fall-through can use ``get_service_legacy`` or carry their
own ``Depends(get_current_user)``.
"""

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.db import (
    connection,
    ensure_migrated,
    _reset_migration_state_for_tests,
)
from kohakuterrarium.api.auth.engine_pool import EnginePool
from kohakuterrarium.api.auth.users import create_user
from kohakuterrarium.api.deps import get_service, set_service

_TEST_ROUNDS = 4


@pytest.fixture
def app(tmp_path, monkeypatch) -> FastAPI:
    """A tiny app with engine_pool + a fake engine-handing route.

    The route just hands back ``service is not None`` — what we're
    testing is whether the dependency runs to completion (200) or
    blocks at the auth gate (401)."""
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    _reset_migration_state_for_tests()
    ensure_migrated()
    # Reset the module-level singleton so each test starts clean.
    set_service(None)

    app = FastAPI()
    # NOTE: capabilities/login routes mount on the auth router; this
    # test app only exposes a dummy engine-handing route to isolate
    # the dependency-chain behaviour.
    app.state.engine_pool = EnginePool(max_active=4, idle_timeout_s=0)
    app.state.auth_config = AuthConfig(
        multi_user="required", bcrypt_rounds=_TEST_ROUNDS
    )
    # Also mount the auth router so login works.
    from kohakuterrarium.api.auth.routes import router as auth_router

    app.include_router(auth_router, prefix="/api/auth")

    router = APIRouter()

    @router.get("/api/dummy-engine-route")
    def dummy(service=Depends(get_service)) -> dict[str, bool]:
        return {"has_service": service is not None}

    app.include_router(router)
    yield app
    set_service(None)
    _reset_migration_state_for_tests()


class TestRequiredMode:
    def test_anonymous_gets_401(self, app):
        with TestClient(app) as client:
            r = client.get("/api/dummy-engine-route")
        assert r.status_code == 401
        # X-Auth-Required header lets the frontend connection state
        # machine raise the login modal (vs. the admin-pswd modal).
        lower = {k.lower(): v for k, v in r.headers.items()}
        assert lower.get("x-auth-required") == "user"

    def test_authenticated_user_passes(self, app):
        # Seed a user, login, then call the gated route with the
        # session cookie.
        with connection() as conn:
            create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "x"},
            )
            assert r.status_code == 200
            r = client.get("/api/dummy-engine-route")
        assert r.status_code == 200
        assert r.json() == {"has_service": True}

    def test_optional_mode_lets_anonymous_through(self, app):
        # Flip to optional → anonymous gets the shared engine.
        app.state.auth_config = AuthConfig(
            multi_user="optional", bcrypt_rounds=_TEST_ROUNDS
        )
        with TestClient(app) as client:
            r = client.get("/api/dummy-engine-route")
        assert r.status_code == 200
        assert r.json() == {"has_service": True}

    def test_off_mode_lets_anonymous_through(self, app):
        app.state.auth_config = AuthConfig(multi_user="off", bcrypt_rounds=_TEST_ROUNDS)
        with TestClient(app) as client:
            r = client.get("/api/dummy-engine-route")
        assert r.status_code == 200


class TestRequiredModePerUserEngine:
    def test_two_users_get_distinct_engines(self, app):
        with connection() as conn:
            create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            create_user(conn, "bob", "x", bcrypt_rounds=_TEST_ROUNDS)
        # Capture the engine each user sees by walking the pool's
        # live_user_ids before / after.
        with TestClient(app) as client_a:
            client_a.post(
                "/api/auth/login",
                json={"username": "alice", "password": "x"},
            )
            client_a.get("/api/dummy-engine-route")
        with TestClient(app) as client_b:
            client_b.post(
                "/api/auth/login",
                json={"username": "bob", "password": "x"},
            )
            client_b.get("/api/dummy-engine-route")
        live = set(app.state.engine_pool.live_user_ids())
        # Both users should have pooled engines; no anonymous slot.
        assert None not in live
        assert len(live) == 2
