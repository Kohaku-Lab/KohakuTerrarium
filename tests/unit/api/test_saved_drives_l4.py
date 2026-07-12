"""R1-01: the saved-session Drive viewer must honour L4 user/session isolation.

The route reads a persisted Drive sidecar without a live engine. Before the
fix it resolved from the process-global session directory and had no auth
dependency, so an anonymous ``required``-mode request reached it and an
authenticated user could probe another user's saved-session name.

These pin the fixed contract:

1. ``multi_user="required"`` + anonymous → 401.
2. User A cannot read User B's saved sidecar → 404.
3. User A can read its own saved Drive rows.
4. L4 disabled retains the local single-user behaviour (existing tests).
"""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.db import (
    _reset_migration_state_for_tests,
    connection,
    ensure_migrated,
)
from kohakuterrarium.api.auth.engine_pool import EnginePool, _user_session_dir
from kohakuterrarium.api.auth.routes import router as auth_router
from kohakuterrarium.api.auth.users import create_user
from kohakuterrarium.api.deps import set_service
from kohakuterrarium.api.routes.persistence import saved_drives as mod
from kohakuterrarium.terrarium.creature_host import Creature
from kohakuterrarium.terrarium.drive.config import (
    DriveRuntimeConfig,
    default_registrations,
)
from kohakuterrarium.terrarium.drive.models import ActorRef
from kohakuterrarium.terrarium.engine import Terrarium
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import _FakeAgent

pytestmark = pytest.mark.timeout(30)

_TEST_ROUNDS = 4
OPS = ActorRef("user", "ops")


async def _make_saved_session(session_dir: Path) -> str:
    """Create + persist one Drive under ``session_dir``; return the file stem."""
    session_dir.mkdir(parents=True, exist_ok=True)
    engine = Terrarium(
        session_dir=str(session_dir),
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
    await engine.add_creature(root, session=True)
    gid = root.graph_id
    svc = LocalTerrariumService(engine)
    from kohakuterrarium.studio.sessions import drives as facade

    await facade.create_record(
        svc,
        graph_id=gid,
        actor=OPS,
        body={"kind": "generic", "title": "alice secret", "spec": {"secret": 1}},
        is_privileged=True,
        operator=True,
    )
    await engine.shutdown()
    return next(session_dir.glob("*.kohakutr")).stem


@pytest.fixture
def l4_app(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    # A non-default session dir must NOT be consulted: the route must resolve
    # inside the per-user namespace, not this global one.
    monkeypatch.delenv("KT_SESSION_DIR", raising=False)
    _reset_migration_state_for_tests()
    ensure_migrated()
    set_service(None)

    app = FastAPI()
    app.state.engine_pool = EnginePool(max_active=4, idle_timeout_s=0)
    app.state.auth_config = AuthConfig(
        multi_user="required", bcrypt_rounds=_TEST_ROUNDS
    )
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(mod.router, prefix="/api/persistence/viewer")
    yield app
    set_service(None)
    _reset_migration_state_for_tests()


def _login(client: TestClient, username: str) -> None:
    r = client.post("/api/auth/login", json={"username": username, "password": "x"})
    assert r.status_code == 200, r.text


async def test_required_anonymous_denied(l4_app):
    with TestClient(l4_app) as client:
        r = client.get("/api/persistence/viewer/whatever/drives")
    assert r.status_code == 401
    lower = {k.lower(): v for k, v in r.headers.items()}
    assert lower.get("x-auth-required") == "user"


async def test_user_reads_own_but_not_others(l4_app, tmp_path):
    with connection() as conn:
        alice = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
        create_user(conn, "bob", "x", bcrypt_rounds=_TEST_ROUNDS)
    stem = await _make_saved_session(_user_session_dir(alice.id))

    # Alice reads her own saved Drive rows.
    with TestClient(l4_app) as client_a:
        _login(client_a, "alice")
        r = client_a.get(f"/api/persistence/viewer/{stem}/drives")
        assert r.status_code == 200, r.text
        drives = r.json()["drives"]
        assert len(drives) == 1 and drives[0]["title"] == "alice secret"

    # Bob cannot even confirm the session exists in his namespace.
    with TestClient(l4_app) as client_b:
        _login(client_b, "bob")
        r = client_b.get(f"/api/persistence/viewer/{stem}/drives")
    assert r.status_code == 404
