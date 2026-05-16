"""Unit tests for :mod:`kohakuterrarium.laboratory._internal.transport_ws`.

Most paths exercise real ``websockets`` round-trips on loopback to an
ephemeral port; the pure parsing branch tests the bind-addr helper
without any I/O.
"""

import asyncio

import pytest

from kohakuterrarium.laboratory._internal.transport_base import (
    ConnectionClosed,
    ConnectionRefused,
)
from kohakuterrarium.laboratory._internal.transport_ws import (
    WebSocketTransport,
    _parse_bind_addr,
)

# ── _parse_bind_addr ──────────────────────────────────────────


class TestParseBindAddr:
    def test_basic(self):
        host, port = _parse_bind_addr("127.0.0.1:8100")
        assert host == "127.0.0.1"
        assert port == 8100

    def test_with_path_suffix_stripped(self):
        host, port = _parse_bind_addr("127.0.0.1:8100/_lab")
        assert host == "127.0.0.1"
        assert port == 8100

    def test_missing_colon_raises(self):
        with pytest.raises(ValueError, match="invalid bind addr"):
            _parse_bind_addr("nohostport")

    def test_invalid_port_raises(self):
        with pytest.raises(ValueError, match="invalid port"):
            _parse_bind_addr("127.0.0.1:not-a-number")


# ── client connect failure ────────────────────────────────────


class TestConnectFailure:
    async def test_refused_when_no_server(self):
        transport = WebSocketTransport()
        with pytest.raises(ConnectionRefused):
            # Loopback port 1 is reserved/unbound on most systems.
            await transport.connect("ws://127.0.0.1:1")

    async def test_invalid_uri_refused(self):
        transport = WebSocketTransport()
        with pytest.raises(ConnectionRefused):
            await transport.connect("not-a-url://garbage")


# ── round-trip server <-> client ─────────────────────────────


class TestRoundTrip:
    async def test_serve_and_connect_send_recv(self):
        transport = WebSocketTransport()
        server = await transport.serve("127.0.0.1:0")
        try:
            host, port = server.local_addr
            url = f"ws://{host}:{port}"

            received_frames = []
            client_conn_holder = {}

            async def server_pump():
                async for conn in server.connections():
                    # Echo back any frame we receive.
                    client_conn_holder["server"] = conn
                    try:
                        frame = await conn.recv_frame()
                        received_frames.append(frame)
                        await conn.send_frame(b"server:" + frame)
                    except ConnectionClosed:
                        return
                    return

            pump_task = asyncio.create_task(server_pump())
            client = await transport.connect(url)
            try:
                await client.send_frame(b"hi")
                # Server's echo back.
                response = await asyncio.wait_for(client.recv_frame(), timeout=2.0)
                assert response == b"server:hi"
                assert received_frames == [b"hi"]
                assert client.is_alive
            finally:
                await client.close()
                pump_task.cancel()
                try:
                    await pump_task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            await server.close()

    async def test_server_close_idempotent(self):
        transport = WebSocketTransport()
        server = await transport.serve("127.0.0.1:0")
        await server.close()
        await server.close()  # idempotent

    async def test_local_addr_before_serve_is_none(self):
        from kohakuterrarium.laboratory._internal.transport_ws import (
            WebSocketServer,
        )

        server = WebSocketServer()
        assert server.local_addr is None


# ── WebSocketConnection close idempotency ───────────────────


class TestConnectionClose:
    async def test_close_idempotent(self):
        transport = WebSocketTransport()
        server = await transport.serve("127.0.0.1:0")
        try:
            host, port = server.local_addr
            url = f"ws://{host}:{port}"

            async def accept():
                async for conn in server.connections():
                    return conn
                return None

            accept_task = asyncio.create_task(accept())
            client = await transport.connect(url)
            try:
                await client.close()
                # Idempotent — second close is silent.
                await client.close()
                assert not client.is_alive
            finally:
                accept_task.cancel()
                try:
                    await accept_task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            await server.close()

    async def test_send_after_close_raises(self):
        transport = WebSocketTransport()
        server = await transport.serve("127.0.0.1:0")
        try:
            host, port = server.local_addr
            url = f"ws://{host}:{port}"

            async def accept():
                async for _ in server.connections():
                    return
                return

            accept_task = asyncio.create_task(accept())
            client = await transport.connect(url)
            try:
                await client.close()
                with pytest.raises(ConnectionClosed):
                    await client.send_frame(b"x")
                with pytest.raises(ConnectionClosed):
                    await client.recv_frame()
            finally:
                accept_task.cancel()
                try:
                    await accept_task
                except (asyncio.CancelledError, Exception):
                    pass
        finally:
            await server.close()
