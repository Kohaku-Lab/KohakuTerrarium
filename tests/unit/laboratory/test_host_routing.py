"""Routing-focused unit tests for :mod:`kohakuterrarium.laboratory._internal.host`.

Exercises SEND routing (creature ref / channel listener / direct),
BROADCAST fan-out, CONTROL unsubscribe / unregister_creature, and the
custom-control-handler exception path.
"""

import asyncio

import pytest

from kohakuterrarium.laboratory.config import ClientConfig, HostConfig
from kohakuterrarium.laboratory._internal.client import ClientConnector
from kohakuterrarium.laboratory._internal.control import (
    _build_control,
    build_register_creature,
    build_subscribe,
    build_unregister_creature,
    build_unsubscribe,
)
from kohakuterrarium.laboratory._internal.envelope import (
    Envelope,
    EnvelopeKind,
)
from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.protocol import HOST_NODE_ID
from kohakuterrarium.laboratory._internal.transport_inproc import (
    InProcTransport,
)


@pytest.fixture(autouse=True)
def _reset_inproc():
    InProcTransport._clear_registry()
    yield
    InProcTransport._clear_registry()


async def _start_pair(port=1, second_name=None):
    cfg_h = HostConfig(
        bind_host="rt",
        bind_port=port,
        token="t",
        heartbeat_timeout_seconds=5.0,
    )
    host = HostEngine(cfg_h, InProcTransport())
    await host.start()
    cfg_c = ClientConfig(
        client_name="w1",
        host_url=f"rt:{port}",
        token="t",
        reconnect_initial_delay_seconds=0.1,
        heartbeat_interval_seconds=10.0,
    )
    c1 = ClientConnector(cfg_c, InProcTransport())
    await c1.start()
    if second_name:
        cfg_c2 = ClientConfig(
            client_name=second_name,
            host_url=f"rt:{port}",
            token="t",
            reconnect_initial_delay_seconds=0.1,
            heartbeat_interval_seconds=10.0,
        )
        c2 = ClientConnector(cfg_c2, InProcTransport())
        await c2.start()
        return host, c1, c2
    return host, c1


# ── CONTROL unsubscribe + unregister_creature ────────────────


class TestControlUnsubUnregister:
    async def test_unsubscribe_after_subscribe(self):
        host, c1 = await _start_pair(port=1)
        try:
            await c1.send(
                build_subscribe(from_node="w1", to_node="_host", channel="chat")
            )
            await asyncio.sleep(0.05)
            assert "w1" in host.addressing.listeners("chat")
            await c1.send(
                build_unsubscribe(from_node="w1", to_node="_host", channel="chat")
            )
            await asyncio.sleep(0.05)
            assert "w1" not in host.addressing.listeners("chat")
        finally:
            await c1.stop()
            await host.stop()

    async def test_unregister_creature(self):
        host, c1 = await _start_pair(port=2)
        try:
            await c1.send(
                build_register_creature(
                    from_node="w1",
                    to_node="_host",
                    ref="creature://alice",
                )
            )
            await asyncio.sleep(0.05)
            assert host.addressing.resolve_creature("creature://alice") == "w1"
            await c1.send(
                build_unregister_creature(
                    from_node="w1",
                    to_node="_host",
                    ref="creature://alice",
                )
            )
            await asyncio.sleep(0.05)
            assert host.addressing.resolve_creature("creature://alice") is None
        finally:
            await c1.stop()
            await host.stop()

    async def test_custom_handler_exception_swallowed(self):
        host, c1 = await _start_pair(port=3)
        try:

            async def boom_handler(sender, env, fields):
                raise RuntimeError("bad")

            host.register_control_handler("op", boom_handler)
            await c1.send(
                _build_control(
                    from_node="w1",
                    to_node="_host",
                    stream_id=0,
                    seq=0,
                    body={"control": "op"},
                )
            )
            await asyncio.sleep(0.05)
            # Host still up.
            assert "w1" in host.alive_clients()
        finally:
            await c1.stop()
            await host.stop()

    async def test_subscribe_non_string_channel_ignored(self):
        host, c1 = await _start_pair(port=4)
        try:
            await c1.send(
                _build_control(
                    from_node="w1",
                    to_node="_host",
                    stream_id=0,
                    seq=0,
                    body={"control": "subscribe", "channel": 123},
                )
            )
            await asyncio.sleep(0.05)
            # No listener recorded since channel wasn't a string.
            assert "w1" not in host.addressing.listeners("123")
        finally:
            await c1.stop()
            await host.stop()


# ── SEND routing via creature ref + channel listener ─────────


