"""The saved-session list and ``/api/projects`` honour L4 user isolation.

``GET /api/sessions`` must read the request's session directory (the one
``GET /api/sessions/open`` already uses): each user lists only the sessions
under ``users/<id>/sessions``, never another user's. Saved projects live in
``users/<id>/projects.json``.
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
from kohakuterrarium.api.routes.persistence import projects as projects_mod
from kohakuterrarium.api.routes.persistence import saved as saved_mod
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.studio.persistence.session_index import close_session_index

pytestmark = pytest.mark.timeout(30)

_TEST_ROUNDS = 4


def _make_session(session_dir: Path, name: str, pwd: str) -> None:
    session_dir.mkdir(parents=True, exist_ok=True)
    store = SessionStore(str(session_dir / f"{name}.kohakutr"))
    try:
        store.init_meta("sid-" + name, "agent", "/cfg.yaml", pwd, ["alice"])
        store.flush()
    finally:
        store.close()


@pytest.fixture
def l4_app(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("KT_SESSION_DIR", raising=False)
    _reset_migration_state_for_tests()
    ensure_migrated()
    set_service(None)
    app = FastAPI()
    app.state.engine_pool = EnginePool(max_active=4)
    app.state.auth_config = AuthConfig(
        multi_user="required", bcrypt_rounds=_TEST_ROUNDS
    )
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(saved_mod.router, prefix="/api/sessions")
    app.include_router(projects_mod.router, prefix="/api/projects")
    yield app
    set_service(None)
    close_session_index()
    _reset_migration_state_for_tests()


def _login(client: TestClient, username: str) -> None:
    r = client.post("/api/auth/login", json={"username": username, "password": "x"})
    assert r.status_code == 200, r.text


def test_each_user_lists_only_their_own_sessions(l4_app, tmp_path):
    with connection() as conn:
        alice = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
        bob = create_user(conn, "bob", "x", bcrypt_rounds=_TEST_ROUNDS)
    _make_session(_user_session_dir(alice.id), "alice-one", str(tmp_path / "a"))
    _make_session(_user_session_dir(alice.id), "alice-two", str(tmp_path / "a"))
    _make_session(_user_session_dir(bob.id), "bob-one", str(tmp_path / "b"))
    # A session in the shared directory belongs to nobody logged in.
    _make_session(tmp_path / "sessions", "shared-one", str(tmp_path / "s"))

    with TestClient(l4_app) as client_a:
        _login(client_a, "alice")
        body = client_a.get("/api/sessions?limit=50").json()
    assert sorted(row["name"] for row in body["sessions"]) == [
        "alice-one",
        "alice-two",
    ]
    assert body["total"] == 2

    with TestClient(l4_app) as client_b:
        _login(client_b, "bob")
        body = client_b.get("/api/sessions?limit=50").json()
    assert [row["name"] for row in body["sessions"]] == ["bob-one"]


def test_required_mode_rejects_anonymous_listing(l4_app):
    with TestClient(l4_app) as client:
        r = client.get("/api/sessions")
    assert r.status_code == 401


def test_projects_are_per_user(l4_app, tmp_path):
    with connection() as conn:
        alice = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
        create_user(conn, "bob", "x", bcrypt_rounds=_TEST_ROUNDS)
    folder = tmp_path / "alice-repo"
    folder.mkdir()
    _make_session(_user_session_dir(alice.id), "alice-one", str(folder))

    with TestClient(l4_app) as client_a:
        _login(client_a, "alice")
        found = client_a.get("/api/projects").json()["projects"]
        assert [(p["path"], p["session_count"]) for p in found] == [(str(folder), 1)]
        renamed = client_a.patch(
            f"/api/projects/{found[0]['id']}", json={"name": "Mine"}
        ).json()
        assert renamed["name"] == "Mine"
        rows = client_a.get(f"/api/sessions?project={found[0]['id']}").json()
        assert [r["project"]["name"] for r in rows["sessions"]] == ["Mine"]
    assert (tmp_path / "users" / str(alice.id) / "projects.json").is_file()
    assert not (tmp_path / "projects.json").exists()

    with TestClient(l4_app) as client_b:
        _login(client_b, "bob")
        body = client_b.get("/api/projects?include_hidden=true").json()
        assert body["projects"] == []
        assert client_b.delete(f"/api/projects/{found[0]['id']}").status_code == 404


def test_required_mode_rejects_anonymous_project_calls(l4_app):
    with TestClient(l4_app) as client:
        assert client.get("/api/projects").status_code == 401
        assert client.delete("/api/projects/p_x").status_code == 401
        assert client.post("/api/projects", json={"path": "/x"}).status_code == 401
