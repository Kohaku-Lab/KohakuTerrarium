"""Additional coverage for :mod:`kohakuterrarium.laboratory._internal.client`.

Drives reconnect, protocol-mismatch, and on-the-wire inbound APP
handling via the InProcTransport.
"""

import asyncio

import pytest

from kohakuterrarium.laboratory.config import ClientConfig, HostConfig
from kohakuterrarium.laboratory._internal.client import (
    ClientConnector,
    NameConflictError,
)
from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.transport_inproc import (
    InProcTransport,
)


@pytest.fixture(autouse=True)
def _reset_inproc():
    InProcTransport._clear_registry()
    yield
    InProcTransport._clear_registry()


async def _start_host(port=1, token="t"):
    cfg = HostConfig(
        bind_host="testh",
        bind_port=port,
        token=token,
        heartbeat_timeout_seconds=5.0,
    )
    host = HostEngine(cfg, InProcTransport())
    await host.start()
    return host


async def _start_client(name="worker", port=1, token="t"):
    cfg = ClientConfig(
        client_name=name,
        host_url=f"testh:{port}",
        token=token,
        reconnect_initial_delay_seconds=0.1,
        heartbeat_interval_seconds=10.0,
    )
    client = ClientConnector(cfg, InProcTransport())
    await client.start()
    return client


# ── name conflict on first attempt is fatal ─────────────────


class TestNameConflict:
    async def test_first_connect_with_existing_name_raises(self):
        host = await _start_host(port=1)
        client1 = await _start_client(name="dup", port=1)
        try:
            with pytest.raises(NameConflictError):
                await _start_client(name="dup", port=1)
        finally:
            await client1.stop()
            await host.stop()


# ── client-side APP handler ─────────────────────────────────


class TestClientAppHandler:
    async def test_handler_runs_and_responds(self):
        host = await _start_host(port=2)
        client = await _start_client(name="w-h", port=2)
        try:
            received = []

            async def handler(msg):
                received.append(msg)
                return {"echo": msg.body}

            client.register_app_extension("api", handler)
            await asyncio.sleep(0.05)
            resp = await host.request(
                to_node="w-h",
                namespace="api",
                type="ping",
                body={"v": 1},
                timeout=5.0,
            )
            assert resp == {"echo": {"v": 1}}
            assert received
        finally:
            await client.stop()
            await host.stop()

    async def test_unknown_namespace_silently_dropped(self):
        host = await _start_host(port=3)
        client = await _start_client(name="w-x", port=3)
        try:
            # No handler for "ghost-ns" on the client → request times out
            # because the worker has no extension to respond.
            with pytest.raises(Exception):
                await host.request(
                    to_node="w-x",
                    namespace="ghost-ns",
                    type="x",
                    timeout=0.3,
                )
        finally:
            await client.stop()
            await host.stop()

    async def test_handler_exception_swallowed_so_caller_times_out(self):
        host = await _start_host(port=4)
        client = await _start_client(name="w-bad", port=4)
        try:

            async def handler(msg):
                raise RuntimeError("boom")

            client.register_app_extension("api", handler)
            await asyncio.sleep(0.05)
            with pytest.raises(Exception):
                await host.request(
                    to_node="w-bad",
                    namespace="api",
                    type="x",
                    timeout=0.3,
                )
        finally:
            await client.stop()
            await host.stop()


# ── client send() while running ─────────────────────────────


class TestClientSend:
    async def test_send_envelope_into_buffer(self):
        host = await _start_host(port=5)
        client = await _start_client(name="w-s", port=5)
        try:
            # A heartbeat envelope into the send buffer should not block.
            from kohakuterrarium.laboratory._internal.envelope import (
                Envelope,
                EnvelopeKind,
            )

            await client.send(
                Envelope(
                    from_node="w-s",
                    to_node="_host",
                    kind=EnvelopeKind.HEARTBEAT,
                    stream_id=0,
                    seq=0,
                )
            )
            await asyncio.sleep(0.05)
        finally:
            await client.stop()
            await host.stop()


# ── client read_loop handles malformed envelopes ─────────────


class TestClientReadLoopResilience:
    async def test_malformed_inbound_not_fatal(self):
        host = await _start_host(port=6)
        client = await _start_client(name="w-mal", port=6)
        try:
            # Inject a malformed frame directly into the client's read
            # path via the host's connection.
            connected_clients = list(host._clients.values())
            assert connected_clients
            cc = connected_clients[0]
            # Send a malformed frame to the client.
            await cc.send_buffer.put(
                type(
                    "_Bad",
                    (),
                    {"encode": staticmethod(lambda: b"not-an-envelope")},
                )()
            )
            await asyncio.sleep(0.1)
            # Client is still connected.
            assert client.is_connected
        finally:
            await client.stop()
            await host.stop()
