"""Unit tests for ``GET /api/auth/capabilities``.

The endpoint is unauthenticated by design — verify it never returns
secrets, never requires auth headers, and reports the right enabled
flags for every config combination.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.routes import router


def _make_app(config: AuthConfig) -> FastAPI:
    """Build a tiny FastAPI app mounting only the auth router.

    Avoids ``create_app``'s heavy boot path (terrarium engine, lab
    transport, static SPA) — the capabilities route only depends on
    ``app.state.auth_config`` resolved via the FastAPI dependency.
    """
    app = FastAPI()
    app.state.auth_config = config
    app.include_router(router, prefix="/api/auth")
    return app


class TestCapabilitiesShape:
    def test_default_all_off(self):
        with TestClient(_make_app(AuthConfig())) as client:
            resp = client.get("/api/auth/capabilities")
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema"] == 1
        assert body["auth"]["host_token"]["enabled"] is False
        assert body["auth"]["admin_token"]["enabled"] is False
        assert body["auth"]["multi_user"]["enabled"] is False
        assert body["auth"]["multi_user"]["mode"] == "off"

    def test_host_token_enabled_reports_loopback_bypass(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/auth/capabilities")
        body = resp.json()
        assert body["auth"]["host_token"] == {
            "enabled": True,
            "loopback_bypass": False,
        }

    def test_multi_user_reports_mode_and_registration(self):
        cfg = AuthConfig(multi_user="required", registration="invite_only")
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/auth/capabilities")
        body = resp.json()
        assert body["auth"]["multi_user"] == {
            "enabled": True,
            "mode": "required",
            "registration": "invite_only",
        }

    def test_admin_token_enabled(self):
        cfg = AuthConfig(admin_token="zzz")
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/auth/capabilities")
        body = resp.json()
        assert body["auth"]["admin_token"]["enabled"] is True

    def test_all_layers_enabled(self):
        cfg = AuthConfig(
            host_token="ht",
            admin_token="at",
            multi_user="required",
            registration="open",
            loopback_bypass=False,
        )
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/auth/capabilities")
        body = resp.json()
        assert body["auth"]["host_token"]["enabled"] is True
        assert body["auth"]["admin_token"]["enabled"] is True
        assert body["auth"]["multi_user"]["enabled"] is True


class TestCapabilitiesSecuritySurface:
    def test_no_auth_headers_required(self):
        """The capabilities probe MUST work without any credentials —
        the frontend hits this BEFORE it knows what credentials it
        even needs to supply.
        """
        cfg = AuthConfig(host_token="abc", admin_token="xyz")
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/auth/capabilities")
        # No Authorization header set; should still succeed.
        assert resp.status_code == 200

    def test_response_never_contains_secrets(self):
        cfg = AuthConfig(
            host_token="VERY-SECRET-HOST-TOKEN",
            admin_token="ANOTHER-SECRET-ADMIN",
        )
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/auth/capabilities")
        body_text = resp.text
        assert "VERY-SECRET-HOST-TOKEN" not in body_text
        assert "ANOTHER-SECRET-ADMIN" not in body_text


class TestCapabilitiesFallbackLoad:
    def test_app_without_state_loads_fresh_config(self, monkeypatch):
        """Routers mounted on a vanilla FastAPI without
        ``app.state.auth_config`` still resolve via fresh
        ``load_auth_config()``.  Useful for unit tests that drive
        routers directly.
        """
        monkeypatch.setenv("KT_AUTH_HOST_TOKEN", "from-env")
        app = FastAPI()
        # NOTE: no app.state.auth_config set.
        app.include_router(router, prefix="/api/auth")
        with TestClient(app) as client:
            resp = client.get("/api/auth/capabilities")
        assert resp.status_code == 200
        body = resp.json()
        # The fresh load picked up the env var.
        assert body["auth"]["host_token"]["enabled"] is True


@pytest.mark.parametrize(
    "multi_user_value, registration_value, expected_enabled",
    [
        ("off", "admin_only", False),
        ("optional", "open", True),
        ("optional", "invite_only", True),
        ("required", "invite_only", True),
        ("required", "admin_only", True),
    ],
)
def test_multi_user_matrix(multi_user_value, registration_value, expected_enabled):
    cfg = AuthConfig(multi_user=multi_user_value, registration=registration_value)
    with TestClient(_make_app(cfg)) as client:
        resp = client.get("/api/auth/capabilities")
    body = resp.json()
    assert body["auth"]["multi_user"]["enabled"] is expected_enabled
    assert body["auth"]["multi_user"]["mode"] == multi_user_value
    assert body["auth"]["multi_user"]["registration"] == registration_value
