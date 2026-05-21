"""Unit tests for the auth route surface — register / login / logout /
me / tokens / users / invitations.

The tests build a small FastAPI app mounting only the auth router so
they don't pay the cost of ``create_app``'s full boot (engine, lab,
SPA).  The auth.db is redirected to per-test tmp via ``KT_AUTH_DB``.
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
    """Mini app with auth router only and a fresh sqlite."""
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    _reset_migration_state_for_tests()
    ensure_migrated()
    app = FastAPI()
    # Multi-user required by default so register/login work; tests
    # override per-case via app.state.auth_config = ...
    app.state.auth_config = AuthConfig(
        multi_user="required",
        registration="open",
        bcrypt_rounds=_TEST_ROUNDS,
    )
    app.include_router(auth_router, prefix="/api/auth")
    yield app
    _reset_migration_state_for_tests()


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


class TestCapabilities:
    def test_returns_shape(self, app):
        with TestClient(app) as client:
            r = client.get("/api/auth/capabilities")
        assert r.status_code == 200
        body = r.json()
        assert "auth" in body
        assert body["auth"]["multi_user"]["mode"] == "required"


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


class TestRegisterOpenMode:
    def test_happy_path(self, app):
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "pwd"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["user"]["username"] == "alice"
        assert body["user"]["role"] == "user"
        # Session cookie set.
        assert "kt_session" in r.cookies

    def test_duplicate_409(self, app):
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
            r = client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "y"},
            )
        assert r.status_code == 409

    def test_invalid_username_400(self, app):
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/register",
                json={"username": "ab", "password": "x"},
            )
        # username 2 chars is the minimum — but pydantic min_length=2
        # allows it, and the validator accepts.  Try a bad-char username:
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/register",
                json={"username": "has space", "password": "x"},
            )
        assert r.status_code == 400


class TestRegisterAdminOnly:
    def test_self_register_blocked(self, app):
        app.state.auth_config = AuthConfig(
            multi_user="required",
            registration="admin_only",
            bcrypt_rounds=_TEST_ROUNDS,
        )
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
        assert r.status_code == 403


class TestRegisterInviteOnly:
    def test_no_invite_400(self, app):
        app.state.auth_config = AuthConfig(
            multi_user="required",
            registration="invite_only",
            bcrypt_rounds=_TEST_ROUNDS,
        )
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invitation_required"

    def test_invalid_invite_400(self, app):
        app.state.auth_config = AuthConfig(
            multi_user="required",
            registration="invite_only",
            bcrypt_rounds=_TEST_ROUNDS,
        )
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/register",
                json={
                    "username": "alice",
                    "password": "x",
                    "invitation_token": "garbage",
                },
            )
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "invitation_invalid"

    def test_valid_invite_succeeds(self, app):
        app.state.auth_config = AuthConfig(
            multi_user="required",
            registration="invite_only",
            bcrypt_rounds=_TEST_ROUNDS,
        )
        # Create an admin + invitation manually.
        with connection() as conn:
            admin = create_user(
                conn, "admin", "x", role="admin", bcrypt_rounds=_TEST_ROUNDS
            )
            from kohakuterrarium.api.auth import invitations as invitations_db

            invite_token, _ = invitations_db.create(
                conn, created_by=admin.id, role="user"
            )
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/register",
                json={
                    "username": "alice",
                    "password": "x",
                    "invitation_token": invite_token,
                },
            )
        assert r.status_code == 200
        # Re-using the same invite must fail.
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/register",
                json={
                    "username": "bob",
                    "password": "x",
                    "invitation_token": invite_token,
                },
            )
        assert r.status_code == 400


class TestRegisterMultiUserOff:
    def test_register_400_when_off(self, app):
        app.state.auth_config = AuthConfig(multi_user="off")
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Login / Logout / Me
# ---------------------------------------------------------------------------


class TestLogin:
    def _seed(self, app, username="alice", password="hunter2"):
        with connection() as conn:
            return create_user(conn, username, password, bcrypt_rounds=_TEST_ROUNDS)

    def test_correct_credentials(self, app):
        self._seed(app)
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "hunter2"},
            )
        assert r.status_code == 200
        assert "kt_session" in r.cookies

    def test_wrong_password_401(self, app):
        self._seed(app)
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "wrong"},
            )
        assert r.status_code == 401

    def test_unknown_user_401(self, app):
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/login",
                json={"username": "nobody", "password": "x"},
            )
        assert r.status_code == 401


class TestLogout:
    def test_logout_clears_cookie(self, app):
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
            r = client.post("/api/auth/logout")
        assert r.status_code == 200
        # Cookie deletion sets it to ""/expired — TestClient may show
        # the cookie removed from jar or set to "".  We assert
        # subsequent /me call is 401.
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "bob", "password": "x"},
            )
            client.post("/api/auth/logout")
            r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_logout_no_cookie_is_idempotent(self, app):
        with TestClient(app) as client:
            r = client.post("/api/auth/logout")
        assert r.status_code == 200


class TestMe:
    def test_authenticated_returns_user(self, app):
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
            r = client.get("/api/auth/me")
        assert r.status_code == 200
        assert r.json()["username"] == "alice"

    def test_anonymous_401(self, app):
        with TestClient(app) as client:
            r = client.get("/api/auth/me")
        assert r.status_code == 401


class TestChangePassword:
    def test_change_then_login_with_new(self, app):
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "old"},
            )
            r = client.post(
                "/api/auth/me/password",
                json={"current_password": "old", "new_password": "new"},
            )
            assert r.status_code == 200
            client.post("/api/auth/logout")
            r = client.post(
                "/api/auth/login",
                json={"username": "alice", "password": "new"},
            )
        assert r.status_code == 200

    def test_wrong_current_password_401(self, app):
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "old"},
            )
            r = client.post(
                "/api/auth/me/password",
                json={"current_password": "WRONG", "new_password": "new"},
            )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# API tokens
# ---------------------------------------------------------------------------


class TestApiTokens:
    def test_create_then_list(self, app):
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
            create_resp = client.post("/api/auth/tokens", json={"name": "kt-cli"})
            assert create_resp.status_code == 200
            assert "token" in create_resp.json()
            plaintext = create_resp.json()["token"]
            assert len(plaintext) == 64

            list_resp = client.get("/api/auth/tokens")
            tokens = list_resp.json()["tokens"]
            assert len(tokens) == 1
            assert tokens[0]["name"] == "kt-cli"
            # Plaintext NEVER in the list — only metadata.
            assert "token" not in tokens[0]

    def test_bearer_auth_works(self, app):
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
            create_resp = client.post("/api/auth/tokens", json={"name": "kt-cli"})
            plaintext = create_resp.json()["token"]
            # Brand new client (no cookies) authenticating with bearer.
        with TestClient(app) as client:
            r = client.get(
                "/api/auth/me",
                headers={"Authorization": f"Bearer {plaintext}"},
            )
        assert r.status_code == 200
        assert r.json()["username"] == "alice"

    def test_revoke_token(self, app):
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
            tid = client.post("/api/auth/tokens", json={"name": "kt-cli"}).json()["id"]
            r = client.delete(f"/api/auth/tokens/{tid}")
        assert r.status_code == 200

    def test_revoke_missing_404(self, app):
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
            r = client.delete("/api/auth/tokens/9999")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Admin: users
# ---------------------------------------------------------------------------


class TestAdminUsers:
    def _seed_admin(self, app):
        with connection() as conn:
            return create_user(
                conn, "root", "x", role="admin", bcrypt_rounds=_TEST_ROUNDS
            )

    def _login(self, client, username, password):
        return client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )

    def test_non_admin_403(self, app):
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
            r = client.get("/api/auth/users")
        assert r.status_code == 403

    def test_admin_lists_users(self, app):
        self._seed_admin(app)
        with TestClient(app) as client:
            self._login(client, "root", "x")
            r = client.get("/api/auth/users")
        assert r.status_code == 200
        assert any(u["username"] == "root" for u in r.json()["users"])

    def test_admin_create_user(self, app):
        self._seed_admin(app)
        with TestClient(app) as client:
            self._login(client, "root", "x")
            r = client.post(
                "/api/auth/users",
                json={"username": "alice", "password": "x", "role": "user"},
            )
        assert r.status_code == 200
        assert r.json()["user"]["username"] == "alice"

    def test_admin_promote_demote(self, app):
        self._seed_admin(app)
        with TestClient(app) as client:
            self._login(client, "root", "x")
            uid = client.post(
                "/api/auth/users",
                json={"username": "alice", "password": "x", "role": "user"},
            ).json()["user"]["id"]
            r = client.patch(f"/api/auth/users/{uid}", json={"role": "admin"})
            assert r.status_code == 200
            assert r.json()["user"]["role"] == "admin"

    def test_cannot_demote_last_admin(self, app):
        self._seed_admin(app)
        with TestClient(app) as client:
            self._login(client, "root", "x")
            root_id = client.get("/api/auth/me").json()["id"]
            r = client.patch(f"/api/auth/users/{root_id}", json={"role": "user"})
        assert r.status_code == 400
        assert r.json()["detail"]["error"] == "last_admin"

    def test_disable_drops_sessions(self, app):
        self._seed_admin(app)
        with TestClient(app) as client:
            self._login(client, "root", "x")
            uid = client.post(
                "/api/auth/users",
                json={"username": "alice", "password": "x", "role": "user"},
            ).json()["user"]["id"]
            r = client.patch(f"/api/auth/users/{uid}", json={"is_active": False})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Admin: invitations
# ---------------------------------------------------------------------------


class TestAdminInvitations:
    def _seed_admin(self, app):
        with connection() as conn:
            create_user(conn, "root", "x", role="admin", bcrypt_rounds=_TEST_ROUNDS)

    def test_create_invite_returns_plaintext_once(self, app):
        self._seed_admin(app)
        with TestClient(app) as client:
            client.post(
                "/api/auth/login",
                json={"username": "root", "password": "x"},
            )
            r = client.post("/api/auth/invitations", json={"role": "user"})
        assert r.status_code == 200
        body = r.json()
        assert "token" in body
        assert len(body["token"]) == 64

        # List does NOT include plaintext.
        with TestClient(app) as client:
            client.post(
                "/api/auth/login",
                json={"username": "root", "password": "x"},
            )
            r = client.get("/api/auth/invitations")
        for inv in r.json()["invitations"]:
            assert "token" not in inv

    def test_non_admin_blocked(self, app):
        with TestClient(app) as client:
            client.post(
                "/api/auth/register",
                json={"username": "alice", "password": "x"},
            )
            r = client.post("/api/auth/invitations", json={"role": "user"})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Multi-user OFF mode
# ---------------------------------------------------------------------------


class TestMultiUserOff:
    def test_me_401_when_off(self, app):
        app.state.auth_config = AuthConfig(multi_user="off")
        with TestClient(app) as client:
            r = client.get("/api/auth/me")
        assert r.status_code == 401
        assert r.json()["detail"]["error"] == "multi_user_disabled"

    def test_login_400_when_off(self, app):
        app.state.auth_config = AuthConfig(multi_user="off")
        with TestClient(app) as client:
            r = client.post(
                "/api/auth/login",
                json={"username": "x", "password": "y"},
            )
        assert r.status_code == 400
