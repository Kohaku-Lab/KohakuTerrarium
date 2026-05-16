"""Unit tests for the attach log + trace WS endpoints (with fake WS)."""

import asyncio

from fastapi import WebSocketDisconnect

from kohakuterrarium.studio.attach import log as log_mod
from kohakuterrarium.studio.attach import trace as trace_mod


class _FakeWebSocket:
    """Minimal WebSocket stand-in supporting send_json/accept/close."""

    def __init__(self, raise_disconnect_after=None):
        self.sent = []
        self.accepted = False
        self.closed = False
        self.close_code = None
        self._raise_after = raise_disconnect_after
        self._count = 0

    async def accept(self):
        self.accepted = True

    async def send_json(self, data):
        self._count += 1
        if self._raise_after is not None and self._count > self._raise_after:
            raise WebSocketDisconnect()
        self.sent.append(data)

    async def close(self, code=None):
        self.closed = True
        self.close_code = code


# ── log.run_log_attach ──────────────────────────────────────


class TestRunLogAttach:
    async def test_no_log_file_closes(self, monkeypatch):
        monkeypatch.setattr(log_mod, "_find_current_process_log", lambda: None)
        ws = _FakeWebSocket()
        await log_mod.run_log_attach(ws)
        assert ws.accepted
        assert ws.closed
        assert any(s["type"] == "error" for s in ws.sent)

    async def test_tail_file_runs(self, monkeypatch, tmp_path):
        log_file = tmp_path / "log.txt"
        log_file.write_text("[12:34:56] [mod] [INFO] hello\n")
        monkeypatch.setattr(log_mod, "_find_current_process_log", lambda: log_file)

        async def _instant_tail(path, ws):
            return  # Skip the infinite poll loop.

        monkeypatch.setattr(log_mod, "_tail_file", _instant_tail)
        ws = _FakeWebSocket()
        await log_mod.run_log_attach(ws)
        # meta frame sent.
        assert any(s["type"] == "meta" for s in ws.sent)

    async def test_disconnect_swallowed(self, monkeypatch, tmp_path):
        log_file = tmp_path / "log.txt"
        log_file.write_text("x")
        monkeypatch.setattr(log_mod, "_find_current_process_log", lambda: log_file)

        async def _disconnect(path, ws):
            raise WebSocketDisconnect()

        monkeypatch.setattr(log_mod, "_tail_file", _disconnect)
        ws = _FakeWebSocket()
        await log_mod.run_log_attach(ws)
        # No re-raise — handler returned cleanly.

    async def test_exception_logs_and_closes(self, monkeypatch, tmp_path):
        log_file = tmp_path / "log.txt"
        log_file.write_text("x")
        monkeypatch.setattr(log_mod, "_find_current_process_log", lambda: log_file)

        async def _boom(path, ws):
            raise RuntimeError("bad")

        monkeypatch.setattr(log_mod, "_tail_file", _boom)
        ws = _FakeWebSocket()
        await log_mod.run_log_attach(ws)
        # Error frame sent + closed.
        assert any(s["type"] == "error" for s in ws.sent[1:])


# ── log._tail_file ──────────────────────────────────────────


class TestTailFile:
    async def test_missing_file_sends_error(self, tmp_path):
        ws = _FakeWebSocket()
        # Patch wait time so this test runs fast.
        await log_mod._tail_file(tmp_path / "ghost", ws)
        assert any("not found" in s.get("text", "") for s in ws.sent)

    async def test_reads_seed_lines_then_polls(self, tmp_path):
        log_file = tmp_path / "live.log"
        log_file.write_text(
            "[12:00:01] [mod] [INFO] seed1\n" "[12:00:02] [mod] [INFO] seed2\n"
        )
        ws = _FakeWebSocket(raise_disconnect_after=1)
        try:
            await log_mod._tail_file(log_file, ws)
        except WebSocketDisconnect:
            pass
        # First lines flushed before disconnect.
        assert ws.sent
        assert ws.sent[0]["type"] == "line"


# ── trace.run_trace_attach ──────────────────────────────────


