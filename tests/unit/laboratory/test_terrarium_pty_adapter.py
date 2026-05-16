"""Unit tests for :class:`TerrariumPtyAdapter`.

The adapter is a :class:`WSProxyAdapter` subclass that bridges a
worker-local PTY shell to the controller's WebSocket.  The real
``pty_session`` spawns a subprocess, so these tests substitute it with
a controllable async stand-in (monkeypatched on the adapter module)
and assert the adapter's own behaviour: creature resolution, cwd
discovery, task spawn / cancel lifecycle, the ``_FakeWebSocket``
bridge, and error-frame emission when the PTY session blows up.
"""

import asyncio
import json

import pytest

from kohakuterrarium.laboratory import ws_proxy
from kohakuterrarium.laboratory.adapters import terrarium_pty
from kohakuterrarium.laboratory.adapters.terrarium_pty import (
    TerrariumPtyAdapter,
    _FakeWebSocket,
)
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class _FakeLabNode:
    def __init__(self):
        self.handlers = {}
        self.unregistered = []
        self.frames = []

    def register_app_extension(self, ns, handler):
        self.handlers[ns] = handler

    def unregister_app_extension(self, ns):
        self.unregistered.append(ns)
        return self.handlers.pop(ns, None) is not None

    async def notify(self, *, to_node, namespace, type, body):
        self.frames.append(body)


def _make_sink(node, stream_id="pty-1"):
    return ws_proxy.WSFrameSink(node, "ctrl", stream_id)


# ── _FakeWebSocket bridge ────────────────────────────────────────


class TestFakeWebSocket:
    async def test_send_json_forwards_to_sink_outbox(self):
        node = _FakeLabNode()
        sink = _make_sink(node)
        fake = _FakeWebSocket(sink)
        await fake.send_json({"type": "stdout", "data": "ls\n"})
        # The frame lands on the sink's outbox verbatim.
        assert sink._outbox.get_nowait() == {"type": "stdout", "data": "ls\n"}

    async def test_receive_text_serialises_inbound_frame_to_json_string(self):
        node = _FakeLabNode()
        sink = _make_sink(node)
        fake = _FakeWebSocket(sink)
        # ``pty_session`` reads JSON *strings*; inject_input puts a dict
        # on the inbox and receive_text must re-serialise it.
        await sink.inject_input({"type": "stdin", "data": "echo hi\n"})
        raw = await fake.receive_text()
        assert isinstance(raw, str)
        assert json.loads(raw) == {"type": "stdin", "data": "echo hi\n"}

    async def test_close_is_a_noop(self):
        # Sink lifecycle is the proxy base class's job; the fake-WS
        # close must not touch it.
        node = _FakeLabNode()
        sink = _make_sink(node)
        fake = _FakeWebSocket(sink)
        await fake.close()  # no exception, no side effect


# ── adapter lifecycle ────────────────────────────────────────────


class TestPtyAdapterLifecycle:
    async def test_registers_and_detaches(self):
        t = await TestTerrariumBuilder().build()
        try:
            node = _FakeLabNode()
            adapter = TerrariumPtyAdapter(t, node)
            assert "terrarium.pty" in node.handlers
            adapter.detach()
            assert "terrarium.pty" in node.unregistered
        finally:
            await t.shutdown()


# ── on_start ─────────────────────────────────────────────────────


class TestOnStart:
    async def test_resolves_creature_cwd_and_returns_ready_setup(self, monkeypatch):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            node = _FakeLabNode()
            adapter = TerrariumPtyAdapter(t, node)
            started = asyncio.Event()

            async def _fake_pty(ws, cwd):
                # Long-running stand-in: stays alive until cancelled.
                started.set()
                await asyncio.Event().wait()

            monkeypatch.setattr(terrarium_pty, "pty_session", _fake_pty)
            sink = _make_sink(node)
            resp = await adapter.on_start({"creature_id": "alice"}, sink)
            # on_start returns immediately with a ``ready`` setup frame
            # carrying the resolved cwd, and spawns the PTY task.
            assert resp["setup"]["type"] == "ready"
            assert "cwd" in resp["setup"]
            assert sink.stream_id in adapter._sessions
            await asyncio.wait_for(started.wait(), timeout=1.0)
            await adapter.on_close(sink.stream_id)
        finally:
            await t.shutdown()

    async def test_unknown_creature_raises_keyerror(self, monkeypatch):
        t = await TestTerrariumBuilder().build()
        try:
            node = _FakeLabNode()
            adapter = TerrariumPtyAdapter(t, node)
            monkeypatch.setattr(
                terrarium_pty, "pty_session", lambda ws, cwd: asyncio.sleep(0)
            )
            sink = _make_sink(node)
            # No such creature → engine.get_creature raises KeyError;
            # the proxy base class maps that to a not_found error.
            with pytest.raises(KeyError):
                await adapter.on_start({"creature_id": "ghost"}, sink)
        finally:
            await t.shutdown()

    async def test_start_dispatch_maps_unknown_creature_to_not_found(self, monkeypatch):
        from kohakuterrarium.laboratory._internal.app import AppMessage

        t = await TestTerrariumBuilder().build()
        try:
            node = _FakeLabNode()
            adapter = TerrariumPtyAdapter(t, node)
            monkeypatch.setattr(
                terrarium_pty, "pty_session", lambda ws, cwd: asyncio.sleep(0)
            )
            msg = AppMessage(
                namespace="terrarium.pty",
                type="start",
                body={"stream_id": "s1", "creature_id": "ghost"},
                sender_node="ctrl",
                request_id=None,
                in_reply_to=None,
            )
            out = await adapter._dispatch(msg)
            assert out["error"]["kind"] == "not_found"
            # The failed start must not leak a sink.
            assert "s1" not in adapter._sinks
        finally:
            await t.shutdown()


