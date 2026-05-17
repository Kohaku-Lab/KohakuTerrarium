"""Handshake edge-case tests for :class:`HostEngine`.

Targets the perform_handshake branches we don't reach with happy-path
client connections: closed connection during recv, malformed first
envelope, non-HELLO first envelope, protocol mismatch.
"""

import asyncio

import pytest

from kohakuterrarium.laboratory.config import HostConfig
from kohakuterrarium.laboratory._internal.envelope import (
    Envelope,
    EnvelopeKind,
)
from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.protocol import (
    HelloPayload,
    build_hello,
)
from kohakuterrarium.laboratory._internal.transport_inproc import (
    InProcTransport,
)


@pytest.fixture(autouse=True)
def _reset_inproc():
    InProcTransport._clear_registry()
    yield
    InProcTransport._clear_registry()


async def _start_host(port=1):
    cfg = HostConfig(
        bind_host="hs",
        bind_port=port,
        token="t",
        heartbeat_timeout_seconds=5.0,
    )
    host = HostEngine(cfg, InProcTransport())
    await host.start()
    return host


class TestHandshakeEdgeCases:
    async def test_garbage_first_frame_closes(self):
        host = await _start_host(port=1)
        try:
            transport = InProcTransport()
            conn = await transport.connect("hs:1")
            await conn.send_frame(b"not-an-envelope")
            await asyncio.sleep(0.1)
            # No client registered.
            assert host.alive_clients() == set()
        finally:
            await host.stop()

    async def test_first_frame_is_send_closes(self):
        host = await _start_host(port=2)
        try:
            transport = InProcTransport()
            conn = await transport.connect("hs:2")
            env = Envelope(
                from_node="x",
                to_node="_host",
                kind=EnvelopeKind.SEND,
                stream_id=0,
                seq=0,
            )
            await conn.send_frame(env.encode())
            await asyncio.sleep(0.1)
            assert host.alive_clients() == set()
        finally:
            await host.stop()

    async def test_invalid_hello_payload_closes(self):
        host = await _start_host(port=3)
        try:
            transport = InProcTransport()
            conn = await transport.connect("hs:3")
            # Build a HELLO with missing required fields by hand.
            from kohakuvault import DataPacker

            packer = DataPacker("msgpack")
            raw = Envelope(
                from_node="",
                to_node="_host",
                kind=EnvelopeKind.HELLO,
                stream_id=0,
                seq=0,
                payload=packer.pack({"garbage": "yes"}),
            )
            await conn.send_frame(raw.encode())
            await asyncio.sleep(0.1)
            assert host.alive_clients() == set()
        finally:
            await host.stop()

    async def test_protocol_mismatch_rejected(self):
        host = await _start_host(port=4)
        try:
            transport = InProcTransport()
            conn = await transport.connect("hs:4")
            # Hand-craft a HELLO with a wildly incompatible protocol version.
            hello = HelloPayload(
                protocol_version="0.0.1",
                framework_version="test",
                client_name="bad-proto",
                token="t",
                capabilities=(),
            )
            await conn.send_frame(build_hello(hello).encode())
            # Receive the reject frame.
            raw = await conn.recv_frame()
            env = Envelope.decode(raw)
            assert env.kind is EnvelopeKind.CONTROL
        finally:
            await host.stop()

    async def test_rotated_token_is_used_for_future_handshakes(self):
        host = await _start_host(port=6)
        try:
            host.set_token("fresh")
            transport = InProcTransport()

            old_conn = await transport.connect("hs:6")
            old_hello = HelloPayload(
                protocol_version="1.0",
                framework_version="test",
                client_name="old-token",
                token="t",
                capabilities=(),
            )
            await old_conn.send_frame(build_hello(old_hello).encode())
            old_raw = await old_conn.recv_frame()
            old_env = Envelope.decode(old_raw)
            assert old_env.kind is EnvelopeKind.CONTROL
            assert "old-token" not in host.alive_clients()

            new_conn = await transport.connect("hs:6")
            new_hello = HelloPayload(
                protocol_version="1.0",
                framework_version="test",
                client_name="new-token",
                token="fresh",
                capabilities=(),
            )
            await new_conn.send_frame(build_hello(new_hello).encode())
            new_raw = await new_conn.recv_frame()
            new_env = Envelope.decode(new_raw)
            assert new_env.kind is EnvelopeKind.WELCOME
            await asyncio.sleep(0.1)
            assert "new-token" in host.alive_clients()
        finally:
            await host.stop()

    async def test_blocked_client_id_is_rejected_on_handshake(self):
        host = await _start_host(port=7)
        try:
            host.block_client_id("blocked-worker")
            transport = InProcTransport()
            conn = await transport.connect("hs:7")
            hello = HelloPayload(
                protocol_version="1.0",
                framework_version="test",
                client_name="blocked-worker",
                token="t",
                capabilities=(),
            )
            await conn.send_frame(build_hello(hello).encode())
            raw = await conn.recv_frame()
            env = Envelope.decode(raw)
            assert env.kind is EnvelopeKind.CONTROL
            await asyncio.sleep(0.1)
            assert "blocked-worker" not in host.alive_clients()

            host.unblock_client_id("blocked-worker")
            retry_conn = await transport.connect("hs:7")
            await retry_conn.send_frame(build_hello(hello).encode())
            retry_raw = await retry_conn.recv_frame()
            retry_env = Envelope.decode(retry_raw)
            assert retry_env.kind is EnvelopeKind.WELCOME
        finally:
            await host.stop()

    async def test_handshake_recv_closed_silent(self):
        host = await _start_host(port=5)
        try:
            transport = InProcTransport()
            conn = await transport.connect("hs:5")
            # Close immediately without sending anything.
            await conn.close()
            await asyncio.sleep(0.1)
            assert host.alive_clients() == set()
        finally:
            await host.stop()
