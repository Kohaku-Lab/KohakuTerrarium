"""Unit tests for WebSocket error-frame handling.

Every WebSocket endpoint that closes the connection on an internal
error should first emit a structured ``{"type": "error", ...}`` frame
so clients (browser, wscat, anything) can surface a real cause instead
of a bare ``Disconnected (code: 1000)``.

Phase 3 collapsed the legacy ``/ws/agents/{id}/chat``,
``/ws/terrariums/{id}``, ``/ws/creatures/{id}`` and
``/ws/terrariums/{id}/channels`` endpoints into the engine-backed
``/ws/sessions/{sid}/creatures/{cid}/chat`` and
``/ws/sessions/{sid}/observer`` routes.  The error-frame contract
moved with them.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kohakuterrarium.api.deps import get_engine
from kohakuterrarium.api.ws import io as io_ws
from kohakuterrarium.api.ws import logs as logs_ws
from kohakuterrarium.api.ws import observer as observer_ws


def _build_client(fake_engine) -> TestClient:
    """FastAPI test app wired with the engine-backed WS routers."""
    app = FastAPI()
    app.include_router(io_ws.router)
    app.include_router(observer_ws.router)
    app.dependency_overrides[get_engine] = lambda: fake_engine
    # The router resolves ``get_engine`` through ``api.deps`` directly
    # (no Depends), so monkeypatch that lookup as well.
    return app


def _assert_error_frame_then_close(ws, *, key: str = "content") -> None:
    """Verify the next message is an error frame and the server then closes."""
    msg = ws.receive_json()
    assert msg["type"] == "error"
    assert (
        key in msg and msg[key]
    ), f"error frame missing non-empty '{key}' field: {msg!r}"
    with pytest.raises(WebSocketDisconnect):
        ws.receive_json()


# ----------------------------------------------------------------------
# /ws/sessions/{sid}/creatures/{cid}/chat — startup validation
# ----------------------------------------------------------------------


def test_ws_io_invalid_creature_sends_error_frame_before_close(monkeypatch):
    class _Engine:
        def get_creature(self, cid):
            raise KeyError(cid)

    fake_engine = _Engine()
    monkeypatch.setattr(io_ws, "get_engine", lambda: fake_engine)
    app = _build_client(fake_engine)
    client = TestClient(app)

    with client.websocket_connect("/ws/sessions/sess1/creatures/nope/chat") as ws:
        _assert_error_frame_then_close(ws)


# ----------------------------------------------------------------------
# /ws/sessions/{sid}/observer — startup validation
# ----------------------------------------------------------------------


def test_ws_observer_invalid_session_sends_error_frame_before_close(monkeypatch):
    class _Engine:
        _environments: dict = {}

        def get_creature(self, cid):  # pragma: no cover — unused here
            raise KeyError(cid)

    fake_engine = _Engine()
    monkeypatch.setattr(observer_ws, "get_engine", lambda: fake_engine)
    app = _build_client(fake_engine)
    client = TestClient(app)

    with client.websocket_connect("/ws/sessions/nope/observer") as ws:
        _assert_error_frame_then_close(ws)


# ----------------------------------------------------------------------
# /ws/logs — no log file path
# ----------------------------------------------------------------------


def test_ws_logs_no_log_file_sends_error_frame_before_close(monkeypatch):
    monkeypatch.setattr(logs_ws, "_find_current_process_log", lambda: None)

    app = FastAPI()
    app.include_router(logs_ws.router)
    client = TestClient(app)

    with client.websocket_connect("/ws/logs") as ws:
        _assert_error_frame_then_close(ws, key="text")


# Suppress unused-import warning — SimpleNamespace kept for parity with
# the legacy test fixture style; future tests may reuse it.
_ = SimpleNamespace
