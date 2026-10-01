"""WS — /ws/daemon/logs backlog + level filter."""

import asyncio
import json
import threading

import pytest
from fastapi import FastAPI, WebSocketDisconnect
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


@pytest.mark.parametrize("ending", ["\n", "\r\n", ""])
def test_backlog_reads_only_suffix(tmp_path, monkeypatch, ending):
    path = tmp_path / "large.log"
    payload = ("old line\n" * 100000 + "中文\nlast" + ending).encode()
    path.write_bytes(payload)
    reads = []
    original_open = open

    class TrackedFile:
        def __enter__(self):
            self.file = original_open(path, "rb")
            return self

        def __exit__(self, *args):
            self.file.close()

        def seek(self, *args):
            return self.file.seek(*args)

        def read(self, size=-1):
            reads.append(size)
            return self.file.read(size)

    monkeypatch.setattr(dl_mod, "open", lambda *args: TrackedFile(), raising=False)
    assert dl_mod._read_backlog(path, 2) == payload.decode().splitlines()[-2:]
    assert reads and all(0 < size <= 65536 for size in reads)
    assert sum(reads) < len(payload) // 4


def test_zero_backlog(client):
    with client.websocket_connect("/ws/daemon/logs?follow=false&lines=0") as ws:
        assert json.loads(ws.receive_text()) == {
            "status": "closed",
            "reason": "follow=false",
        }


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"one",
        b"a\r\nb\r\n",
        b"a\rb\rc",
        "中文\u2028last".encode(),
        b"bad\xff\nlast",
    ],
)
def test_backlog_text_compatibility(tmp_path, monkeypatch, payload):
    path = tmp_path / "web.log"
    path.write_bytes(payload)
    monkeypatch.setattr(dl_mod, "_READ_CHUNK_BYTES", 4)
    for lines in (1, 2, 100):
        assert (
            dl_mod._read_backlog(path, lines)
            == payload.decode("utf-8", errors="replace").splitlines()[-lines:]
        )
    path.unlink()
    assert dl_mod._read_backlog(path, 10) == []


def test_backlog_disk_io_is_off_event_loop(client, monkeypatch):
    disk_threads, send_threads = [], []
    read, level = dl_mod._read_backlog, dl_mod._line_level

    def tracked_read(*args):
        disk_threads.append(threading.get_ident())
        return read(*args)

    def tracked_level(line):
        send_threads.append(threading.get_ident())
        return level(line)

    monkeypatch.setattr(dl_mod, "_read_backlog", tracked_read)
    monkeypatch.setattr(dl_mod, "_line_level", tracked_level)
    with client.websocket_connect("/ws/daemon/logs?follow=false") as ws:
        while "status" not in json.loads(ws.receive_text()):
            pass
    assert disk_threads and send_threads
    assert set(disk_threads).isdisjoint(send_threads)


@pytest.mark.asyncio
async def test_tail_utf8_partial_lines_truncation_and_worker(tmp_path, monkeypatch):
    path = tmp_path / "web.log"
    path.write_bytes(b"")
    monkeypatch.setattr(dl_mod, "_READ_CHUNK_BYTES", 4)
    read = dl_mod._read_chunk
    disk_threads = []
    stage = 0

    def tracked_read(path, pos):
        nonlocal stage
        disk_threads.append(threading.get_ident())
        # Real file writes at polling boundaries exercise chunked UTF-8,
        # a completed partial line, and discard of a truncated partial line.
        if stage == 0:
            path.write_bytes("中文\npart".encode())
            stage = 1
        elif stage == 1 and pos == path.stat().st_size:
            with path.open("ab") as f:
                f.write(b"ial\nold-partial")
            stage = 2
        elif stage == 2 and pos == path.stat().st_size:
            path.write_bytes(b"new\n")
            stage = 3
        return read(path, pos)

    monkeypatch.setattr(dl_mod, "_read_chunk", tracked_read)
    lines = []

    async def send(line):
        lines.append(line)
        if len(lines) == 3:
            raise WebSocketDisconnect()

    await asyncio.wait_for(dl_mod._tail(path, send), timeout=5)
    assert lines == ["中文", "partial", "new"]
    assert disk_threads and threading.get_ident() not in disk_threads
