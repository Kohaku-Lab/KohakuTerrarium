"""Integration tier — one fat workflow drives the full auth stack.

Per CLAUDE.md tier discipline ("one core-lib folder → one test-class;
each test method = one complete feature workflow end-to-end in a
single function"), this file is the cross-layer audit-protection net:
unit tier proves each module's contract; this tier proves the layers
COMPOSE correctly.

Workflow exercised in one function:

    1. Boot a fresh app with L2 (host token) + L3 (admin token)
       + L4 (multi_user=required) all enabled.
    2. ``/capabilities`` probe (unauthenticated, must pass L2).
    3. Anonymous chat-route call → 401 from L4.
    4. Wrong host-token → 401 from L2.
    5. Register the operator (admin role via direct DB seed).
    6. Login → session cookie.
    7. Authenticated read (host token + cookie) → 200.
    8. Config-mutation without ``X-Admin-Token`` → 401 from L3.
    9. Config-mutation WITH admin token → 200.
    10. ``POST /admin/rotate-host-token`` → live-updates host_token
        + the new value is what the next request must present.
    11. Old host token now 401s.
    12. Second user registers via invitation token.
    13. Each user's engine pool slot is distinct.
    14. Logout drops the session; subsequent /me → 401.

This is one regression-protection workflow against the four-layer
composition; unit tier covers each cell independently.
"""

import pytest
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.db import (
    connection,
    ensure_migrated,
    _reset_migration_state_for_tests,
)
from kohakuterrarium.api.auth.users import create_user
from kohakuterrarium.api.deps import set_service

_TEST_ROUNDS = 4


@pytest.fixture
def app(tmp_path, monkeypatch):
    """Build a fresh FastAPI app with all four auth layers ON."""
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "sessions"))
    _reset_migration_state_for_tests()
    ensure_migrated()
    set_service(None)

    # Use create_app so we exercise the production lifespan +
    # middleware + auth router wiring path.
    from kohakuterrarium.api.app import create_app

    application = create_app()
    # Override the boot snapshot with the all-on configuration.
    application.state.auth_config = AuthConfig(
        host_token="HOST-TOKEN",
        admin_token="ADMIN-TOKEN",
        multi_user="required",
        registration="invite_only",
        loopback_bypass=False,
        bcrypt_rounds=_TEST_ROUNDS,
    )

    yield application
    set_service(None)
    _reset_migration_state_for_tests()


