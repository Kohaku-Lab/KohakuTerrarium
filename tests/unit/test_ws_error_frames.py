"""Unit tests for WebSocket error-frame handling.

Every WebSocket endpoint that closes the connection on an internal
error should first emit a structured ``{"type": "error", ...}`` frame
so clients (browser, wscat, anything) can surface a real cause instead
of a bare ``Disconnected (code: 1000)``.

Covered endpoints:
  * /ws/agents/{agent_id}/chat           (startup validation)
  * /ws/terrariums/{terrarium_id}/channels
  * /ws/terrariums/{terrarium_id}         (existing startup validation)
  * /ws/creatures/{agent_id}              (existing startup validation)
  * /ws/logs                              (existing no-log-file path)

These tests use ``TestClient`` + ``app.dependency_overrides[get_manager]``
in the same style as ``tests/unit/test_api_readonly_endpoints.py``.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from kohakuterrarium.api.deps import get_manager
from kohakuterrarium.api.ws import agents as agents_ws
from kohakuterrarium.api.ws import channels as channels_ws
from kohakuterrarium.api.ws import chat as chat_ws
from kohakuterrarium.api.ws import logs as logs_ws


def _build_client(fake_manager) -> TestClient:
    """FastAPI test app wired with all WS routers and a fake manager."""
    app = FastAPI()
    app.include_router(agents_ws.router)
    app.include_router(channels_ws.router)
    app.include_router(chat_ws.router)
    app.dependency_overrides[get_manager] = lambda: fake_manager
    return TestClient(app)


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
# /ws/agents/{agent_id}/chat — startup validation (new behavior)
# ----------------------------------------------------------------------


def test_ws_agents_invalid_id_sends_error_frame_before_close():
    fake_manager = SimpleNamespace(_agents={})
    client = _build_client(fake_manager)

    with client.websocket_connect("/ws/agents/nonexistent/chat") as ws:
        _assert_error_frame_then_close(ws)


# ----------------------------------------------------------------------
# /ws/terrariums/{terrarium_id}/channels — catch-all send_json (new behavior)
# ----------------------------------------------------------------------


class _RaisingChannelStreamManager:
    """Manager whose ``terrarium_channel_stream`` raises on first iteration."""

    async def terrarium_channel_stream(self, tid, channels=None):
        raise ValueError(f"Terrarium not found: {tid}")
        yield  # pragma: no cover — keeps this an async generator


def test_ws_channels_stream_error_sends_error_frame_before_close():
    client = _build_client(_RaisingChannelStreamManager())

    with client.websocket_connect("/ws/terrariums/nope/channels") as ws:
        _assert_error_frame_then_close(ws)


# ----------------------------------------------------------------------
# /ws/terrariums/{terrarium_id} — existing startup validation (regression)
# ----------------------------------------------------------------------


class _UnknownRuntimeManager:
    """Manager whose ``_get_runtime`` always reports the terrarium missing."""

    def _get_runtime(self, tid):
        raise ValueError(f"Terrarium not found: {tid}")


def test_ws_terrarium_invalid_id_sends_error_frame_before_close():
    client = _build_client(_UnknownRuntimeManager())

    with client.websocket_connect("/ws/terrariums/nope") as ws:
        _assert_error_frame_then_close(ws)


# ----------------------------------------------------------------------
# /ws/creatures/{agent_id} — existing startup validation (regression)
# ----------------------------------------------------------------------


def test_ws_creature_invalid_id_sends_error_frame_before_close():
    fake_manager = SimpleNamespace(_agents={})
    client = _build_client(fake_manager)

    with client.websocket_connect("/ws/creatures/nope") as ws:
        _assert_error_frame_then_close(ws)


# ----------------------------------------------------------------------
# /ws/logs — no log file path (existing) + new error frame on catch-all
# ----------------------------------------------------------------------


def test_ws_logs_no_log_file_sends_error_frame_before_close(monkeypatch):
    monkeypatch.setattr(logs_ws, "_find_current_process_log", lambda: None)

    app = FastAPI()
    app.include_router(logs_ws.router)
    client = TestClient(app)

    with client.websocket_connect("/ws/logs") as ws:
        _assert_error_frame_then_close(ws, key="text")
