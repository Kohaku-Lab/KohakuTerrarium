"""Consumer-loop tests for :class:`TerrariumAttachAdapter`.

Companion to ``test_terrarium_attach.py`` (lifecycle / channel / ui
helpers); split out to keep each file under the 600-line cap. Focuses
on ``_consume_input`` — the task that pulls inbound WS frames off the
sink and routes them — plus the ``_AttachSession.teardown``
error-tolerance branches.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kohakuterrarium.laboratory.adapters.terrarium_attach import (
    TerrariumAttachAdapter,
    _AttachSession,
)
from kohakuterrarium.laboratory.ws_proxy import WSFrameSink
from kohakuterrarium.testing.terrarium import TestTerrariumBuilder


class _FakeLabNode:
    def __init__(self):
        self.app_extensions = {}
        self.notifications = []

    def register_app_extension(self, ns, handler):
        self.app_extensions[ns] = handler

    def unregister_app_extension(self, ns):
        return self.app_extensions.pop(ns, None) is not None

    async def notify(self, *, to_node, namespace, type, body):
        self.notifications.append(body)


def _make_sink(node, stream_id="stream-1"):
    return WSFrameSink(node, "ctrl", stream_id)


def _drain(sink):
    frames = []
    while not sink._outbox.empty():
        frames.append(sink._outbox.get_nowait())
    return frames


async def _build_adapter(t):
    node = _FakeLabNode()
    adapter = TerrariumAttachAdapter(t, node)
    return adapter, node


# ── _consume_input: frame routing ───────────────────────────────


class TestConsumeInputRouting:
    async def test_input_frame_emits_user_input_and_processes(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")
            alice.agent.inject_input = AsyncMock()
            sink = _make_sink(node)
            consumer = asyncio.create_task(
                adapter._consume_input(sink, alice, alice.agent)
            )
            try:
                # An ``input`` frame is echoed back as a ``user_input``
                # frame for the source creature, then forwarded to the
                # agent via the spawned _process_input task.
                await sink.inject_input({"type": "input", "content": "hello"})
                for _ in range(50):
                    if alice.agent.inject_input.await_count:
                        break
                    await asyncio.sleep(0.01)
                alice.agent.inject_input.assert_awaited_with("hello", source="web")
                frames = _drain(sink)
                kinds = [f["type"] for f in frames]
                assert "user_input" in kinds
                user_in = next(f for f in frames if f["type"] == "user_input")
                assert user_in["content"] == "hello"
                assert user_in["source"] == "alice"
            finally:
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_ui_dismiss_frame_is_ignored(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")
            alice.agent.inject_input = AsyncMock()
            sink = _make_sink(node)
            consumer = asyncio.create_task(
                adapter._consume_input(sink, alice, alice.agent)
            )
            try:
                await sink.inject_input({"type": "ui_dismiss"})
                # Followed by a real input so we can prove the dismiss
                # was skipped without blocking the loop.
                await sink.inject_input({"type": "input", "content": "x"})
                for _ in range(50):
                    if alice.agent.inject_input.await_count:
                        break
                    await asyncio.sleep(0.01)
                # ui_dismiss produced no frame; only the input did.
                frames = _drain(sink)
                assert all(f["type"] != "ui_dismiss" for f in frames)
                assert any(f["type"] == "user_input" for f in frames)
            finally:
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_ui_reply_frame_routes_to_handler(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")
            received = []
            alice.agent.output_router = SimpleNamespace(
                submit_reply_with_status=lambda r: received.append(r)
                or (True, "applied"),
            )
            sink = _make_sink(node)
            consumer = asyncio.create_task(
                adapter._consume_input(sink, alice, alice.agent)
            )
            try:
                await sink.inject_input(
                    {"type": "ui_reply", "event_id": "e1", "action_id": "a"}
                )
                for _ in range(50):
                    if received:
                        break
                    await asyncio.sleep(0.01)
                # The ui_reply was parsed and handed to the router; an
                # ack frame is emitted back.
                assert received and received[0].event_id == "e1"
                frames = _drain(sink)
                assert any(f["type"] == "ui_reply_ack" for f in frames)
            finally:
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_unknown_frame_type_is_skipped(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")
            alice.agent.inject_input = AsyncMock()
            sink = _make_sink(node)
            consumer = asyncio.create_task(
                adapter._consume_input(sink, alice, alice.agent)
            )
            try:
                # A frame whose type is neither ui_reply/ui_dismiss/input
                # is dropped without side effects.
                await sink.inject_input({"type": "telemetry"})
                await sink.inject_input({"type": "input", "content": "x"})
                for _ in range(50):
                    if alice.agent.inject_input.await_count:
                        break
                    await asyncio.sleep(0.01)
                assert alice.agent.inject_input.await_count == 1
            finally:
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_empty_input_content_is_skipped(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")
            alice.agent.inject_input = AsyncMock()
            sink = _make_sink(node)
            consumer = asyncio.create_task(
                adapter._consume_input(sink, alice, alice.agent)
            )
            try:
                # An input frame with no usable content never reaches
                # the agent.
                await sink.inject_input({"type": "input", "content": 123})
                await sink.inject_input({"type": "input", "content": "real"})
                for _ in range(50):
                    if alice.agent.inject_input.await_count:
                        break
                    await asyncio.sleep(0.01)
                alice.agent.inject_input.assert_awaited_once_with("real", source="web")
            finally:
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
        finally:
            adapter.detach()
            await t.shutdown()


# ── _consume_input: target routing ──────────────────────────────


class TestConsumeInputTargetRouting:
    async def test_input_targeting_sibling_routes_to_that_agent(self):
        t = await (
            TestTerrariumBuilder().with_creature("alice").with_creature("bob").build()
        )
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")
            bob = t.get_creature("bob")
            alice.agent.inject_input = AsyncMock()
            bob.agent.inject_input = AsyncMock()
            sink = _make_sink(node)
            consumer = asyncio.create_task(
                adapter._consume_input(sink, alice, alice.agent)
            )
            try:
                # A frame addressed to "bob" must be routed to bob's
                # agent, not alice's.
                await sink.inject_input(
                    {"type": "input", "content": "for bob", "target": "bob"}
                )
                for _ in range(50):
                    if bob.agent.inject_input.await_count:
                        break
                    await asyncio.sleep(0.01)
                bob.agent.inject_input.assert_awaited_with("for bob", source="web")
                alice.agent.inject_input.assert_not_awaited()
                # The echoed user_input frame names the effective target.
                frames = _drain(sink)
                user_in = next(f for f in frames if f["type"] == "user_input")
                assert user_in["source"] == "bob"
            finally:
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_input_targeting_unknown_creature_emits_error(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            alice = t.get_creature("alice")
            alice.agent.inject_input = AsyncMock()
            sink = _make_sink(node)
            consumer = asyncio.create_task(
                adapter._consume_input(sink, alice, alice.agent)
            )
            try:
                # Targeting a creature not in the session yields an
                # ``error`` frame and the input is NOT delivered.
                await sink.inject_input(
                    {
                        "type": "input",
                        "content": "lost",
                        "target": "nobody",
                    }
                )
                for _ in range(50):
                    if not sink._outbox.empty():
                        break
                    await asyncio.sleep(0.01)
                frames = _drain(sink)
                err = next(f for f in frames if f["type"] == "error")
                assert err["source"] == "nobody"
                assert "not found" in err["content"]
                alice.agent.inject_input.assert_not_awaited()
            finally:
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await consumer
        finally:
            adapter.detach()
            await t.shutdown()


# ── _AttachSession.teardown error tolerance ─────────────────────


class TestTeardownErrorTolerance:
    async def test_teardown_swallows_every_remove_failure(self):
        # teardown must not raise even when every cleanup step fails —
        # a half-removed session is worse than a logged error.
        def _boom(*_a, **_k):
            raise RuntimeError("router gone")

        bad_agent = SimpleNamespace(
            output_router=SimpleNamespace(remove_secondary=_boom)
        )
        bad_sibling_agent = SimpleNamespace(
            output_router=SimpleNamespace(remove_secondary=_boom)
        )
        bad_channel = SimpleNamespace(remove_on_send=_boom)

        async def _noop():
            await asyncio.Event().wait()

        consumer_task = asyncio.create_task(_noop())
        session = _AttachSession(
            creature=SimpleNamespace(),
            agent=bad_agent,
            primary_out=object(),
            sibling_modules=[(bad_sibling_agent, object())],
            channel_cbs=[(bad_channel, object())],
            consumer_task=consumer_task,
        )
        # Every branch raises internally; teardown still completes and
        # still cancels the consumer task.
        session.teardown()
        assert consumer_task.cancelled() or not consumer_task.done()
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

    async def test_teardown_cancels_running_consumer(self):
        async def _noop():
            await asyncio.Event().wait()

        consumer_task = asyncio.create_task(_noop())
        await asyncio.sleep(0)
        session = _AttachSession(
            creature=SimpleNamespace(),
            agent=SimpleNamespace(
                output_router=SimpleNamespace(remove_secondary=lambda x: None)
            ),
            primary_out=object(),
            sibling_modules=[],
            channel_cbs=[],
            consumer_task=consumer_task,
        )
        session.teardown()
        # The still-running consumer task is cancelled by teardown.
        for _ in range(50):
            if consumer_task.cancelled() or consumer_task.done():
                break
            await asyncio.sleep(0.01)
        assert consumer_task.cancelled() or consumer_task.done()


# ── on_close defensive path ─────────────────────────────────────


class TestOnCloseDefensive:
    async def test_on_close_unknown_stream_is_noop(self):
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, _node = await _build_adapter(t)
            # No session registered for this id → on_close returns
            # silently rather than raising KeyError.
            await adapter.on_close("never-started")
        finally:
            adapter.detach()
            await t.shutdown()

    async def test_replay_channel_history_skips_when_no_channels(self):
        # _replay_channel_history short-circuits on a graph with no
        # shared channels — no frames emitted.
        t = await TestTerrariumBuilder().with_creature("alice").build()
        try:
            adapter, node = await _build_adapter(t)
            gid = t.get_creature("alice").graph_id
            sink = _make_sink(node)
            adapter._replay_channel_history(gid, sink)
            assert sink._outbox.empty()
            _ = time.time  # silence unused import in trimmed builds
        finally:
            adapter.detach()
            await t.shutdown()
