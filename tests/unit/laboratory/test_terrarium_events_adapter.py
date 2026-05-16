"""Unit tests for :class:`TerrariumEventsAdapter` with fake LabNode."""

import asyncio

import pytest

from kohakuterrarium.laboratory._internal.app import AppMessage
from kohakuterrarium.laboratory.adapters.terrarium_events import (
    TerrariumEventsAdapter,
)
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class _FakeLabNode:
    """Mimics LabNode without actually wiring transport."""

    def __init__(self):
        self.app_extensions = {}
        self.notifications = []

    def register_app_extension(self, ns, handler):
        self.app_extensions[ns] = handler

    def unregister_app_extension(self, ns):
        return self.app_extensions.pop(ns, None) is not None

    async def notify(self, *, to_node, namespace, type, body):
        self.notifications.append(
            {"to": to_node, "namespace": namespace, "type": type, "body": body}
        )


def _app_msg(type_, body, sender="ctrl"):
    return AppMessage(
        sender_node=sender,
        namespace="terrarium.events",
        type=type_,
        body=body,
        request_id="r1",
        in_reply_to=None,
    )


# ── basic registration / detach ──────────────────────────────


class TestRegistration:
    async def test_init_registers(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumEventsAdapter(t, node)
            assert "terrarium.events" in node.app_extensions
            adapter.detach()
            assert "terrarium.events" not in node.app_extensions
        finally:
            await t.shutdown()

    async def test_detach_cancels_active_streams(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumEventsAdapter(t, node)

            async def _slow_pump():
                await asyncio.sleep(10)

            task = asyncio.create_task(_slow_pump())
            adapter._active["s1"] = task
            adapter.detach()
            await asyncio.sleep(0.05)
            assert task.cancelled() or task.done()
        finally:
            await t.shutdown()


# ── _dispatch error wrappers ─────────────────────────────────


class TestDispatch:
    async def test_unknown_type_returns_error(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumEventsAdapter(t, node)
            resp = await adapter._dispatch(_app_msg("garbage", {}))
            assert resp["error"]["kind"] == "unknown_type"
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_start_chat_unknown_creature(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumEventsAdapter(t, node)
            resp = await adapter._dispatch(
                _app_msg(
                    "start_chat",
                    {
                        "stream_id": "s1",
                        "creature_id": "ghost",
                        "message": "hi",
                    },
                )
            )
            assert resp["error"]["kind"] == "not_found"
        finally:
            adapter.detach()
            await t.shutdown()


# ── start_chat success ───────────────────────────────────────


class TestStartChat:
    async def test_start_chat_streams_tokens(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice", responses=["hello"]).build()
        )
        node = _FakeLabNode()
        try:
            adapter = TerrariumEventsAdapter(t, node)
            resp = await adapter._dispatch(
                _app_msg(
                    "start_chat",
                    {
                        "stream_id": "s1",
                        "creature_id": "alice",
                        "message": "hi",
                    },
                )
            )
            assert resp["started"] is True
            # Wait for the pump task to complete.
            task = adapter._active.get("s1")
            if task is not None:
                await asyncio.wait_for(task, timeout=2.0)
            # Notifications received include token + eof.
            assert any("token" in n["body"] for n in node.notifications)
            assert any("eof" in n["body"] for n in node.notifications)
        finally:
            adapter.detach()
            await t.shutdown()


# ── cancel_stream ────────────────────────────────────────────


class TestCancelStream:
    async def test_cancel_unknown_returns_cancelled(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumEventsAdapter(t, node)
            resp = await adapter._dispatch(
                _app_msg("cancel_stream", {"stream_id": "ghost"})
            )
            assert resp["cancelled"] is True
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_cancel_running_stream(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumEventsAdapter(t, node)

            async def _slow():
                await asyncio.sleep(10)

            task = asyncio.create_task(_slow())
            adapter._active["s2"] = task
            resp = await adapter._dispatch(
                _app_msg("cancel_stream", {"stream_id": "s2"})
            )
            assert resp["cancelled"] is True
            await asyncio.sleep(0.05)
            assert task.cancelled() or task.done()
        finally:
            adapter.detach()
            await t.shutdown()


# ── _send_frame swallows notify errors ───────────────────────


class TestSendFrame:
    async def test_failed_delivery_logs_silent(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()

        async def _boom(**kw):
            raise RuntimeError("delivery failed")

        node.notify = _boom
        try:
            adapter = TerrariumEventsAdapter(t, node)
            # Should not raise.
            await adapter._send_frame("ctrl", {"stream_id": "s", "token": "x"})
        finally:
            adapter.detach()
            await t.shutdown()


# ── start_subscribe ─────────────────────────────────────────


class TestStartSubscribe:
    async def test_start_subscribe_creates_task(self):
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumEventsAdapter(t, node)
            resp = await adapter._dispatch(
                _app_msg(
                    "start_subscribe",
                    {"stream_id": "sub-1", "filter": None},
                )
            )
            assert resp["started"] is True
            assert "sub-1" in adapter._active
            # Cancel before teardown to avoid hanging.
            adapter._active["sub-1"].cancel()
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_subscribe_pump_streams_engine_events(self):
        # An EngineEvent emitted on the engine must reach the consumer
        # as an ``event`` frame carrying the stream id.
        from kohakuterrarium.terrarium.events import EngineEvent, EventKind

        t = await TestTerrariumBuilder().with_creature("alice").build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumEventsAdapter(t, node)
            await adapter._dispatch(
                _app_msg(
                    "start_subscribe",
                    {"stream_id": "sub-ev", "filter": None},
                )
            )
            # Let the pump task subscribe before we emit.
            await asyncio.sleep(0.05)
            t._emit(
                EngineEvent(
                    kind=EventKind.CREATURE_STARTED,
                    creature_id="alice",
                    graph_id=t.get_creature("alice").graph_id,
                )
            )
            for _ in range(50):
                if any("event" in n["body"] for n in node.notifications):
                    break
                await asyncio.sleep(0.02)
            event_frames = [n for n in node.notifications if "event" in n["body"]]
            assert event_frames
            assert event_frames[0]["body"]["stream_id"] == "sub-ev"
        finally:
            task = adapter._active.get("sub-ev")
            if task is not None:
                task.cancel()
            adapter.detach()
            await t.shutdown()

    async def test_subscribe_pump_emits_eof_on_natural_end(self):
        # When engine.subscribe ends naturally (generator exhausted),
        # the pump emits a final ``eof`` frame so the consumer's
        # iterator terminates cleanly.
        from kohakuterrarium.terrarium.events import EngineEvent, EventKind

        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumEventsAdapter(t, node)

            async def _finite_subscribe(filter_):
                yield EngineEvent(kind=EventKind.CREATURE_STARTED)
                # generator ends here → pump should send eof

            t.subscribe = _finite_subscribe
            await adapter._dispatch(
                _app_msg(
                    "start_subscribe",
                    {"stream_id": "sub-eof", "filter": None},
                )
            )
            task = adapter._active.get("sub-eof")
            await asyncio.wait_for(task, timeout=2.0)
            # Both the event frame and the trailing eof frame arrived.
            assert any("event" in n["body"] for n in node.notifications)
            assert any(n["body"].get("eof") for n in node.notifications)
            # The pump cleared its own bookkeeping in ``finally``.
            assert "sub-eof" not in adapter._active
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_subscribe_pump_error_emits_error_frame(self):
        # If engine.subscribe blows up mid-iteration, the pump catches
        # it and forwards a structured ``error`` frame to the consumer.
        t = await TestTerrariumBuilder().build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumEventsAdapter(t, node)

            async def _boom_subscribe(filter_):
                raise RuntimeError("subscribe backend down")
                yield  # pragma: no cover - makes this an async generator

            t.subscribe = _boom_subscribe
            await adapter._dispatch(
                _app_msg(
                    "start_subscribe",
                    {"stream_id": "sub-err", "filter": None},
                )
            )
            for _ in range(50):
                if any("error" in n["body"] for n in node.notifications):
                    break
                await asyncio.sleep(0.02)
            err_frames = [n for n in node.notifications if "error" in n["body"]]
            assert err_frames
            err = err_frames[0]["body"]["error"]
            assert err["kind"] == "events"
            assert "subscribe backend down" in err["message"]
        finally:
            adapter.detach()
            await t.shutdown()


# ── _pump_chat error + cancel paths ──────────────────────────────


class TestChatPumpFailureModes:
    async def test_chat_pump_exception_emits_error_frame(self):
        # When creature.chat raises, the chat pump surfaces a structured
        # ``error`` frame rather than dropping the stream silently.
        t = await TestTerrariumBuilder().with_creature("alice").build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumEventsAdapter(t, node)
            creature = t.get_creature("alice")

            async def _boom_chat(message):
                raise RuntimeError("llm provider unreachable")
                yield  # pragma: no cover - async generator marker

            creature.chat = _boom_chat
            await adapter._dispatch(
                _app_msg(
                    "start_chat",
                    {
                        "stream_id": "chat-err",
                        "creature_id": "alice",
                        "message": "hi",
                    },
                )
            )
            for _ in range(50):
                if any("error" in n["body"] for n in node.notifications):
                    break
                await asyncio.sleep(0.02)
            err_frames = [n for n in node.notifications if "error" in n["body"]]
            assert err_frames
            err = err_frames[0]["body"]["error"]
            assert err["kind"] == "engine"
            assert "llm provider unreachable" in err["message"]
            # No eof frame is sent on the error path.
            assert not any("eof" in n["body"] for n in node.notifications)
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_chat_pump_cancellation_sends_no_eof(self):
        # A cancelled chat pump (consumer left) must NOT emit eof — the
        # consumer is gone, so a trailing frame would be pointless.
        t = await TestTerrariumBuilder().with_creature("alice").build()
        node = _FakeLabNode()
        try:
            adapter = TerrariumEventsAdapter(t, node)
            creature = t.get_creature("alice")
            entered = asyncio.Event()

            async def _hang_chat(message):
                entered.set()
                await asyncio.Event().wait()
                yield "never"  # pragma: no cover

            creature.chat = _hang_chat
            await adapter._dispatch(
                _app_msg(
                    "start_chat",
                    {
                        "stream_id": "chat-cancel",
                        "creature_id": "alice",
                        "message": "hi",
                    },
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=1.0)
            task = adapter._active["chat-cancel"]
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            # Cancelled mid-pump → no eof frame delivered.
            assert not any("eof" in n["body"] for n in node.notifications)
            # And the pump cleared its own bookkeeping in ``finally``.
            assert "chat-cancel" not in adapter._active
        finally:
            adapter.detach()
            await t.shutdown()
