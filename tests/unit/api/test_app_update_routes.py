"""HTTP surface for ``/api/app/*`` (06b)."""

import asyncio
import json
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from kohakuterrarium.launcher.update_runner import UpdateResult

from kohakuterrarium.api.routes import app_update as _r


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    app = FastAPI()
    app.state.lab_mode = "standalone"
    app.include_router(_r.router, prefix="/api/app")
    app.include_router(_r.ws_router)
    return TestClient(app)


class TestSettingsRoundTrip:
    def test_get_returns_defaults_on_fresh_install(self, client):
        resp = client.get("/api/app/settings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["feed"]["kind"] == "github_releases"
        assert body["channel"] == "stable"
        assert body["update"]["mode"] == "notify-on-launch"

    def test_put_persists(self, client):
        resp = client.put(
            "/api/app/settings",
            json={
                "feed": {"kind": "github_releases", "repo": "x/y"},
                "channel": "beta",
                "pinned_version": "1.5.0",
                "update": {
                    "mode": "manual",
                    "check-cache-hours": 12,
                    "keep-versions": 5,
                },
            },
        )
        assert resp.status_code == 200
        echoed = resp.json()
        assert echoed["channel"] == "beta"
        assert echoed["pinned_version"] == "1.5.0"
        assert echoed["feed"]["repo"] == "x/y"

        body = client.get("/api/app/settings").json()
        assert body["update"]["mode"] == "manual"
        assert body["update"]["check-cache-hours"] == 12

    def test_invalid_payload_coerces_silently(self, client):
        # The new endpoint runs coercion rather than HTTP 400 — invalid
        # fields snap back to defaults so the UI never gets stuck on a
        # rejected save.
        resp = client.put(
            "/api/app/settings",
            json={"channel": "experimental", "update": {"mode": "weekly"}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["channel"] == "stable"
        assert body["update"]["mode"] == "notify-on-launch"


class TestState:
    def test_state_includes_install_metadata(self, client):
        resp = client.get("/api/app/state")
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "active",
            "installed",
            "settings",
            "launcher_install",
            "platform",
            "py_abi",
        ):
            assert key in body


class TestRejectionPaths:
    def test_lab_client_blocks_all_routes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
        app = FastAPI()
        app.state.lab_mode = "lab-client"
        app.include_router(_r.router, prefix="/api/app")
        c = TestClient(app)
        for path in ("/api/app/settings", "/api/app/state"):
            assert c.get(path).status_code == 404

    def test_update_refuses_outside_launcher(self, client):
        # The default test environment has no active pointer — the
        # update / rollback routes must refuse with 409 so the UI
        # surfaces the "use kt self-update from terminal" hint.
        assert client.post("/api/app/update").status_code == 409
        assert client.post("/api/app/rollback").status_code == 409


def progress_socket(frames, hook=None):
    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        if message["type"] == "websocket.send":
            frame = json.loads(message["text"])
            if hook:
                await hook(frame)
            frames.append(frame)

    return WebSocket({"type": "websocket", "headers": []}, receive, send)


@pytest.mark.asyncio
@pytest.mark.parametrize("ok", [True, False])
async def test_stream_delivers_worker_progress_before_completion(monkeypatch, ok):
    forwarded = threading.Event()
    frames = []

    async def delivered(frame):
        if frame.get("phase") == "download":
            forwarded.set()

    def update(push):
        push("download", 10, "first")
        assert forwarded.wait(3), "progress must arrive while worker is running"
        push("extract", 80, "second")
        return UpdateResult(
            ok=ok, version="1.2.3", error=None if ok else "smoke failed"
        )

    monkeypatch.setattr(_r, "run_update", update)
    ws = progress_socket(frames, delivered)
    await ws.accept()
    await asyncio.wait_for(_r._stream_update(ws), timeout=5)
    assert [f["message"] for f in frames[:-1]] == ["first", "second"]
    assert frames[-1]["status"] == ("ok" if ok else "failed")


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_kind", ["success", "failure", "disconnect", "cancel"])
async def test_stream_quiet_period_leaves_no_pending_getters(monkeypatch, exit_kind):
    started = threading.Event()
    release = threading.Event()
    frames = []
    before = asyncio.all_tasks()

    def update(push):
        started.set()
        assert release.wait(5)
        push("extract", 90, "late progress")
        if exit_kind == "failure":
            raise RuntimeError("updater failed")
        return UpdateResult(ok=True, version="1.2.3")

    async def delivered(frame):
        if exit_kind == "disconnect":
            raise WebSocketDisconnect()

    monkeypatch.setattr(_r, "run_update", update)
    ws = progress_socket(frames, delivered)
    await ws.accept()
    task = asyncio.create_task(_r._stream_update(ws))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        # Cross two old polling intervals with no incoming progress.
        await asyncio.sleep(1.1)
        if exit_kind == "cancel":
            task.cancel()
        release.set()
        results = await asyncio.wait_for(
            asyncio.gather(task, return_exceptions=True), 3
        )
        if exit_kind == "failure":
            assert isinstance(results[0], RuntimeError)
        elif exit_kind == "disconnect":
            assert isinstance(results[0], WebSocketDisconnect)
        elif exit_kind == "cancel":
            assert isinstance(results[0], asyncio.CancelledError)
        else:
            assert [f["message"] for f in frames[:-1]] == ["late progress"]
        await asyncio.sleep(0)
        pending = asyncio.all_tasks() - before
        assert not pending, f"stream leaked tasks: {pending}"
    finally:
        release.set()
        for pending_task in asyncio.all_tasks() - before:
            pending_task.cancel()
        await asyncio.gather(*(asyncio.all_tasks() - before), return_exceptions=True)
