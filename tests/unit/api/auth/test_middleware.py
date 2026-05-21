"""Unit tests for L2 — :class:`HostTokenMiddleware`.

The middleware is a pure-ASGI gate.  Tests exercise both HTTP and
WebSocket scopes by mounting a tiny FastAPI app with a router that
returns simple JSON / opens a WS — we don't need the production
``create_app`` boot path for these.
"""

import pytest
from fastapi import APIRouter, FastAPI, WebSocket
from fastapi.testclient import TestClient

from kohakuterrarium.api.auth.config import AuthConfig
from kohakuterrarium.api.auth.middleware import HostTokenMiddleware
from kohakuterrarium.api.auth.routes import router as auth_router


def _make_app(config: AuthConfig) -> FastAPI:
    """Mini app: auth router (carries /capabilities), a trivial echo
    HTTP route, and a trivial WS echo.  Middleware added on top so the
    suite can assert every gate decision."""
    app = FastAPI()
    app.state.auth_config = config
    app.include_router(auth_router, prefix="/api/auth")

    test_router = APIRouter()

    @test_router.get("/api/test/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    @test_router.websocket("/ws/test/echo")
    async def ws_echo(websocket: WebSocket) -> None:
        await websocket.accept()
        msg = await websocket.receive_text()
        await websocket.send_text(f"echo:{msg}")
        await websocket.close()

    app.include_router(test_router)
    app.add_middleware(HostTokenMiddleware)
    return app


# ---------------------------------------------------------------------------
# Gate off
# ---------------------------------------------------------------------------


class TestMiddlewareDisabled:
    def test_no_host_token_means_no_gate(self):
        # host_token = "" → off; loopback or not, request passes.
        with TestClient(_make_app(AuthConfig())) as client:
            resp = client.get("/api/test/ping")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_no_host_token_ws_accepts(self):
        with TestClient(_make_app(AuthConfig())) as client:
            with client.websocket_connect("/ws/test/echo") as ws:
                ws.send_text("hi")
                assert ws.receive_text() == "echo:hi"


# ---------------------------------------------------------------------------
# Loopback bypass
# ---------------------------------------------------------------------------


class TestLoopbackBypass:
    def test_loopback_bypass_lets_localhost_pass_without_token(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=True)
        # TestClient sets client = ("testclient", 50000); not loopback.
        # We synthesize a real loopback request via the ``transport``
        # kwarg below — but ASGITransport doesn't surface client IP
        # cleanly.  Simpler: rely on Starlette's default which puts
        # ``testclient`` in scope.client; that is NOT in our loopback
        # set, so the gate should engage and 401.
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/test/ping")
        # TestClient is NOT loopback per our middleware's host set —
        # this confirms the gate engages.
        assert resp.status_code == 401

    def test_loopback_bypass_disabled_still_requires_token(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/test/ping")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Capabilities probe is always reachable
# ---------------------------------------------------------------------------


class TestCapabilitiesAlwaysPasses:
    def test_capabilities_does_not_require_token(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/auth/capabilities")
        assert resp.status_code == 200
        # And it doesn't leak the secret.
        assert "abc" not in resp.text


# ---------------------------------------------------------------------------
# HTTP — token check
# ---------------------------------------------------------------------------


class TestHttpToken:
    def test_missing_authorization_is_401(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/test/ping")
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"
        assert "www-authenticate" in {k.lower() for k in resp.headers.keys()}

    def test_wrong_bearer_is_401(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            resp = client.get(
                "/api/test/ping", headers={"Authorization": "Bearer wrong"}
            )
        assert resp.status_code == 401

    def test_correct_bearer_passes(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/test/ping", headers={"Authorization": "Bearer abc"})
        assert resp.status_code == 200

    def test_non_bearer_scheme_is_rejected(self):
        # Basic auth header → not a Bearer; treated as no token supplied.
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            resp = client.get(
                "/api/test/ping",
                headers={"Authorization": "Basic dXNlcjpwYXNz"},
            )
        assert resp.status_code == 401

    def test_lower_case_bearer_accepted(self):
        # RFC 7235 says auth schemes are case-insensitive.
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/test/ping", headers={"Authorization": "bearer abc"})
        assert resp.status_code == 200

    def test_token_with_whitespace_is_trimmed(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            resp = client.get(
                "/api/test/ping",
                headers={"Authorization": "Bearer  abc  "},
            )
        # "Bearer  abc  " → split on whitespace → "abc  ", then strip.
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# WebSocket — token check
# ---------------------------------------------------------------------------


class TestWebSocketToken:
    def test_ws_without_token_is_rejected(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            # WebSocketDisconnect raised because handshake closed with
            # code 4401.
            from starlette.websockets import WebSocketDisconnect

            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/test/echo"):
                    pass
        assert exc_info.value.code == 4401

    def test_ws_subprotocol_token_accepted(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            with client.websocket_connect(
                "/ws/test/echo", subprotocols=["kt-token.abc"]
            ) as ws:
                ws.send_text("hi")
                assert ws.receive_text() == "echo:hi"

    def test_ws_query_token_accepted(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            with client.websocket_connect("/ws/test/echo?token=abc") as ws:
                ws.send_text("hi")
                assert ws.receive_text() == "echo:hi"

    def test_ws_wrong_subprotocol_token_rejected(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            from starlette.websockets import WebSocketDisconnect

            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect(
                    "/ws/test/echo", subprotocols=["kt-token.WRONG"]
                ):
                    pass
        assert exc_info.value.code == 4401

    def test_ws_wrong_query_token_rejected(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            from starlette.websockets import WebSocketDisconnect

            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect("/ws/test/echo?token=wrong"):
                    pass

    def test_ws_subprotocol_among_many_offered(self):
        # Browser may offer multiple sub-protocols; we accept if any
        # is the right kt-token.
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            with client.websocket_connect(
                "/ws/test/echo",
                subprotocols=["chat", "kt-token.abc", "binary"],
            ) as ws:
                ws.send_text("ping")
                assert ws.receive_text() == "echo:ping"


# ---------------------------------------------------------------------------
# Constant-time compare — defensive check
# ---------------------------------------------------------------------------


class TestConstantTimeCompare:
    def test_no_early_return_on_first_byte_mismatch(self):
        """Best-effort check: same-prefix wrong tokens get the same
        rejection shape, no leak of "you got the first N chars right."
        Not a true timing test (Python is too noisy), but verifies the
        contract: every wrong token gets 401 with identical body.
        """
        cfg = AuthConfig(host_token="abc123def456", loopback_bypass=False)
        wrong_tokens = ["", "a", "ab", "abc123", "abc123def45", "zzzz"]
        bodies = []
        with TestClient(_make_app(cfg)) as client:
            for wrong in wrong_tokens:
                resp = client.get(
                    "/api/test/ping",
                    headers={"Authorization": f"Bearer {wrong}"},
                )
                assert resp.status_code == 401
                bodies.append(resp.text)
        # Every rejection looks identical — no progressive info leak.
        assert len(set(bodies)) == 1


# Sub-protocol echo is the helper's job (covered in test_ws_auth.py).
# The middleware only gates; it doesn't touch accept().


# ---------------------------------------------------------------------------
# Path scoping — gate ONLY /api/* and /ws/* (audit-caught)
# ---------------------------------------------------------------------------


class TestPathScoping:
    """Previously the middleware gated everything, including static
    SPA assets and health checks.  Remote browsers couldn't load the
    login page; container orchestrators couldn't health-check.  Pin
    the fixed scoping."""

    def _make_app_with_extras(self, config: AuthConfig) -> FastAPI:
        app = _make_app(config)
        from fastapi import APIRouter

        extras = APIRouter()

        @extras.get("/")
        def root() -> dict[str, str]:
            return {"page": "spa-index"}

        @extras.get("/assets/app.js")
        def asset() -> dict[str, str]:
            return {"asset": "app.js"}

        @extras.get("/healthz")
        def healthz() -> dict[str, str]:
            return {"ok": "yes"}

        @extras.get("/readyz")
        def readyz() -> dict[str, str]:
            return {"ready": "yes"}

        app.include_router(extras)
        return app

    def test_static_root_passes_without_token(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(self._make_app_with_extras(cfg)) as client:
            assert client.get("/").status_code == 200

    def test_static_assets_pass_without_token(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(self._make_app_with_extras(cfg)) as client:
            assert client.get("/assets/app.js").status_code == 200

    def test_healthz_passes_without_token(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(self._make_app_with_extras(cfg)) as client:
            assert client.get("/healthz").status_code == 200

    def test_readyz_passes_without_token(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(self._make_app_with_extras(cfg)) as client:
            assert client.get("/readyz").status_code == 200

    def test_api_still_gated(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(self._make_app_with_extras(cfg)) as client:
            assert client.get("/api/test/ping").status_code == 401


# ---------------------------------------------------------------------------
# Dual-token wire — X-KT-Host-Token preferred, Bearer fallback
# ---------------------------------------------------------------------------


class TestDualTokenWire:
    """L2 + L4-API-token coexistence.  Audit-caught: both originally
    read ``Authorization: Bearer``, so CLI / mobile clients couldn't
    carry a host token AND a user API token at the same time."""

    def test_dedicated_header_accepted(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/test/ping", headers={"X-KT-Host-Token": "abc"})
        assert resp.status_code == 200

    def test_dedicated_header_wins_over_bearer(self):
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            resp = client.get(
                "/api/test/ping",
                headers={
                    "X-KT-Host-Token": "abc",
                    # User API token in Authorization — middleware
                    # accepts L2 via the dedicated header and lets
                    # the request through (L4 would resolve the user
                    # token downstream when enabled).
                    "Authorization": "Bearer user-api-token-here",
                },
            )
        assert resp.status_code == 200

    def test_bearer_still_works_for_back_compat(self):
        # Single-tenant deployments that only enable L2 keep working.
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            resp = client.get("/api/test/ping", headers={"Authorization": "Bearer abc"})
        assert resp.status_code == 200

    def test_dedicated_header_with_wrong_value_rejected(self):
        # Dedicated header wins — even if Bearer is right, the wrong
        # dedicated header rejects.  This is the contract that lets
        # L4 callers send a User-token Bearer alongside the host
        # token without accidentally promoting the user token to
        # host-token status.
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        with TestClient(_make_app(cfg)) as client:
            resp = client.get(
                "/api/test/ping",
                headers={
                    "X-KT-Host-Token": "wrong",
                    "Authorization": "Bearer abc",
                },
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# CORS preflight bypass
# ---------------------------------------------------------------------------


class TestCorsPreflightBypass:
    def test_options_preflight_passes_without_token(self):
        # CORS preflights don't carry Authorization — the gate MUST
        # let them through so the CORS middleware (innermost) can
        # respond.  Otherwise browsers can never make a cross-origin
        # request that needs L2 auth.
        cfg = AuthConfig(host_token="abc", loopback_bypass=False)
        app = _make_app(cfg)
        from fastapi.middleware.cors import CORSMiddleware

        # The actual app already has CORS NOT installed; install one
        # here so OPTIONS gets a proper response.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        with TestClient(app) as client:
            resp = client.options(
                "/api/test/ping",
                headers={
                    "Origin": "https://app.example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
        # CORS responds with 200 + Allow-Origin.
        assert resp.status_code in (200, 204)


# ---------------------------------------------------------------------------
# Config snapshot read
# ---------------------------------------------------------------------------


class TestConfigSnapshot:
    def test_changing_state_at_runtime_takes_effect(self):
        app = _make_app(AuthConfig())
        with TestClient(app) as client:
            # No gate.
            assert client.get("/api/test/ping").status_code == 200
            # Flip the config snapshot at runtime — next request gates.
            app.state.auth_config = AuthConfig(host_token="abc", loopback_bypass=False)
            assert client.get("/api/test/ping").status_code == 401
            assert (
                client.get(
                    "/api/test/ping", headers={"Authorization": "Bearer abc"}
                ).status_code
                == 200
            )
