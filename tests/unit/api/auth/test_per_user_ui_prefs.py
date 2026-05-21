"""Pin: UI prefs are per-user when L4 enabled, shared when L4 off.

Audit-caught: the API route originally called global ``load_prefs()`` /
``save_prefs()`` so two users' theme + layout state would collide.
The fix threads ``user_id`` through ``studio.identity.ui_prefs`` and
the route resolves it from ``Depends(get_optional_user)``.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth import router as auth_router
from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.db import (
    connection,
    ensure_migrated,
    _reset_migration_state_for_tests,
)
from kohakuterrarium.api.auth.users import create_user
from kohakuterrarium.api.routes.identity.ui_prefs import router as ui_prefs_router
from kohakuterrarium.studio.identity.ui_prefs import (
    load_prefs,
    save_prefs,
    ui_prefs_path,
)

_TEST_ROUNDS = 4


@pytest.fixture
def app(tmp_path, monkeypatch) -> FastAPI:
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    _reset_migration_state_for_tests()
    ensure_migrated()
    app = FastAPI()
    app.state.auth_config = AuthConfig(
        multi_user="required",
        registration="open",
        bcrypt_rounds=_TEST_ROUNDS,
    )
    app.include_router(auth_router, prefix="/api/auth")
    app.include_router(ui_prefs_router, prefix="/api/settings")
    yield app
    _reset_migration_state_for_tests()


class TestStoreLayerPerUser:
    """``ui_prefs_path(user_id)`` resolves under ``users/<id>/``."""

    def test_user_id_lands_in_user_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        assert ui_prefs_path(42) == tmp_path / "users" / "42" / "ui_prefs.json"

    def test_none_user_uses_shared_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        assert ui_prefs_path(None) == tmp_path / "ui_prefs.json"

    def test_save_then_load_round_trip_per_user(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        save_prefs({"theme": "dark"}, user_id=1)
        save_prefs({"theme": "light"}, user_id=2)
        # Two users, two distinct files, two distinct values.
        assert load_prefs(user_id=1)["theme"] == "dark"
        assert load_prefs(user_id=2)["theme"] == "light"
        # Shared slot untouched.
        assert load_prefs(user_id=None)["theme"] == "system"  # default


class TestRouteScopesByUser:
    def test_two_users_dont_collide(self, app):
        with connection() as conn:
            create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            create_user(conn, "bob", "x", bcrypt_rounds=_TEST_ROUNDS)

        # Alice sets dark theme.
        with TestClient(app) as client_a:
            client_a.post(
                "/api/auth/login", json={"username": "alice", "password": "x"}
            )
            r = client_a.post(
                "/api/settings/ui-prefs",
                json={"values": {"theme": "dark"}},
            )
            assert r.status_code == 200
            assert r.json()["values"]["theme"] == "dark"

        # Bob's GET sees defaults, not alice's setting.
        with TestClient(app) as client_b:
            client_b.post("/api/auth/login", json={"username": "bob", "password": "x"})
            r = client_b.get("/api/settings/ui-prefs")
            assert r.status_code == 200
            assert r.json()["values"]["theme"] == "system"  # default, NOT "dark"

        # Alice re-reads → still dark.
        with TestClient(app) as client_a:
            client_a.post(
                "/api/auth/login", json={"username": "alice", "password": "x"}
            )
            r = client_a.get("/api/settings/ui-prefs")
            assert r.json()["values"]["theme"] == "dark"

    def test_anonymous_l4_required_blocked(self, app):
        # With multi_user="required" + no L4 fall-through for
        # anonymous via ``get_optional_user`` (returns None), the
        # ui-prefs route still works for anonymous (None user_id)
        # because the dep is OPTIONAL — anonymous gets the shared
        # slot.  L4-required-enforcement happens at ``get_service``,
        # not at every L4-aware route.  This pins the design:
        # ui-prefs is L4-aware but anonymous-tolerant.
        with TestClient(app) as client:
            r = client.get("/api/settings/ui-prefs")
        # Returns 200 with the shared-slot defaults.
        assert r.status_code == 200
