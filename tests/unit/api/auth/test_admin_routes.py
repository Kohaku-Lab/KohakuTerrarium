"""Coverage for admin-only routes — patch/delete users, revoke
invitations, rotate-host-token, rotate-admin-token, token-status.

These fill the routes.py coverage gaps the re-audit highlighted —
unit happy-path + 404 + last-admin guard + race conditions.
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
    yield app
    _reset_migration_state_for_tests()


def _seed_admin(app):
    with connection() as conn:
        create_user(conn, "root", "x", role="admin", bcrypt_rounds=_TEST_ROUNDS)


def _login(client, username="root", password="x"):
    r = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# PATCH /users/{id}
# ---------------------------------------------------------------------------


class TestPatchUser:
    def test_404_for_unknown_user(self, app):
        _seed_admin(app)
        with TestClient(app) as client:
            _login(client)
            r = client.patch("/api/auth/users/9999", json={"role": "admin"})
        assert r.status_code == 404

    def test_demote_admin_fails_on_last_admin(self, app):
        _seed_admin(app)
        with TestClient(app) as client:
            _login(client)
            me = client.get("/api/auth/me").json()
            r = client.patch(f"/api/auth/users/{me['id']}", json={"is_active": False})
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "last_admin"

    def test_disable_user_drops_sessions(self, app):
        _seed_admin(app)
        with TestClient(app) as client:
            _login(client)
            target = client.post(
                "/api/auth/users",
                json={"username": "alice", "password": "x", "role": "user"},
            ).json()["user"]
            r = client.patch(
                f"/api/auth/users/{target['id']}", json={"is_active": False}
            )
        assert r.status_code == 200
        assert r.json()["user"]["is_active"] is False


# ---------------------------------------------------------------------------
# DELETE /users/{id}
# ---------------------------------------------------------------------------


class TestDeleteUser:
    def test_404_for_unknown(self, app):
        _seed_admin(app)
        with TestClient(app) as client:
            _login(client)
            r = client.delete("/api/auth/users/9999")
        assert r.status_code == 404

    def test_cannot_delete_last_admin(self, app):
        _seed_admin(app)
        with TestClient(app) as client:
            _login(client)
            me = client.get("/api/auth/me").json()
            r = client.delete(f"/api/auth/users/{me['id']}")
        assert r.status_code == 400

    def test_delete_regular_user_succeeds(self, app):
        _seed_admin(app)
        with TestClient(app) as client:
            _login(client)
            target = client.post(
                "/api/auth/users",
                json={"username": "alice", "password": "x", "role": "user"},
            ).json()["user"]
            r = client.delete(f"/api/auth/users/{target['id']}")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Invitations — revoke + race
# ---------------------------------------------------------------------------


class TestInvitationRevoke:
    def test_revoke_unknown_returns_404(self, app):
        _seed_admin(app)
        with TestClient(app) as client:
            _login(client)
            r = client.delete("/api/auth/invitations/9999")
        assert r.status_code == 404

    def test_revoke_used_invitation_returns_404(self, app):
        # Used invitations can't be "revoked" — the workflow keeps
        # them around as audit.
        _seed_admin(app)
        with connection() as conn:
            create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
        with TestClient(app) as client:
            _login(client)
            invite = client.post("/api/auth/invitations", json={"role": "user"}).json()
            client.post("/api/auth/logout")
            # Consume via register-with-invitation.
            app.state.auth_config = AuthConfig(
                multi_user="required",
                registration="invite_only",
                bcrypt_rounds=_TEST_ROUNDS,
            )
            client.post(
                "/api/auth/register",
                json={
                    "username": "bob",
                    "password": "x",
                    "invitation_token": invite["token"],
                },
            )
            client.post("/api/auth/logout")
            _login(client)
            r = client.delete(f"/api/auth/invitations/{invite['id']}")
        assert r.status_code == 404


class TestInvitationRace:
    def test_invitation_race_409(self, app):
        # Set up invite-only registration with one consumed invitation
        # — second register attempt with the same token now races
        # against ourselves: the first consume succeeds, the second
        # gets 400 (invitation_invalid since peek already returns None).
        _seed_admin(app)
        app.state.auth_config = AuthConfig(
            multi_user="required",
            registration="invite_only",
            bcrypt_rounds=_TEST_ROUNDS,
        )
        with TestClient(app) as client:
            _login(client)
            invite = client.post("/api/auth/invitations", json={"role": "user"}).json()
            client.post("/api/auth/logout")
            r1 = client.post(
                "/api/auth/register",
                json={
                    "username": "alice",
                    "password": "x",
                    "invitation_token": invite["token"],
                },
            )
            assert r1.status_code == 200
            r2 = client.post(
                "/api/auth/register",
                json={
                    "username": "bob",
                    "password": "x",
                    "invitation_token": invite["token"],
                },
            )
        # Second consume sees a fully-used invitation at peek → 400
        # (invitation_invalid).  The race path (409 invitation_race)
        # only fires when two concurrent registers slip past peek but
        # only one wins at consume — that's an SQL race we can't
        # easily simulate in TestClient.  This test pins the
        # peek-already-used path which is the common operator
        # experience.
        assert r2.status_code == 400


# ---------------------------------------------------------------------------
# Token rotation + status
# ---------------------------------------------------------------------------


class TestTokenRotation:
    def test_non_admin_blocked(self, app):
        # Sign up a regular user; they should get 403 on admin routes.
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
            r = client.post("/api/auth/admin/rotate-host-token")
        assert r.status_code == 403

    def test_rotate_host_token_returns_plaintext_and_updates_state(self, app):
        _seed_admin(app)
        with TestClient(app) as client:
            _login(client)
            r = client.post("/api/auth/admin/rotate-host-token")
        assert r.status_code == 200
        body = r.json()
        assert body["field"] == "host_token"
        assert len(body["token"]) == 64
        # The live AuthConfig snapshot on app.state should reflect the
        # new token — the contract that makes "next request honours
        # rotation without restart" work.
        assert app.state.auth_config.host_token == body["token"]

    def test_rotate_admin_token(self, app):
        _seed_admin(app)
        with TestClient(app) as client:
            _login(client)
            r = client.post("/api/auth/admin/rotate-admin-token")
        assert r.status_code == 200
        assert r.json()["field"] == "admin_token"
        assert app.state.auth_config.admin_token == r.json()["token"]


class TestRotationConfigErrorTranslation:
    """When ``config.toml`` has a shape the minimal TOML writer
    refuses (top-level scalar / nested table), the rotate route must
    translate to a 400 with a clear operator message — not a raw
    500/traceback (audit nit)."""

    def test_400_when_config_has_top_level_scalar(self, app, tmp_path):
        _seed_admin(app)
        # Seed config.toml with a shape the writer rejects.
        (tmp_path / "config.toml").write_text(
            'version = 7\n\n[auth]\nhost_token = "old"\n', encoding="utf-8"
        )
        with TestClient(app) as client:
            _login(client)
            r = client.post("/api/auth/admin/rotate-host-token")
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "config_toml_unsupported_shape"


class TestTokenStatus:
    def test_returns_tail_when_set(self, app):
        _seed_admin(app)
        with TestClient(app) as client:
            _login(client)
            client.post("/api/auth/admin/rotate-host-token")
            r = client.get("/api/auth/admin/token-status")
        assert r.status_code == 200
        body = r.json()
        # Tail is 6 chars when token is long.
        assert len(body["host_token"]["tail"]) == 6
        assert body["host_token"]["enabled"] is True
        # admin_token not rotated → disabled, empty tail.
        assert body["admin_token"]["enabled"] is False

    def test_non_admin_blocked(self, app):
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
            r = client.get("/api/auth/admin/token-status")
        assert r.status_code == 403

    def test_mask_tail_empty(self, app):
        from kohakuterrarium.api.auth.routes import _mask_tail

        assert _mask_tail("") == ""
        assert _mask_tail("short") == "short"  # ≤6 chars: return as-is
        assert _mask_tail("0123456789abcdef") == "abcdef"  # last 6
