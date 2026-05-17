"""HTTP + WS surface for ``/api/sessions/{name}/memory/{status,build}``.

The HTTP routes are thin wrappers around
:mod:`studio.sessions.memory_build`; the WS handler streams progress
frames + a terminal status frame.  These tests exercise the route
shape, error paths, and the WS frame contract using a real on-disk
``SessionStore`` so the close-hook + state-vault paths run for real.
"""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.routes.persistence import memory_index as memory_index_mod
from kohakuterrarium.api.ws import memory_build as ws_memory_build
from kohakuterrarium.session.store import SessionStore


def _real_session(tmp_path: Path, *, with_events: bool = True) -> Path:
    """Build a real session file with one agent + a small user_input event.

    A single text event is enough for ``index_events`` to emit at
    least one FTS / vector block, which is what the status probe
    asserts on.
    """
    path = tmp_path / "alice.kohakutr"
    store = SessionStore(str(path))
    try:
        store.init_meta("alice", "agent", "/p", "/w", ["alice"])
        if with_events:
            store.append_event(
                "alice",
                "user_input",
                {
                    "content": "hello world from the unit test",
                    "round": 1,
                },
                turn_index=1,
            )
    finally:
        store.close()
    return path


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    # Resolve session names against ``tmp_path`` so the routes find the
    # files this test writes.
    from kohakuterrarium.studio.persistence import store as store_mod

    monkeypatch.setattr(store_mod, "_SESSION_DIR", tmp_path, raising=True)

    app = FastAPI()
    app.state.lab_mode = "standalone"
    app.include_router(memory_index_mod.router, prefix="/api/sessions")
    app.include_router(ws_memory_build.router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestMemoryStatus:
    def test_unknown_session_404(self, client):
        resp = client.get("/api/sessions/nosuch/memory/status")
        assert resp.status_code == 404

    def test_empty_session_reports_unindexed(self, client, tmp_path):
        _real_session(tmp_path, with_events=False)
        resp = client.get("/api/sessions/alice/memory/status")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["indexed"] is False
        assert body["vec_blocks"] == 0
        assert isinstance(body["agents"], list)

    def test_built_session_reports_indexed(self, client, tmp_path):
        # Build then probe.
        _real_session(tmp_path)
        resp = client.post(
            "/api/sessions/alice/memory/build",
            json={"embedder": "auto"},
        )
        assert resp.status_code == 200, resp.text
        # The HTTP POST only returns the WS URL — do the actual run
        # via the sync helper so we don't need a WS client here.
        memory_index_mod.run_build_sync(
            "alice",
            embedder="auto",
            model=None,
            dimensions=None,
            force=False,
            progress=None,
        )
        status = client.get("/api/sessions/alice/memory/status").json()
        assert status["indexed"] is True
        assert status["vec_blocks"] >= 1


class TestBuildAck:
    def test_post_returns_ws_url(self, client, tmp_path):
        _real_session(tmp_path)
        resp = client.post(
            "/api/sessions/alice/memory/build",
            json={"embedder": "auto"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["websocket"].endswith("/ws/sessions/alice/memory/build")
        assert body["request"]["embedder"] == "auto"
        assert body["request"]["force"] is False

    def test_post_unknown_session_404(self, client):
        resp = client.post(
            "/api/sessions/nosuch/memory/build",
            json={"embedder": "auto"},
        )
        assert resp.status_code == 404


class TestBuildWebSocket:
    def test_terminal_ok_frame(self, client, tmp_path):
        _real_session(tmp_path)
        with client.websocket_connect(
            "/ws/sessions/alice/memory/build?embedder=auto"
        ) as ws:
            frames = []
            while True:
                msg = ws.receive_text()
                frame = json.loads(msg)
                frames.append(frame)
                if "status" in frame:
                    break
        # Last frame is the terminal.
        terminal = frames[-1]
        assert terminal["status"] == "ok"
        assert terminal["error"] is None
        assert isinstance(terminal["stats"], dict)
        # At least one progress frame before the terminal.
        progress_frames = [f for f in frames[:-1] if "phase" in f]
        assert progress_frames, "expected at least one progress frame"
        # Each progress frame has the documented keys.
        for f in progress_frames:
            assert "phase" in f
            assert "percent" in f
            assert "blocks_total" in f

    def test_unknown_session_emits_failed_terminal(self, client):
        with client.websocket_connect("/ws/sessions/nosuch/memory/build") as ws:
            frames = []
            while True:
                msg = ws.receive_text()
                frame = json.loads(msg)
                frames.append(frame)
                if "status" in frame:
                    break
        terminal = frames[-1]
        assert terminal["status"] == "failed"
        assert "not found" in (terminal["error"] or "").lower()

    def test_empty_session_does_not_crash(self, client, tmp_path):
        """No-agents session walks the early-return branch — stats has
        the same keys as the normal path so CLI / frontend formatting
        doesn't KeyError. Regression for the audit-found bug in
        ``cli/memory.py`` where the formatter did ``stats['dimensions']``."""
        _real_session(tmp_path, with_events=False)
        # Build via the sync helper since no-agents means no progress
        # frames are interesting — the assertion is on the return shape.
        result = memory_index_mod.run_build_sync(
            "alice",
            embedder="auto",
            model=None,
            dimensions=None,
            force=False,
            progress=None,
        )
        # No agents → empty per-agent dict + zero blocks, but ``stats``
        # always exposes the same four keys so callers can format
        # uniformly.
        for key in ("fts_blocks", "vec_blocks", "has_vectors", "dimensions"):
            assert key in result["stats"], (key, result["stats"])


class TestConcurrentBuildGuard:
    def test_second_build_for_same_session_rejected(self, client, tmp_path):
        """The WS module owns a per-session in-flight set. A second
        build while the first is mid-stream gets a terminal ``failed``
        frame instead of racing through ``SessionMemory`` write paths.
        """
        import threading

        _real_session(tmp_path)
        first_connected = threading.Event()
        first_terminal = threading.Event()
        first_frames: list = []

        def run_first():
            with client.websocket_connect(
                "/ws/sessions/alice/memory/build?embedder=auto"
            ) as ws:
                first_connected.set()
                while True:
                    frame = json.loads(ws.receive_text())
                    first_frames.append(frame)
                    if "status" in frame:
                        first_terminal.set()
                        return

        t = threading.Thread(target=run_first)
        t.start()
        try:
            first_connected.wait(timeout=5.0)
            # First build is in flight. Open a second WS on the same
            # session and assert it receives a "failed" terminal frame
            # without waiting on the first.
            second_frames: list = []
            with client.websocket_connect(
                "/ws/sessions/alice/memory/build?embedder=auto"
            ) as ws:
                while True:
                    frame = json.loads(ws.receive_text())
                    second_frames.append(frame)
                    if "status" in frame:
                        break
            assert second_frames[-1]["status"] == "failed"
            assert "already running" in (second_frames[-1]["error"] or "").lower()
        finally:
            first_terminal.wait(timeout=30.0)
            t.join(timeout=2.0)