class TestSendRouting:
    async def test_send_to_creature_ref_routes(self):
        host, c1 = await _start_pair(port=5)
        try:
            # Register a creature ref.
            await c1.send(
                build_register_creature(
                    from_node="w1",
                    to_node="_host",
                    ref="creature://alice",
                )
            )
            await asyncio.sleep(0.05)
            # Have w1 register an extension so we can verify routing.
            received = asyncio.Event()

            async def handler(msg):
                received.set()
                return None

            c1.register_app_extension("api", handler)
            # Host issues a request to creature://alice — the resolver
            # should rewrite this to "w1" and route there.
            await host.notify(
                to_node="w1",
                namespace="api",
                type="hi",
            )
            await asyncio.wait_for(received.wait(), timeout=2.0)
        finally:
            await c1.stop()
            await host.stop()

    async def test_send_to_unknown_node_dropped(self):
        host, c1 = await _start_pair(port=6)
        try:
            # Send to a non-existent node — should be silently dropped,
            # not echoed back to the sender, and not crash the host.
            echoed: list[Envelope] = []
            c1.on_envelope(
                lambda e: echoed.append(e) if e.kind is EnvelopeKind.SEND else None
            )
            env = Envelope(
                from_node="w1",
                to_node="ghost-node",
                kind=EnvelopeKind.SEND,
                stream_id=0,
                seq=0,
                payload=b"to-ghost",
            )
            await c1.send(env)
            await asyncio.sleep(0.1)
            assert echoed == []
            assert "w1" in host.alive_clients()
        finally:
            await c1.stop()
            await host.stop()

    async def test_send_to_host_dropped(self):
        host, c1 = await _start_pair(port=7)
        try:
            # The host id is not a routable peer — a SEND to it is dropped
            # silently rather than echoed or crashing the router.
            echoed: list[Envelope] = []
            c1.on_envelope(
                lambda e: echoed.append(e) if e.kind is EnvelopeKind.SEND else None
            )
            env = Envelope(
                from_node="w1",
                to_node=HOST_NODE_ID,
                kind=EnvelopeKind.SEND,
                stream_id=0,
                seq=0,
                payload=b"to-host",
            )
            await c1.send(env)
            await asyncio.sleep(0.1)
            assert echoed == []
            assert "w1" in host.alive_clients()
        finally:
            await c1.stop()
            await host.stop()


# ── BROADCAST fan-out ────────────────────────────────────────


class TestBroadcastFanout:
    async def test_topic_broadcast_to_listeners(self):
        host, c1, c2 = await _start_pair(port=10, second_name="w2")
        try:
            c2_received: list[Envelope] = []
            c2.on_envelope(
                lambda e: (
                    c2_received.append(e) if e.kind is EnvelopeKind.BROADCAST else None
                )
            )
            # Both subscribe to "news".
            await c1.send(
                build_subscribe(from_node="w1", to_node="_host", channel="news")
            )
            await c2.send(
                build_subscribe(from_node="w2", to_node="_host", channel="news")
            )
            await asyncio.sleep(0.05)
            assert host.addressing.listeners("news") == {"w1", "w2"}
            # Broadcast envelope from w1.
            env = Envelope(
                from_node="w1",
                to_node="topic://news",
                kind=EnvelopeKind.BROADCAST,
                stream_id=0,
                seq=0,
                payload=b"breaking",
            )
            await c1.send(env)
            await asyncio.sleep(0.1)
            # The fan-out delivered the broadcast to the other subscriber w2.
            assert [e.payload for e in c2_received] == [b"breaking"]
        finally:
            await c2.stop()
            await c1.stop()
            await host.stop()


# ── _handle_app: malformed envelope ─────────────────────────


class TestHandleAppMalformed:
    async def test_response_with_no_pending_request_silent(self):
        host, c1 = await _start_pair(port=20)
        try:
            from kohakuterrarium.laboratory._internal.app import (
                build_app_envelope,
            )

            # Send a "response" for a request that doesn't exist.
            env = build_app_envelope(
                from_node="w1",
                to_node="_host",
                namespace="api",
                type="x",
                body={"ok": True},
                in_reply_to="never-asked",
            )
            await c1.send(env)
            await asyncio.sleep(0.05)
            # Host didn't crash.
            assert "w1" in host.alive_clients()
        finally:
            await c1.stop()
            await host.stop()


# ── disconnect_client during pending request aborts it ─────


class TestPendingRequestAbort:
    async def test_disconnect_aborts_pending(self):
        host, c1 = await _start_pair(port=30)
        try:
            # Start a request that will never get answered.
            req_task = asyncio.create_task(
                host.request(
                    to_node="w1",
                    namespace="silent",
                    type="ping",
                    timeout=5.0,
                )
            )
            # Give it a moment to enter the pending table.
            await asyncio.sleep(0.05)
            # Now disconnect.
            await c1.stop()
            # Pending request should resolve with RequestAbortedError.
            with pytest.raises(Exception):
                await asyncio.wait_for(req_task, timeout=2.0)
        finally:
            await host.stop()
