"""Coverage push for api/ws/{files,logs,pty,runtime_graph}.

Mostly exercises endpoint dispatch via TestClient: connects, drives
one round-trip, disconnects to break the long-running pump loops.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kohakuterrarium.api.deps import get_engine, get_service
from kohakuterrarium.api.ws import (
    files as ws_files,
    logs as ws_logs,
    pty as ws_pty,
    runtime_graph as ws_rg,
)
from kohakuterrarium.terrarium.service import LocalTerrariumService
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


def _app(routers, *, engine=None, service=None):
    app = FastAPI()
    app.dependency_overrides[get_engine] = lambda: engine or SimpleNamespace()
    app.dependency_overrides[get_service] = lambda: service or SimpleNamespace()
    for r in routers:
        app.include_router(r)
    return app


# ── ws/files — full dispatch matrix ─────────────────────────


class TestWsFilesDispatch:
    async def test_remote_creature_rejected(self):
        # Service has get_creature_info that returns a non-None info →
        # the endpoint sends the "remote" error frame.
        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)

        async def _info(cid):
            return SimpleNamespace(graph_id="g-remote")

        svc.get_creature_info = _info
        app = _app([ws_files.router], service=svc)
        try:
            with TestClient(app) as client:
                with client.websocket_connect("/ws/files/remote-cid") as ws:
                    msg = ws.receive_json()
                    assert msg["type"] == "error"
                    assert "remote" in msg["text"]
        finally:
            await t.shutdown()

    async def test_get_creature_info_exception_routes_to_not_found(self):
        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)

        async def _boom(cid):
            raise RuntimeError("bad")

        svc.get_creature_info = _boom
        app = _app([ws_files.router], service=svc)
        try:
            with TestClient(app) as client:
                with client.websocket_connect("/ws/files/anything") as ws:
                    msg = ws.receive_json()
                    assert msg["type"] == "error"
                    assert "not found" in msg["text"]
        finally:
            await t.shutdown()

    async def test_watch_directory_exception_logs(self, monkeypatch):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            agent = t.get_creature("alice").agent
            agent._working_dir = "/tmp"

            async def _boom(*a, **kw):
                raise RuntimeError("watcher bad")

            monkeypatch.setattr(ws_files, "watch_directory", _boom)
            app = _app([ws_files.router], service=svc)
            with TestClient(app) as client:
                with client.websocket_connect("/ws/files/alice") as ws:
                    msg = ws.receive_json()
                    assert msg["type"] == "error"
                    assert "watcher bad" in msg["text"]
        finally:
            await t.shutdown()


# ── ws/logs error paths ─────────────────────────────────────


class TestWsLogsErrors:
    def test_no_log_file(self, monkeypatch):
        monkeypatch.setattr(ws_logs, "_find_current_process_log", lambda: None)
        app = _app([ws_logs.router])
        with TestClient(app) as client:
            with client.websocket_connect("/ws/logs") as ws:
                msg = ws.receive_json()
                assert msg["type"] == "error"

    def test_tail_file_exception(self, monkeypatch, tmp_path):
        from fastapi import WebSocketDisconnect

        log_file = tmp_path / "log.txt"
        log_file.write_text("x")
        monkeypatch.setattr(ws_logs, "_find_current_process_log", lambda: log_file)

        async def _boom(path, ws):
            raise RuntimeError("tail crashed")

        monkeypatch.setattr(ws_logs, "_tail_file", _boom)
        app = _app([ws_logs.router])
        with TestClient(app) as client:
            with client.websocket_connect("/ws/logs") as ws:
                # The meta frame goes out first, carrying the resolved
                # log path.
                m1 = ws.receive_json()
                assert m1["type"] == "meta"
                assert m1["path"] == str(log_file)
                # _tail_file raised → an error frame with the exception
                # text, then the socket closes.
                m2 = ws.receive_json()
                assert m2["type"] == "error"
                assert m2["text"] == "tail crashed"
                with pytest.raises(WebSocketDisconnect):
                    ws.receive_json()


# ── ws/pty error paths ──────────────────────────────────────


class TestWsPtyRemote:
    async def test_remote_creature_routed(self, monkeypatch):
        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)
        # Has get_creature_info returning a non-None info → goes through
        # the remote path; but service has no `_resolve_home` so _resolve
        # returns "_host" → endpoint sends "home unresolved" error.

        async def _info(cid):
            return SimpleNamespace(creature_id=cid)

        svc.get_creature_info = _info
        try:
            app = _app([ws_pty.router], service=svc)
            with TestClient(app) as client:
                with client.websocket_connect(
                    "/ws/sessions/sid/creatures/cid/pty"
                ) as ws:
                    msg = ws.receive_json()
                    assert msg["type"] == "error"
                    assert "unresolved" in msg["data"]
        finally:
            await t.shutdown()

    async def test_remote_creature_proxy_exception(self, monkeypatch):
        from fastapi import WebSocketDisconnect

        t = await TestTerrariumBuilder().build()
        svc = LocalTerrariumService(t)
        proxy_calls = []

        async def _info(cid):
            return SimpleNamespace(creature_id=cid)

        async def _resolve(cid):
            return "worker-1"

        async def _proxy(**kw):
            proxy_calls.append(kw)
            raise RuntimeError("proxy crashed")

        svc.get_creature_info = _info
        svc._resolve_home = _resolve
        svc.host = "HOST"
        svc.demux = "DEMUX"
        monkeypatch.setattr(ws_pty, "proxy_ws_to_lab", _proxy)
        try:
            app = _app([ws_pty.router], service=svc)
            with TestClient(app) as client:
                with client.websocket_connect(
                    "/ws/sessions/sid/creatures/cid/pty"
                ) as ws:
                    # Proxy raised → endpoint closes the socket with no
                    # further frame; the next receive sees the close.
                    with pytest.raises(WebSocketDisconnect):
                        ws.receive_json()
            # The proxy was actually dispatched to the resolved worker
            # node with the creature id in the body.
            assert len(proxy_calls) == 1
            assert proxy_calls[0]["target_node"] == "worker-1"
            assert proxy_calls[0]["body"] == {"creature_id": "cid"}
            assert proxy_calls[0]["namespace"] == "terrarium.pty"
        finally:
            await t.shutdown()

    async def test_local_pty_session_exception(self, monkeypatch):
        from fastapi import WebSocketDisconnect

        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        pty_calls = []

        async def _boom(websocket, cwd):
            pty_calls.append(cwd)
            raise RuntimeError("pty bad")

        monkeypatch.setattr(ws_pty, "pty_session", _boom)
        try:
            app = _app([ws_pty.router], service=svc)
            with TestClient(app) as client:
                with client.websocket_connect(
                    "/ws/sessions/_/creatures/alice/pty"
                ) as ws:
                    # pty_session raised → endpoint closes the socket.
                    with pytest.raises(WebSocketDisconnect):
                        ws.receive_json()
            # The local PTY path was taken (pty_session invoked once).
            assert len(pty_calls) == 1
        finally:
            await t.shutdown()


# ── ws/runtime_graph deeper paths ───────────────────────────


class TestWsRuntimeGraphDeeper:
    async def test_subscribe_and_disconnect_cleanup(self):
        # Exercise the subscribed/snapshot path with a channel registered,
        # so sync_channel_observers walks at least one channel.
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_channel("chat").build()
        )
        svc = LocalTerrariumService(t)
        try:
            app = _app([ws_rg.router], service=svc)
            with TestClient(app) as client:
                with client.websocket_connect("/ws/runtime/graph") as ws:
                    sub = ws.receive_json()
                    assert sub["type"] == "subscribed"
                    snap = ws.receive_json()
                    assert snap["type"] == "snapshot"
                    # Channel was registered → sync_channel_observers walked it.
                    assert "graphs" in snap["snapshot"]
        finally:
            await t.shutdown()

    async def test_endpoint_handles_exception(self, monkeypatch):
        from fastapi import WebSocketDisconnect

        t = await TestTerrariumBuilder().with_creature("alice").build()
        svc = LocalTerrariumService(t)
        try:
            # Patch the service-routed snapshot to raise so the
            # exception handler fires.  ``runtime_graph_stream`` reads
            # ``get_service()`` directly (not via Depends), so patch the
            # module-level lookup to hand back our instrumented svc.
            async def _boom():
                raise RuntimeError("snapshot crashed")

            monkeypatch.setattr(svc, "runtime_graph_snapshot", _boom)
            monkeypatch.setattr(ws_rg, "get_service", lambda: svc)
            app = _app([ws_rg.router], service=svc)
            with TestClient(app) as client:
                with client.websocket_connect("/ws/runtime/graph") as ws:
                    # The snapshot is built BEFORE the subscribed frame
                    # is sent, so a snapshot failure means the very
                    # first frame is the error frame (no subscribed).
                    err = ws.receive_json()
                    assert err["type"] == "error"
                    assert err["message"] == "snapshot crashed"
                    with pytest.raises(WebSocketDisconnect):
                        ws.receive_json()
        finally:
            await t.shutdown()
