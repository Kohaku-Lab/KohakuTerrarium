"""WS — /ws/daemon/logs backlog + level filter."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.ws import daemon_logs as dl_mod


@pytest.fixture
def app(tmp_path, monkeypatch):
    log_path = tmp_path / "web.log"
    log_path.write_text(
        "\n".join(
            [
                "[10:00:00] [boot] [INFO] starting",
                "[10:00:01] [worker] [DEBUG] internal",
                "[10:00:02] [worker] [WARNING] something",
                "[10:00:03] [worker] [ERROR] oops",
                "[10:00:04] [worker] [INFO] still alive",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    # Point the module at our tmp log file via the query-param override.
    monkeypatch.setattr(dl_mod, "_DEFAULT_LOG_PATH", log_path)
    app = FastAPI()
    app.state.lab_mode = "standalone"
    app.include_router(dl_mod.router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestBacklog:
    def test_backlog_no_follow_returns_terminal(self, client):
        with client.websocket_connect("/ws/daemon/logs?follow=false&lines=10") as ws:
            frames = []
            while True:
                msg = ws.receive_text()
                frame = json.loads(msg)
                frames.append(frame)
                if "status" in frame:
                    break
        lines = [f["line"] for f in frames if "line" in f]
        # INFO is the default min level — DEBUG is filtered out.
        assert any("starting" in line for line in lines)
        assert any("ERROR" in line for line in lines)
        assert not any("DEBUG" in line for line in lines)
        assert frames[-1]["status"] == "closed"

    def test_level_error_only(self, client):
        with client.websocket_connect("/ws/daemon/logs?follow=false&level=ERROR") as ws:
            frames = []
            while True:
                msg = ws.receive_text()
                frame = json.loads(msg)
                frames.append(frame)
                if "status" in frame:
                    break
        lines = [f["line"] for f in frames if "line" in f]
        assert all("ERROR" in line for line in lines)
        assert len(lines) == 1

    def test_path_query_is_ignored(self, client, tmp_path):
        """SECURITY regression: an attacker-controlled ``?path=...``
        query string MUST NOT redirect the tail to another file.

        Previously the handler honoured ``q.get("path")`` and would
        stream the contents of any file the daemon could read. The
        fix pinned the path to ``_DEFAULT_LOG_PATH``. This test
        creates a "secret" file outside the configured log path and
        asserts its contents never appear in the stream.
        """
        secret = tmp_path / "should-not-leak.txt"
        secret.write_text(
            "SECRET-MARKER-THIS-MUST-NOT-APPEAR-IN-WS-STREAM\n",
            encoding="utf-8",
        )
        with client.websocket_connect(
            f"/ws/daemon/logs?follow=false&path={secret.as_posix()}"
        ) as ws:
            frames = []
            while True:
                msg = ws.receive_text()
                frame = json.loads(msg)
                frames.append(frame)
                if "status" in frame:
                    break
        combined = "\n".join(f.get("line", "") for f in frames if "line" in f)
        assert "SECRET-MARKER" not in combined
