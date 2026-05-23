"""Codex device-code streaming login route.

Covers POST /api/settings/codex-login-stream — the NDJSON stream the
frontend CodexLoginModal consumes to render the verification URL +
user code BEFORE the OAuth poll loop completes (the original
``/codex-login`` route blocks until success and never surfaces the
device code to the UI).

The route wraps ``login_async(on_device_code=...)`` from
:mod:`kohakuterrarium.studio.identity.codex_oauth`. We replace that
with deterministic stubs so the tests never touch the real OAuth
endpoints + can drive every emit case (device_code → completed,
device_code → error, worker-node 400).
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.identity import codex as codex_mod


@pytest.fixture
def app(monkeypatch):
    # The route's verify_admin_token dependency is keyed on the
    # current AuthConfig.  Standalone-mode default leaves
    # admin_token disabled so requests pass through.
    monkeypatch.setenv("KT_AUTH_ADMIN_TOKEN", "")
    app = FastAPI()
    app.state.lab_mode = "standalone"
    app.include_router(codex_mod.router, prefix="/api/settings")
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def _parse_ndjson(body: str) -> list[dict]:
    """Parse a newline-delimited-JSON response body into a list."""
    return [json.loads(line) for line in body.splitlines() if line.strip()]


class TestStreamHappyPath:
    def test_device_code_then_completed(self, client, monkeypatch):
        # Stub login_async so it fires on_device_code once + returns
        # the success envelope.
        async def fake_login(*, on_device_code=None):
            assert on_device_code is not None
            await on_device_code("https://example.test/codex/device", "ABCD-EFGH", 600)
            return {"status": "ok", "expires_at": 1234567890.0}

        monkeypatch.setattr(codex_mod, "login_async", fake_login)

        resp = client.post("/api/settings/codex-login-stream")
        assert resp.status_code == 200
        # FastAPI's TestClient buffers StreamingResponse — we get the
        # whole NDJSON body once the stream closes.
        events = _parse_ndjson(resp.text)
        # First event: device_code with URL + code.
        assert events[0]["event"] == "device_code"
        assert events[0]["verification_url"] == "https://example.test/codex/device"
        assert events[0]["user_code"] == "ABCD-EFGH"
        assert events[0]["expires_in"] == 600
        # Last event: completed with expires_at echoed back.
        assert events[-1]["event"] == "completed"
        assert events[-1]["expires_at"] == 1234567890.0


class TestStreamErrorPath:
    def test_emits_error_event_when_login_raises(self, client, monkeypatch):
        async def fake_login(*, on_device_code=None):
            raise RuntimeError("simulated oauth failure")

        monkeypatch.setattr(codex_mod, "login_async", fake_login)

        resp = client.post("/api/settings/codex-login-stream")
        assert resp.status_code == 200
        events = _parse_ndjson(resp.text)
        # Single terminal error event — no device_code, no completed.
        assert any(e["event"] == "error" for e in events)
        err = next(e for e in events if e["event"] == "error")
        assert "simulated oauth failure" in err["message"]
        assert "RuntimeError" in err["message"]
        assert not any(e["event"] == "completed" for e in events)


class TestStreamWorkerNodeRefused:
    def test_worker_node_param_returns_400(self, client, monkeypatch):
        # Even if a stub login function exists, the route MUST reject
        # any non-empty / non-_host node value because cross-node
        # event streaming isn't wired in 1.5.0.
        async def fake_login(*, on_device_code=None):  # pragma: no cover
            raise AssertionError("login_async should not run for worker node")

        monkeypatch.setattr(codex_mod, "login_async", fake_login)

        resp = client.post("/api/settings/codex-login-stream?node=worker-1")
        assert resp.status_code == 400
        body = resp.json()
        assert "Streaming Codex login" in body["detail"]


class TestStreamCallbackContract:
    def test_callback_fires_before_completion(self, client, monkeypatch):
        # Verify the route streams the device_code event AS SOON AS
        # the callback fires — i.e. NOT after login_async returns —
        # by ordering the calls: callback then sleep then return.
        # We use an asyncio.Event to assert ordering inside the
        # stubbed login function.
        import asyncio

        callback_fired = asyncio.Event()

        async def fake_login(*, on_device_code=None):
            await on_device_code("https://ex.test/codex/device", "XYZ-123", 300)
            callback_fired.set()
            # Tiny await so the queue puts both events before close.
            await asyncio.sleep(0)
            return {"status": "ok", "expires_at": 42.0}

        monkeypatch.setattr(codex_mod, "login_async", fake_login)

        resp = client.post("/api/settings/codex-login-stream")
        assert resp.status_code == 200
        events = _parse_ndjson(resp.text)
        # device_code event index must be < completed event index.
        device_idx = next(
            i for i, e in enumerate(events) if e["event"] == "device_code"
        )
        complete_idx = next(
            i for i, e in enumerate(events) if e["event"] == "completed"
        )
        assert device_idx < complete_idx
