"""Memory build progress teardown and worker ownership."""

import asyncio
import json
import threading

import pytest
from starlette.websockets import WebSocket, WebSocketDisconnect

from kohakuterrarium.api.ws import memory_build as mod


def socket(frames, send_hook=None):
    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        if message["type"] == "websocket.send":
            frame = json.loads(message["text"])
            if send_hook:
                await send_hook(frame)
            frames.append(frame)

    return WebSocket(
        {"type": "websocket", "headers": [], "query_string": b""}, receive, send
    )


async def test_disconnected_sender_with_full_queue_releases_guard(monkeypatch):
    disconnected = threading.Event()
    finished = threading.Event()

    async def disconnect(frame):
        disconnected.set()
        raise WebSocketDisconnect()

    def build(*args, progress, **kwargs):
        try:
            progress({"percent": 0})
            assert disconnected.wait(3)
            for i in range(200):
                progress({"percent": i})
            return {"stats": {"blocks": 1}}
        finally:
            finished.set()

    monkeypatch.setattr(mod, "run_build_sync", build)
    task = asyncio.create_task(
        mod.ws_memory_build(socket([], disconnect), "full-queue")
    )
    try:
        assert await asyncio.to_thread(finished.wait, 3)
        done, _ = await asyncio.wait({task}, timeout=1)
        assert task in done, "finished worker must not strand the handler"
        await task
        assert "full-queue" not in mod._INFLIGHT_BUILDS
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_cancellation_keeps_guard_until_worker_really_finishes(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = []

    def build(*args, progress, **kwargs):
        calls.append(1)
        started.set()
        try:
            assert release.wait(5)
            progress({"percent": 100})
            return {"stats": {}}
        finally:
            finished.set()

    monkeypatch.setattr(mod, "run_build_sync", build)
    task = asyncio.create_task(mod.ws_memory_build(socket([]), "cancelled-build"))
    try:
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        await asyncio.wait_for(task, 1)
        assert "cancelled-build" in mod._INFLIGHT_BUILDS
        frames = []
        await mod.ws_memory_build(socket(frames), "cancelled-build")
        assert "already running" in frames[-1]["error"]
        assert len(calls) == 1
    finally:
        release.set()
        assert await asyncio.to_thread(finished.wait, 3)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    for _ in range(100):
        if "cancelled-build" not in mod._INFLIGHT_BUILDS:
            break
        await asyncio.sleep(0.01)
    assert "cancelled-build" not in mod._INFLIGHT_BUILDS
    frames = []
    await mod.ws_memory_build(socket(frames), "cancelled-build")
    assert frames[-1]["status"] == "ok"
    assert len(calls) == 2


@pytest.mark.parametrize(
    "error", [None, LookupError("missing session"), RuntimeError("failed build")]
)
async def test_progress_precedes_terminal_and_failure_releases_guard(
    monkeypatch, error
):
    def build(*args, progress, **kwargs):
        progress({"phase": "scan", "percent": 0})
        progress({"phase": "write", "percent": 100})
        if error:
            raise error
        return {"stats": {"blocks": 1}}

    monkeypatch.setattr(mod, "run_build_sync", build)
    frames = []
    await mod.ws_memory_build(socket(frames), "terminal-build")
    assert [frame["percent"] for frame in frames[:-1]] == [0, 100]
    assert frames[-1]["status"] == ("failed" if error else "ok")
    assert "terminal-build" not in mod._INFLIGHT_BUILDS
