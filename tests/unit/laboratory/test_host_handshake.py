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
