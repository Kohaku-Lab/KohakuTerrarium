"""Defensive-path coverage for the api/ws/* endpoints.

Every WS endpoint wraps its error-frame ``send_json`` in a nested
``except Exception: pass`` so a socket that's already gone during the
error-handling itself doesn't re-raise out of the endpoint. These
tests drive a fake WebSocket whose ``send_json`` / ``close`` raise so
those nested handlers actually execute — the documented contract is
"never propagate out of the endpoint", which we assert by the call
returning normally.
"""

from fastapi import WebSocketDisconnect

from kohakuterrarium.api.ws import files as files_mod
from kohakuterrarium.api.ws import io as io_mod
from kohakuterrarium.api.ws import logs as logs_mod
from kohakuterrarium.api.ws import observer as observer_mod
from kohakuterrarium.api.ws import pty as pty_mod


class _FlakyWebSocket:
    """Fake WebSocket whose send/close can be made to raise.

    ``send_fail_after`` — the Nth send_json (0-indexed) and every send
    after it raises. ``close_fails`` — every ``close()`` raises.
    """

    def __init__(self, *, send_fail_after=None, close_fails=False):
        self.accepted = False
        self.closed = False
        self.sent: list[dict] = []
        self._send_fail_after = send_fail_after
        self._close_fails = close_fails
        self._send_count = 0

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        idx = self._send_count
        self._send_count += 1
        if self._send_fail_after is not None and idx >= self._send_fail_after:
            raise RuntimeError("socket gone")
        self.sent.append(payload)

    async def close(self, code=None):
        if self._close_fails:
            raise RuntimeError("close failed")
        self.closed = True


# ── io WS — nested send failures ───────────────────────────────


class TestIoWsDefensive:
    async def test_keyerror_branch_send_failure_swallowed(self, monkeypatch):
        # attach_io raises KeyError; the error-frame send itself fails →
        # the nested handler swallows it and the endpoint still returns.
        async def _attach(ws, service, sid, cid):
            raise KeyError("not found")

        monkeypatch.setattr(io_mod, "attach_io", _attach)
        monkeypatch.setattr(io_mod, "get_service", lambda: object())
        ws = _FlakyWebSocket(send_fail_after=0)
        # Must not raise.
        await io_mod.session_creature_chat(ws, "g", "alice")
        assert ws.closed is True

    async def test_generic_branch_send_failure_swallowed(self, monkeypatch):
        async def _attach(ws, service, sid, cid):
            raise RuntimeError("io broken")

        monkeypatch.setattr(io_mod, "attach_io", _attach)
        monkeypatch.setattr(io_mod, "get_service", lambda: object())
        ws = _FlakyWebSocket(send_fail_after=0)
        await io_mod.session_creature_chat(ws, "g", "alice")
        assert ws.closed is True


# ── logs WS — nested send + close failures ─────────────────────


class TestLogsWsDefensive:
    async def test_error_frame_send_failure_swallowed(self, monkeypatch, tmp_path):
        log = tmp_path / "log.txt"
        log.write_text("x")
        monkeypatch.setattr(logs_mod, "_find_current_process_log", lambda: log)

        async def _boom(path, ws):
            raise RuntimeError("tail crashed")

        monkeypatch.setattr(logs_mod, "_tail_file", _boom)
        # The meta frame (index 0) goes out; the error frame (index 1)
        # fails — nested handler swallows it.
        ws = _FlakyWebSocket(send_fail_after=1)
        await logs_mod.tail_logs(ws)
        # meta frame still made it out.
        assert ws.sent and ws.sent[0]["type"] == "meta"

    async def test_close_failure_swallowed(self, monkeypatch, tmp_path):
        log = tmp_path / "log.txt"
        log.write_text("x")
        monkeypatch.setattr(logs_mod, "_find_current_process_log", lambda: log)

        async def _boom(path, ws):
            raise RuntimeError("tail crashed")

        monkeypatch.setattr(logs_mod, "_tail_file", _boom)
        # The error frame sends fine, but close() raises → the inner
        # close-failure handler logs and swallows.
        ws = _FlakyWebSocket(close_fails=True)
        await logs_mod.tail_logs(ws)
        types = [s["type"] for s in ws.sent]
        assert "error" in types


# ── observer WS — nested send failure ──────────────────────────


class TestObserverWsDefensive:
    async def test_not_found_send_failure_swallowed(self):
        class _Svc:
            async def get_graph(self, sid):
                return None

        ws = _FlakyWebSocket(send_fail_after=0)
        # send of the not-found error frame fails → swallowed; close runs.
        await observer_mod.session_channel_observer(ws, "ghost", _Svc())
        assert ws.closed is True


# ── files WS — no-working-dir + close failure ──────────────────


class _FakeAgentNoCwd:
    """An agent that exposes no working directory anywhere."""

    executor = None


class _FakeCreature:
    def __init__(self, agent):
        self.agent = agent