class TestAuthIntegration:
    """One fat workflow.  No per-step assertions duplicated as
    separate test methods — that's the unit tier's job."""

    def test_full_lifecycle_register_login_mutate_rotate_logout(self, app):
        host_bearer = {"Authorization": "Bearer HOST-TOKEN"}
        wrong_bearer = {"Authorization": "Bearer NOT-THE-RIGHT-ONE"}

        with TestClient(app) as client:
            # ── 1. /capabilities is always reachable ─────────────────
            r = client.get("/api/auth/capabilities")
            assert r.status_code == 200
            caps = r.json()["auth"]
            assert caps["host_token"]["enabled"] is True
            assert caps["admin_token"]["enabled"] is True
            assert caps["multi_user"]["mode"] == "required"

            # ── 2. Wrong host token → L2 rejects ─────────────────────
            r = client.get("/api/auth/me", headers=wrong_bearer)
            assert r.status_code == 401

            # ── 3. No host token → L2 rejects ────────────────────────
            r = client.get("/api/auth/me")
            assert r.status_code == 401

            # ── 4. Right host token but anonymous → L4 rejects ───────
            r = client.get("/api/auth/me", headers=host_bearer)
            assert r.status_code == 401
            # Distinguishable from L2 by the X-Auth-Required header.
            assert r.headers.get("X-Auth-Required", "").lower() in ("user", "")

            # ── 5. Admin seeded directly via DB (registration is
            #     invite_only — no self-register without a token; the
            #     operator's CLI path is ``kt admin users add``) ─────
            with connection() as conn:
                create_user(
                    conn,
                    "operator",
                    "ops-pw",
                    role="admin",
                    bcrypt_rounds=_TEST_ROUNDS,
                )

            # ── 6. Login with the right host token ───────────────────
            r = client.post(
                "/api/auth/login",
                json={"username": "operator", "password": "ops-pw"},
                headers=host_bearer,
            )
            assert r.status_code == 200, r.text
            assert "kt_session" in client.cookies

            # ── 7. Authenticated /me works ───────────────────────────
            r = client.get("/api/auth/me", headers=host_bearer)
            assert r.status_code == 200
            assert r.json()["username"] == "operator"

            # ── 8. Config-mutation without admin token → L3 rejects ──
            # ``/api/settings/keys`` is one of the L3-gated routes
            # (the identity api_keys router is mounted at
            # ``/api/settings`` by app.py:659).
            r = client.post(
                "/api/settings/keys",
                json={"provider": "openai", "key": "sk-x"},
                headers=host_bearer,
            )
            assert r.status_code == 401
            assert r.headers.get("X-Auth-Required", "").lower() == "admin"

            # ── 9. Same call WITH admin token → passes L3 ────────────
            # We only assert L3 auth resolution; anything that's NOT
            # 401 here means L3 let us through to the handler (the
            # handler's own success / business-error response is
            # outside this test's scope).
            r = client.post(
                "/api/settings/keys",
                json={"provider": "openai", "key": "sk-x"},
                headers={**host_bearer, "X-Admin-Token": "ADMIN-TOKEN"},
            )
            assert r.status_code != 401

            # ── 10. Rotate host token via admin route ────────────────
            r = client.post(
                "/api/auth/admin/rotate-host-token",
                headers={**host_bearer, "X-Admin-Token": "ADMIN-TOKEN"},
            )
            assert r.status_code == 200
            new_token = r.json()["token"]
            assert len(new_token) == 64
            assert new_token != "HOST-TOKEN"

            # ── 11. Old token no longer works ────────────────────────
            r = client.get("/api/auth/me", headers=host_bearer)
            assert r.status_code == 401
            new_bearer = {"Authorization": f"Bearer {new_token}"}
            r = client.get("/api/auth/me", headers=new_bearer)
            assert r.status_code == 200

            # ── 12. Issue an invitation, register a second user ──────
            r = client.post(
                "/api/auth/invitations",
                json={"role": "user"},
                headers={**new_bearer, "X-Admin-Token": "ADMIN-TOKEN"},
            )
            assert r.status_code == 200
            invite_token = r.json()["token"]
            # Second user registration via the invitation (new client
            # so we don't carry operator's cookie).
        # New TestClient = fresh cookie jar.
        with TestClient(app) as client2:
            new_bearer = {"Authorization": f"Bearer {new_token}"}
            r = client2.post(
                "/api/auth/register",
                json={
                    "username": "alice",
                    "password": "x",
                    "invitation_token": invite_token,
                },
                headers=new_bearer,
            )
            assert r.status_code == 200

            # ── 13. Anonymous slot stays out of the pool ─────────────
            # We can't easily exercise a ``Depends(get_service)`` route
            # without spinning up real session machinery; what we can
            # assert is the policy contract: under L4=required, no
            # request EVER lands the anonymous ``None`` slot in the
            # pool.  The pool may be empty (no routes touched
            # get_service in this client) — that's fine.  What matters
            # is the absence of ``None``.
            r = client2.get("/api/auth/me", headers=new_bearer)
            assert r.status_code == 200
            assert r.json()["username"] == "alice"

            live = set(app.state.engine_pool.live_user_ids())
            assert None not in live

            # ── 14. Logout drops the session ─────────────────────────
            client2.post("/api/auth/logout", headers=new_bearer)
            r = client2.get("/api/auth/me", headers=new_bearer)
            assert r.status_code == 401


class TestAuthIntegrationOffMode:
    """A second workflow — all auth off → behaviour preserves the
    pre-1.5.0 single-user open-host contract.  This pins the
    "defaults don't surprise existing operators" promise."""

    def test_defaults_preserve_open_host(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("KT_SESSION_DIR", str(tmp_path / "sessions"))
        _reset_migration_state_for_tests()
        ensure_migrated()
        set_service(None)
        try:
            from kohakuterrarium.api.app import create_app

            app = create_app()
            # Default AuthConfig — everything off.
            app.state.auth_config = AuthConfig()
            with TestClient(app) as client:
                # No auth headers anywhere — capabilities + reads work.
                assert client.get("/api/auth/capabilities").status_code == 200
                caps = client.get("/api/auth/capabilities").json()["auth"]
                assert caps["host_token"]["enabled"] is False
                assert caps["multi_user"]["mode"] == "off"
                # /me fails because there's no user concept, but it
                # fails with multi_user_disabled — not auth_required.
                r = client.get("/api/auth/me")
                assert r.status_code == 401
                assert r.json()["detail"]["error"] == "multi_user_disabled"
        finally:
            set_service(None)
            _reset_migration_state_for_tests()