class TestRunTraceAttach:
    async def test_not_live_closes_1011(self, monkeypatch):
        monkeypatch.setattr(
            trace_mod,
            "_find_live_store",
            lambda name, stores=None: None,
        )
        ws = _FakeWebSocket()
        await trace_mod.run_trace_attach(ws, "ghost", agent=None)
        assert ws.closed
        assert ws.close_code == 1011
        assert any(s["type"] == "error" for s in ws.sent)

    async def test_disconnect_during_pump(self, monkeypatch):
        # Set up a fake store that subscribes successfully.
        subscribed = []

        class _FakeStore:
            def subscribe(self, cb):
                subscribed.append(cb)

            def unsubscribe(self, cb):
                pass

        monkeypatch.setattr(
            trace_mod,
            "_find_live_store",
            lambda name, stores=None: _FakeStore(),
        )

        ws = _FakeWebSocket(raise_disconnect_after=0)
        await trace_mod.run_trace_attach(ws, "live", agent=None)
        # WebSocketDisconnect was swallowed.

    async def test_other_exception_logs_and_closes(self, monkeypatch):
        class _FakeStore:
            def subscribe(self, cb):
                pass

            def unsubscribe(self, cb):
                pass

        monkeypatch.setattr(
            trace_mod,
            "_find_live_store",
            lambda name, stores=None: _FakeStore(),
        )

        class _BadWS(_FakeWebSocket):
            async def send_json(self, data):
                # Raise on first send_json call (the "subscribed" frame).
                self.sent.append(data)
                raise RuntimeError("bad send")

        ws = _BadWS()
        await trace_mod.run_trace_attach(ws, "live", agent=None)
        # Closed despite exception.

    async def test_subscribed_event_is_pumped_to_the_ws(self, monkeypatch):
        # Happy path: a store event posted after subscription must flow
        # through the per-connection queue and out the websocket.
        captured_cb = {}

        class _FakeStore:
            def subscribe(self, cb):
                captured_cb["cb"] = cb

            def unsubscribe(self, cb):
                pass

        monkeypatch.setattr(
            trace_mod,
            "_find_live_store",
            lambda name, stores=None: _FakeStore(),
        )

        # WS that closes after receiving the "subscribed" hello + one
        # real event frame, so the pump loop runs exactly once.
        class _TwoFrameWS(_FakeWebSocket):
            async def send_json(self, data):
                self.sent.append(data)
                if len(self.sent) >= 2:
                    raise WebSocketDisconnect()

        ws = _TwoFrameWS()

        async def _runner():
            await trace_mod.run_trace_attach(ws, "live", agent=None)

        task = asyncio.create_task(_runner())
        # Let the handler reach the pump loop + register the callback.
        await asyncio.sleep(0.02)
        captured_cb["cb"]("alice:e7", {"payload": "real-event"})
        await asyncio.wait_for(task, timeout=2.0)
        # Frame 0 = "subscribed" hello, frame 1 = the pumped event.
        assert ws.sent[0]["type"] == "subscribed"
        assert ws.sent[1] == {
            "type": "event",
            "key": "alice:e7",
            "event": {"payload": "real-event"},
        }

    async def test_callback_after_loop_closed_is_swallowed(self, monkeypatch):
        # If the event loop has torn down when the store callback fires
        # (WS dropped between callback registration and dispatch),
        # call_soon_threadsafe raises RuntimeError — the callback must
        # swallow it rather than crash the tool worker thread.
        captured_cb = {}

        class _FakeStore:
            def subscribe(self, cb):
                captured_cb["cb"] = cb

            def unsubscribe(self, cb):
                pass

        monkeypatch.setattr(
            trace_mod,
            "_find_live_store",
            lambda name, stores=None: _FakeStore(),
        )
        ws = _FakeWebSocket(raise_disconnect_after=0)
        await trace_mod.run_trace_attach(ws, "live", agent=None)
        cb = captured_cb["cb"]

        # The handler captured the live loop; simulate the closed-loop
        # race by making its call_soon_threadsafe raise.
        loop = asyncio.get_running_loop()
        orig = loop.call_soon_threadsafe

        def _boom(*a, **k):
            raise RuntimeError("Event loop is closed")

        loop.call_soon_threadsafe = _boom
        try:
            cb("alice:e1", {"v": 1})  # must be swallowed — no raise
        finally:
            loop.call_soon_threadsafe = orig

    async def test_close_failure_after_exception_is_swallowed(self, monkeypatch):
        # When the pump raises a non-disconnect error, the handler tries
        # to send an error frame + close. If close() ALSO raises, that
        # secondary failure must be swallowed too.
        class _FakeStore:
            def subscribe(self, cb):
                pass

            def unsubscribe(self, cb):
                pass

        monkeypatch.setattr(
            trace_mod,
            "_find_live_store",
            lambda name, stores=None: _FakeStore(),
        )

        class _BadWS(_FakeWebSocket):
            async def send_json(self, data):
                self.sent.append(data)
                raise RuntimeError("send exploded")

            async def close(self, code=None):
                raise RuntimeError("close exploded too")

        ws = _BadWS()
        # No exception propagates despite both send_json AND close failing.
        await trace_mod.run_trace_attach(ws, "live", agent=None)

    async def test_agent_filter_isolates_events(self, monkeypatch):
        captured_cb = {}

        class _FakeStore:
            def subscribe(self, cb):
                captured_cb["cb"] = cb

            def unsubscribe(self, cb):
                pass

        monkeypatch.setattr(
            trace_mod,
            "_find_live_store",
            lambda name, stores=None: _FakeStore(),
        )
        # Capture exactly which payloads reach the queue.
        enqueued = []
        monkeypatch.setattr(
            trace_mod,
            "_enqueue_or_drop",
            lambda q, payload: enqueued.append(payload),
        )

        ws = _FakeWebSocket(raise_disconnect_after=0)
        await trace_mod.run_trace_attach(ws, "live", agent="alice")
        # Now invoke the captured callback to exercise filter branches.
        cb = captured_cb["cb"]
        cb("alice:e0", {"v": 1})  # passes filter
        cb("bob:e0", {"v": 2})  # filtered out — wrong agent
        cb("alice:attached:bob:e1", {"v": 3})  # passes via attached ns
        # _on_event schedules the enqueue via call_soon_threadsafe; give
        # the loop a turn to drain those scheduled callbacks.
        await asyncio.sleep(0)
        # Only alice's own + alice-attached events were enqueued.
        keys = [p["key"] for p in enqueued]
        assert keys == ["alice:e0", "alice:attached:bob:e1"]
        assert [p["event"] for p in enqueued] == [{"v": 1}, {"v": 3}]