class TestFilesWsDefensive:
    async def test_no_working_dir_error_frame(self, monkeypatch):
        # find_creature succeeds but the agent has no working dir → the
        # endpoint sends the "no working directory" error and closes.
        monkeypatch.setattr(
            files_mod, "find_creature", lambda e, s, a: _FakeCreature(_FakeAgentNoCwd())
        )
        monkeypatch.setattr(files_mod, "host_engine_or_none", lambda svc: object())
        ws = _FlakyWebSocket()
        await files_mod.watch_files(ws, "alice", service=object())
        assert ws.sent[-1]["type"] == "error"
        assert "working directory" in ws.sent[-1]["text"]
        assert ws.closed is True

    async def test_watch_directory_disconnect_swallowed(self, monkeypatch):
        agent = _FakeAgentNoCwd()
        agent._working_dir = "/tmp"
        monkeypatch.setattr(
            files_mod, "find_creature", lambda e, s, a: _FakeCreature(agent)
        )
        monkeypatch.setattr(files_mod, "host_engine_or_none", lambda svc: object())

        async def _disc(root, ws):
            raise WebSocketDisconnect()

        monkeypatch.setattr(files_mod, "watch_directory", _disc)
        ws = _FlakyWebSocket()
        # WebSocketDisconnect from watch_directory → swallowed, no error
        # frame, endpoint returns cleanly.
        await files_mod.watch_files(ws, "alice", service=object())
        assert not any(s["type"] == "error" for s in ws.sent)

    async def test_watch_crash_close_failure_swallowed(self, monkeypatch):
        agent = _FakeAgentNoCwd()
        agent._working_dir = "/tmp"
        monkeypatch.setattr(
            files_mod, "find_creature", lambda e, s, a: _FakeCreature(agent)
        )
        monkeypatch.setattr(files_mod, "host_engine_or_none", lambda svc: object())

        async def _boom(root, ws):
            raise RuntimeError("watcher bad")

        monkeypatch.setattr(files_mod, "watch_directory", _boom)
        # watch_directory raises; the error frame sends fine but close()
        # raises → the inner handler logs + swallows.
        ws = _FlakyWebSocket(close_fails=True)
        await files_mod.watch_files(ws, "alice", service=object())
        assert any(s["type"] == "error" for s in ws.sent)


# ── pty WS — disconnect + close-failure swallow paths ──────────


class TestPtyWsDefensive:
    async def test_get_creature_info_exception_then_not_found(self, monkeypatch):
        # find_creature fails, get_creature_info ALSO raises → info=None
        # path → "not found" error frame + close.
        def _no_creature(e, s, c):
            raise KeyError("missing")

        monkeypatch.setattr(pty_mod, "find_creature", _no_creature)
        monkeypatch.setattr(pty_mod, "host_engine_or_none", lambda svc: object())

        class _Svc:
            async def get_creature_info(self, cid):
                raise RuntimeError("registry down")

        ws = _FlakyWebSocket()
        await pty_mod.session_pty_ws(ws, "sid", "cid", service=_Svc())
        assert ws.sent[-1]["type"] == "error"
        assert "not found" in ws.sent[-1]["data"]
        assert ws.closed is True

    async def test_local_pty_disconnect_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            pty_mod, "find_creature", lambda e, s, c: _FakeCreature(object())
        )
        monkeypatch.setattr(pty_mod, "host_engine_or_none", lambda svc: object())
        monkeypatch.setattr(pty_mod, "_session_cwd", lambda cr: "/tmp")

        async def _boom(ws, cwd):
            raise WebSocketDisconnect()

        monkeypatch.setattr(pty_mod, "pty_session", _boom)
        ws = _FlakyWebSocket()
        # WebSocketDisconnect from pty_session → swallowed, no error frame.
        await pty_mod.session_pty_ws(ws, "sid", "cid", service=object())
        assert ws.sent == []

    async def test_local_pty_crash_close_failure_swallowed(self, monkeypatch):
        monkeypatch.setattr(
            pty_mod, "find_creature", lambda e, s, c: _FakeCreature(object())
        )
        monkeypatch.setattr(pty_mod, "host_engine_or_none", lambda svc: object())
        monkeypatch.setattr(pty_mod, "_session_cwd", lambda cr: "/tmp")

        async def _boom(ws, cwd):
            raise RuntimeError("pty bad")

        monkeypatch.setattr(pty_mod, "pty_session", _boom)
        # pty_session raises; close() also raises → inner handler swallows.
        ws = _FlakyWebSocket(close_fails=True)
        await pty_mod.session_pty_ws(ws, "sid", "cid", service=object())

    async def test_remote_proxy_disconnect_swallowed(self, monkeypatch):
        def _no_creature(e, s, c):
            raise KeyError("missing")

        monkeypatch.setattr(pty_mod, "find_creature", _no_creature)
        monkeypatch.setattr(pty_mod, "host_engine_or_none", lambda svc: object())

        async def _resolve(svc, cid):
            return "worker-1"

        monkeypatch.setattr(pty_mod, "_resolve_creature_home", _resolve)

        async def _proxy(**kw):
            raise WebSocketDisconnect()

        monkeypatch.setattr(pty_mod, "proxy_ws_to_lab", _proxy)

        class _Svc:
            host = "HOST"
            demux = "DEMUX"

            async def get_creature_info(self, cid):
                return object()

        ws = _FlakyWebSocket()
        # proxy raises WebSocketDisconnect → swallowed, endpoint returns.
        await pty_mod.session_pty_ws(ws, "sid", "cid", service=_Svc())

    async def test_remote_proxy_crash_close_failure_swallowed(self, monkeypatch):
        def _no_creature(e, s, c):
            raise KeyError("missing")

        monkeypatch.setattr(pty_mod, "find_creature", _no_creature)
        monkeypatch.setattr(pty_mod, "host_engine_or_none", lambda svc: object())

        async def _resolve(svc, cid):
            return "worker-1"

        monkeypatch.setattr(pty_mod, "_resolve_creature_home", _resolve)

        async def _proxy(**kw):
            raise RuntimeError("proxy crashed")

        monkeypatch.setattr(pty_mod, "proxy_ws_to_lab", _proxy)

        class _Svc:
            host = "HOST"
            demux = "DEMUX"

            async def get_creature_info(self, cid):
                return object()

        # proxy raises; close() also raises → inner handler swallows.
        ws = _FlakyWebSocket(close_fails=True)
        await pty_mod.session_pty_ws(ws, "sid", "cid", service=_Svc())
