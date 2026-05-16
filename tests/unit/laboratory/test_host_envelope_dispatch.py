"""Branch coverage for envelope dispatch in HostEngine.

Drives unexpected-kind envelopes (HELLO/WELCOME after handshake,
unknown kinds, channel:// SEND, malformed CONTROL) over a real
host+client pair.
"""

import asyncio

import pytest

from kohakuterrarium.laboratory.config import ClientConfig, HostConfig
from kohakuterrarium.laboratory._internal.client import ClientConnector
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


async def _start_pair(port=1):
    cfg_h = HostConfig(
        bind_host="dx",
        bind_port=port,
        token="t",
        heartbeat_timeout_seconds=5.0,
    )
    host = HostEngine(cfg_h, InProcTransport())
    await host.start()
    cfg_c = ClientConfig(
        client_name="w1",
        host_url=f"dx:{port}",
        token="t",
        reconnect_initial_delay_seconds=0.1,
        heartbeat_interval_seconds=10.0,
    )
    client = ClientConnector(cfg_c, InProcTransport())
    await client.start()
    return host, client


# ── Unexpected HELLO / WELCOME after handshake ──────────────


class TestUnexpectedHandshakeEnvelopes:
    async def test_hello_after_handshake_logged(self):
        host, client = await _start_pair(port=1)
        try:
            from kohakuterrarium.laboratory._internal.protocol import (
                HelloPayload,
                build_hello,
            )

            # Send a fresh HELLO over an established connection.
            hello = build_hello(
                HelloPayload(
                    protocol_version="1.0",
                    framework_version="",
                    client_name="w1",
                    token="t",
                    capabilities=(),
                )
            )
            await client.send(hello)
            await asyncio.sleep(0.1)
            # Host still alive.
            assert "w1" in host.alive_clients()
        finally:
            await client.stop()
            await host.stop()


# ── BROADCAST fan-out to non-existent listener ──────────────


class TestFanoutBroadcastSkip:
    async def test_unknown_listener_skipped(self):
        host, client = await _start_pair(port=2)
        try:
            from kohakuterrarium.laboratory._internal.control import (
                build_subscribe,
            )

            received: list[Envelope] = []
            client.on_envelope(
                lambda e: (
                    received.append(e) if e.kind is EnvelopeKind.BROADCAST else None
                )
            )
            # Register a real listener (w1) plus a dead one (ghost-node).
            host.addressing.register_listener("ch", "ghost-node")
            await client.send(
                build_subscribe(from_node="w1", to_node="_host", channel="ch")
            )
            await asyncio.sleep(0.05)
            env = Envelope(
                from_node="w1",
                to_node="topic://ch",
                kind=EnvelopeKind.BROADCAST,
                stream_id=0,
                seq=0,
                payload=b"hello-channel",
            )
            await client.send(env)
            await asyncio.sleep(0.1)
            # The fan-out skipped the dead ghost-node but still delivered
            # the broadcast to the live subscriber w1.
            assert [e.payload for e in received] == [b"hello-channel"]
        finally:
            await client.stop()
            await host.stop()


# ── _route_send via channel:// listener ─────────────────────


class TestRouteSendChannel:
    async def test_channel_listener_routes(self):
        host, client = await _start_pair(port=3)
        try:
            from kohakuterrarium.laboratory._internal.control import (
                build_subscribe,
            )

            received: list[Envelope] = []
            client.on_envelope(
                lambda e: received.append(e) if e.kind is EnvelopeKind.SEND else None
            )
            await client.send(
                build_subscribe(from_node="w1", to_node="_host", channel="chx")
            )
            await asyncio.sleep(0.05)
            # SEND envelope addressed to channel://chx.
            env = Envelope(
                from_node="w1",
                to_node="channel://chx",
                kind=EnvelopeKind.SEND,
                stream_id=0,
                seq=0,
                payload=b"to-channel",
            )
            await client.send(env)
            await asyncio.sleep(0.1)
            # The host resolved channel://chx to its sole listener (w1) and
            # routed the SEND there.
            assert [e.payload for e in received] == [b"to-channel"]
        finally:
            await client.stop()
            await host.stop()


# ── Malformed CONTROL ──────────────────────────────────────


class TestMalformedControl:
    async def test_garbage_control_body(self):
        host, client = await _start_pair(port=4)
        try:
            from kohakuterrarium.laboratory._internal.control import (
                _build_control,
            )

            # Build a CONTROL envelope missing "control" key.
            env = _build_control(
                from_node="w1",
                to_node="_host",
                stream_id=0,
                seq=0,
                body={"not_control": True},
            )
            await client.send(env)
            await asyncio.sleep(0.1)
            assert "w1" in host.alive_clients()
        finally:
            await client.stop()
            await host.stop()


# ── _route_send to local _host is dropped ───────────────────


