"""Unit tests for WebSocket route handlers in :mod:`kohakuterrarium.api.ws`.

Each WS handler is a thin shell over a studio.attach.* helper or a
service.subscribe iterator. We test the shell's error / dispatch paths
with a fake WebSocket + service.
"""

from fastapi import WebSocketDisconnect

from kohakuterrarium.api.ws import io as io_mod
from kohakuterrarium.api.ws import logs as logs_mod
from kohakuterrarium.api.ws import observer as observer_mod
from kohakuterrarium.api.ws import trace as trace_mod
from kohakuterrarium.terrarium.events import EngineEvent, EventKind


class _FakeWebSocket:
    def __init__(self, *, raise_on_send=None):
        self.accepted = False
        self.closed = False
        self.sent: list[dict] = []
        self._raise_on_send = raise_on_send

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        if self._raise_on_send is not None:
            raise self._raise_on_send
        self.sent.append(payload)

    async def close(self):
        self.closed = True


# ── trace WS ───────────────────────────────────────────────────


class TestTraceWs:
    async def test_delegates_to_studio_helper(self, monkeypatch):
        called = []

        async def fake_run(ws, name, agent):
            called.append((name, agent))

        monkeypatch.setattr(trace_mod, "run_trace_attach", fake_run)
        ws = _FakeWebSocket()
        await trace_mod.session_events_stream(ws, "sess-1", agent="alice")
        assert called == [("sess-1", "alice")]


# ── observer WS ───────────────────────────────────────────────


class TestObserverWs:
    async def test_session_not_found(self, monkeypatch):
        class _Svc:
            async def get_graph(self, sid):
                return None

        ws = _FakeWebSocket()
        await observer_mod.session_channel_observer(ws, "ghost", _Svc())
        assert ws.accepted is True
        assert ws.sent[0]["type"] == "error"
        assert ws.closed is True

    async def test_subscribes_and_streams(self, monkeypatch):
        events = [
            EngineEvent(
                kind=EventKind.CHANNEL_MESSAGE,
                graph_id="g",
                channel="chat",
                payload={
                    "sender": "alice",
                    "content": "hi",
                    "message_id": "m1",
                },
            ),
        ]

        class _Svc:
            async def get_graph(self, sid):
                return object()  # exists

            def subscribe(self, flt):
                async def gen():
                    for e in events:
                        yield e

                return gen()

        ws = _FakeWebSocket()
        await observer_mod.session_channel_observer(ws, "g", _Svc())
        assert ws.sent[0]["type"] == "channel_message"
        assert ws.sent[0]["sender"] == "alice"

    async def test_swallows_websocket_disconnect(self, monkeypatch):
        class _Svc:
            async def get_graph(self, sid):
                return object()

            def subscribe(self, flt):
                async def gen():
                    raise WebSocketDisconnect()
                    yield  # pragma: no cover

                return gen()

        ws = _FakeWebSocket()
        # Should not raise.
        await observer_mod.session_channel_observer(ws, "g", _Svc())


# ── logs WS ────────────────────────────────────────────────────


class TestLogsWs:
    async def test_no_log_file(self, monkeypatch):
        monkeypatch.setattr(logs_mod, "_find_current_process_log", lambda: None)
        ws = _FakeWebSocket()
        await logs_mod.tail_logs(ws)
        assert ws.sent[0]["type"] == "error"
        assert ws.closed is True

    async def test_tail_runs(self, monkeypatch, tmp_path):
        log_path = tmp_path / "log.txt"
        log_path.write_text("hi\n")

        monkeypatch.setattr(logs_mod, "_find_current_process_log", lambda: log_path)

        async def fake_tail(path, ws):
            await ws.send_json({"type": "line", "text": "tailed"})

        monkeypatch.setattr(logs_mod, "_tail_file", fake_tail)
        ws = _FakeWebSocket()
        await logs_mod.tail_logs(ws)
        types = [s["type"] for s in ws.sent]
        assert "meta" in types
        assert "line" in types

    async def test_swallows_disconnect(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            logs_mod, "_find_current_process_log", lambda: tmp_path / "x"
        )

        async def boom(path, ws):
            raise WebSocketDisconnect()

        monkeypatch.setattr(logs_mod, "_tail_file", boom)
        ws = _FakeWebSocket()
        # Should not raise.
        await logs_mod.tail_logs(ws)


# ── io WS ──────────────────────────────────────────────────────


class TestIoWs:
    async def test_creature_missing(self, monkeypatch):
        async def fake_attach(ws, service, sid, cid):
            raise KeyError("not found")

        # Replace dep + helper.
        monkeypatch.setattr(io_mod, "attach_io", fake_attach)
        monkeypatch.setattr(io_mod, "get_service", lambda: object())
        ws = _FakeWebSocket()
        await io_mod.session_creature_chat(ws, "g", "alice")
        assert ws.sent and ws.sent[0]["type"] == "error"
        assert ws.closed is True

    async def test_websocket_disconnect_swallowed(self, monkeypatch):
        async def boom(ws, service, sid, cid):
            raise WebSocketDisconnect()

        monkeypatch.setattr(io_mod, "attach_io", boom)
        monkeypatch.setattr(io_mod, "get_service", lambda: object())
        ws = _FakeWebSocket()
        await io_mod.session_creature_chat(ws, "g", "alice")
        # Disconnect → no error frame, no explicit close from this branch.

    async def test_generic_exception_logs_and_closes(self, monkeypatch):
        async def boom(ws, service, sid, cid):
            raise RuntimeError("io broken")

        monkeypatch.setattr(io_mod, "attach_io", boom)
        monkeypatch.setattr(io_mod, "get_service", lambda: object())
        ws = _FakeWebSocket()
        await io_mod.session_creature_chat(ws, "g", "alice")
        assert ws.sent and ws.sent[0]["type"] == "error"
        assert ws.closed is True