# ── on_close ─────────────────────────────────────────────────────


class TestOnClose:
    async def test_cancels_running_pty_task(self, monkeypatch):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            node = _FakeLabNode()
            adapter = TerrariumPtyAdapter(t, node)
            cancelled = asyncio.Event()

            async def _fake_pty(ws, cwd):
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

            monkeypatch.setattr(terrarium_pty, "pty_session", _fake_pty)
            sink = _make_sink(node)
            await adapter.on_start({"creature_id": "alice"}, sink)
            task = adapter._sessions[sink.stream_id]["task"]
            # Let the PTY task actually enter pty_session before cancel.
            await asyncio.sleep(0.02)
            await adapter.on_close(sink.stream_id)
            # on_close cancels the still-running PTY task.
            await asyncio.wait_for(cancelled.wait(), timeout=1.0)
            assert task.cancelled() or task.done()
        finally:
            await t.shutdown()

    async def test_on_close_unknown_stream_is_noop(self):
        t = await TestTerrariumBuilder().build()
        try:
            node = _FakeLabNode()
            adapter = TerrariumPtyAdapter(t, node)
            # No session for this id → on_close returns silently.
            await adapter.on_close("never-started")
        finally:
            await t.shutdown()

    async def test_on_close_after_task_finished_is_noop(self, monkeypatch):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            node = _FakeLabNode()
            adapter = TerrariumPtyAdapter(t, node)

            async def _quick_pty(ws, cwd):
                return None

            monkeypatch.setattr(terrarium_pty, "pty_session", _quick_pty)
            sink = _make_sink(node)
            await adapter.on_start({"creature_id": "alice"}, sink)
            # Let the PTY task finish on its own.
            await asyncio.sleep(0.02)
            # on_close on an already-done task must not raise.
            await adapter.on_close(sink.stream_id)
        finally:
            await t.shutdown()


# ── _run_pty error handling ──────────────────────────────────────


class TestRunPty:
    async def test_pty_session_exception_emits_error_frame(self, monkeypatch):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            node = _FakeLabNode()
            adapter = TerrariumPtyAdapter(t, node)

            async def _boom_pty(ws, cwd):
                raise RuntimeError("pty spawn failed")

            monkeypatch.setattr(terrarium_pty, "pty_session", _boom_pty)
            sink = _make_sink(node)
            sink.start()
            try:
                fake_ws = _FakeWebSocket(sink)
                await adapter._run_pty(fake_ws, "/tmp", sink)
                # The crash is caught and surfaced as an ``error`` frame
                # on the sink rather than killing the worker silently.
                for _ in range(50):
                    if not sink._outbox.empty():
                        break
                    await asyncio.sleep(0.01)
                frame = sink._outbox.get_nowait()
                assert frame["type"] == "error"
                assert "pty spawn failed" in frame["data"]
            finally:
                await sink.close()
        finally:
            await t.shutdown()

    async def test_run_pty_cancellation_propagates(self, monkeypatch):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            node = _FakeLabNode()
            adapter = TerrariumPtyAdapter(t, node)

            async def _hang_pty(ws, cwd):
                await asyncio.Event().wait()

            monkeypatch.setattr(terrarium_pty, "pty_session", _hang_pty)
            sink = _make_sink(node)
            fake_ws = _FakeWebSocket(sink)
            task = asyncio.create_task(adapter._run_pty(fake_ws, "/tmp", sink))
            await asyncio.sleep(0.02)
            task.cancel()
            # _run_pty re-raises CancelledError — it does NOT swallow it
            # into an error frame.
            with pytest.raises(asyncio.CancelledError):
                await task
            assert sink._outbox.empty()
        finally:
            await t.shutdown()