class TestRouteSendToHostDropped:
    async def test_send_to_host_id_drops(self):
        host, client = await _start_pair(port=5)
        try:
            # A bare SEND addressed to the host id has no destination node
            # (the host is not a routable peer) — it must be silently
            # dropped, never echoed back to the sender, and leave the host
            # running.
            echoed: list[Envelope] = []
            client.on_envelope(
                lambda e: echoed.append(e) if e.kind is EnvelopeKind.SEND else None
            )
            env = Envelope(
                from_node="w1",
                to_node=HOST_NODE_ID,
                kind=EnvelopeKind.SEND,
                stream_id=0,
                seq=0,
                payload=b"for-host",
            )
            await client.send(env)
            await asyncio.sleep(0.1)
            assert echoed == []
            assert "w1" in host.alive_clients()
        finally:
            await client.stop()
            await host.stop()


# ── HostEngine.notify error path for unknown client ─────────


class TestHostNotifyUnknown:
    async def test_notify_unknown_raises(self):
        host, client = await _start_pair(port=6)
        try:
            with pytest.raises(KeyError):
                await host.notify(to_node="ghost", namespace="x", type="y")
        finally:
            await client.stop()
            await host.stop()


# ── unsupported envelope kind ───────────────────────────────


class TestUnsupportedKind:
    async def test_log_kind_envelope_is_logged_and_dropped(self):
        host, client = await _start_pair(port=7)
        try:
            # LOG is a defined-but-unimplemented kind in 1.5 — the host's
            # router must log + drop it without crashing the read loop.
            env = Envelope(
                from_node="w1",
                to_node=HOST_NODE_ID,
                kind=EnvelopeKind.LOG,
                stream_id=0,
                seq=0,
                payload=b"some log line",
            )
            await client.send(env)
            await asyncio.sleep(0.1)
            # Host survived; client still a member.
            assert "w1" in host.alive_clients()
            assert client.is_connected
        finally:
            await client.stop()
            await host.stop()


# ── CONTROL / APP addressed to a non-host node ──────────────


class TestNonHostRouting:
    async def test_control_to_peer_is_routed_like_send(self):
        host, client = await _start_pair(port=8)
        try:
            from kohakuterrarium.laboratory._internal.control import (
                build_register_creature,
            )

            received: list[Envelope] = []
            client.on_envelope(
                lambda e: received.append(e) if e.kind is EnvelopeKind.CONTROL else None
            )
            # First make w1 a routable creature ref so the host can
            # resolve a CONTROL addressed to it.
            await client.send(
                build_register_creature(from_node="w1", to_node="_host", ref="cr1")
            )
            await asyncio.sleep(0.05)
            # A CONTROL addressed to the creature ref (NOT _host) takes
            # the route-send path and is delivered to w1.
            env = Envelope(
                from_node="w1",
                to_node="cr1",
                kind=EnvelopeKind.CONTROL,
                stream_id=0,
                seq=0,
                payload=b"peer-control",
            )
            await client.send(env)
            await asyncio.sleep(0.1)
            assert [e.payload for e in received] == [b"peer-control"]
        finally:
            await client.stop()
            await host.stop()

    async def test_app_to_peer_is_forwarded(self):
        host, client = await _start_pair(port=9)
        try:
            from kohakuterrarium.laboratory._internal.control import (
                build_register_creature,
            )

            received: list[Envelope] = []
            client.on_envelope(
                lambda e: received.append(e) if e.kind is EnvelopeKind.APP else None
            )
            await client.send(
                build_register_creature(from_node="w1", to_node="_host", ref="cr2")
            )
            await asyncio.sleep(0.05)
            # An APP envelope addressed to the creature ref is forwarded
            # by the host to w1 rather than dispatched to a host handler.
            env = Envelope(
                from_node="w1",
                to_node="cr2",
                kind=EnvelopeKind.APP,
                stream_id=0,
                seq=0,
                payload=b"forwarded-app",
            )
            await client.send(env)
            await asyncio.sleep(0.1)
            assert [e.payload for e in received] == [b"forwarded-app"]
        finally:
            await client.stop()
            await host.stop()


# ── CONTROL with no registered handler ──────────────────────


class TestUnhandledControl:
    async def test_unknown_control_type_is_dropped(self):
        host, client = await _start_pair(port=10)
        try:
            from kohakuterrarium.laboratory._internal.control import (
                _build_control,
            )

            # A well-formed CONTROL whose ``control`` type has no
            # registered handler is logged + dropped, not an error.
            env = _build_control(
                from_node="w1",
                to_node="_host",
                stream_id=0,
                seq=0,
                body={"control": "no_such_control_type"},
            )
            await client.send(env)
            await asyncio.sleep(0.1)
            assert "w1" in host.alive_clients()
        finally:
            await client.stop()
            await host.stop()


# ── APP for an unregistered namespace ───────────────────────


class TestUnregisteredAppNamespace:
    async def test_app_request_unknown_namespace_is_dropped(self):
        host, client = await _start_pair(port=11)
        try:
            from kohakuterrarium.laboratory._internal.app import (
                build_app_envelope,
            )

            # An APP request for a namespace with no extension is logged
            # and dropped — no response is sent, the host stays up.
            env = build_app_envelope(
                from_node="w1",
                to_node=HOST_NODE_ID,
                namespace="nonexistent.ns",
                type="ping",
                body={},
                request_id="req-xyz",
            )
            await client.send(env)
            await asyncio.sleep(0.1)
            assert "w1" in host.alive_clients()
            assert client.is_connected
        finally:
            await client.stop()
            await host.stop()
