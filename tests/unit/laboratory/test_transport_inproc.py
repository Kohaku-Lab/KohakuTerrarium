"""Unit tests for :mod:`kohakuterrarium.laboratory._internal.transport_inproc`."""

import asyncio
import uuid

import pytest

from kohakuterrarium.laboratory._internal.transport_base import (
    AddressInUse,
    ConnectionClosed,
    ConnectionRefused,
)
from kohakuterrarium.laboratory._internal.transport_inproc import (
    InProcServer,
    InProcTransport,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Wipe the class-level registry between tests."""
    InProcTransport._clear_registry()
    yield
    InProcTransport._clear_registry()


def _addr() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


# ── serve / connect ──────────────────────────────────────────────


class TestServeAndConnect:
    async def test_serve_returns_server(self):
        t = InProcTransport()
        server = await t.serve(_addr())
        assert isinstance(server, InProcServer)
        await server.close()

    async def test_serve_address_in_use(self):
        t = InProcTransport()
        addr = _addr()
        server = await t.serve(addr)
        try:
            with pytest.raises(AddressInUse):
                await t.serve(addr)
        finally:
            await server.close()

    async def test_connect_unknown_addr(self):
        t = InProcTransport()
        with pytest.raises(ConnectionRefused):
            await t.connect("not-bound")

    async def test_connect_after_close_refused(self):
        t = InProcTransport()
        addr = _addr()
        server = await t.serve(addr)
        await server.close()
        with pytest.raises(ConnectionRefused):
            await t.connect(addr)

    async def test_round_trip_frame(self):
        t = InProcTransport()
        addr = _addr()
        server = await t.serve(addr)
        try:
            client = await t.connect(addr)
            # Pull the server-side connection.
            server_iter = server.connections().__aiter__()
            server_conn = await server_iter.__anext__()
            try:
                await client.send_frame(b"hello")
                received = await server_conn.recv_frame()
                assert received == b"hello"
                await server_conn.send_frame(b"world")
                response = await client.recv_frame()
                assert response == b"world"
            finally:
                await server_conn.close()
                await client.close()
        finally:
            await server.close()


class TestConnection:
    async def test_is_alive_initially(self):
        t = InProcTransport()
        addr = _addr()
        server = await t.serve(addr)
        try:
            client = await t.connect(addr)
            try:
                assert client.is_alive
            finally:
                await client.close()
        finally:
            await server.close()

    async def test_send_after_close_raises(self):
        t = InProcTransport()
        addr = _addr()
        server = await t.serve(addr)
        try:
            client = await t.connect(addr)
            await client.close()
            with pytest.raises(ConnectionClosed):
                await client.send_frame(b"x")
        finally:
            await server.close()

    async def test_send_after_peer_close_raises(self):
        # Local side is still open but the *peer* has closed — observed
        # when ``recv_frame`` drains the peer's close sentinel. A
        # subsequent ``send_frame`` must refuse with ConnectionClosed
        # naming the peer rather than queueing a frame nobody will read.
        t = InProcTransport()
        addr = _addr()
        server = await t.serve(addr)
        try:
            client = await t.connect(addr)
            server_iter = server.connections().__aiter__()
            server_conn = await server_iter.__anext__()
            await server_conn.close()
            # Drain the close sentinel so the client marks the peer dead
            # without closing its own side.
            with pytest.raises(ConnectionClosed):
                await client.recv_frame()
            assert client._closed is False
            with pytest.raises(ConnectionClosed, match="peer closed"):
                await client.send_frame(b"x")
        finally:
            await server.close()

    async def test_recv_after_close_raises(self):
        t = InProcTransport()
        addr = _addr()
        server = await t.serve(addr)
        try:
            client = await t.connect(addr)
            await client.close()
            with pytest.raises(ConnectionClosed):
                await client.recv_frame()
        finally:
            await server.close()

    async def test_peer_close_signals(self):
        t = InProcTransport()
        addr = _addr()
        server = await t.serve(addr)
        try:
            client = await t.connect(addr)
            server_iter = server.connections().__aiter__()
            server_conn = await server_iter.__anext__()
            await server_conn.close()
            with pytest.raises(ConnectionClosed):
                await client.recv_frame()
            assert client.is_alive is False
        finally:
            await server.close()

    async def test_double_close_is_noop(self):
        t = InProcTransport()
        addr = _addr()
        server = await t.serve(addr)
        try:
            client = await t.connect(addr)
            await client.close()
            await client.close()  # no raise
        finally:
            await server.close()


class TestServer:
    async def test_close_makes_connections_iterator_stop(self):
        t = InProcTransport()
        addr = _addr()
        server = await t.serve(addr)
        consumed = []

        async def consume():
            async for conn in server.connections():
                consumed.append(conn)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0)
        await server.close()
        await asyncio.wait_for(task, timeout=1.0)
        assert consumed == []  # no connection arrived before close

    async def test_addr_property(self):
        t = InProcTransport()
        addr = _addr()
        server = await t.serve(addr)
        try:
            assert server.addr == addr
        finally:
            await server.close()

    async def test_double_close_safe(self):
        t = InProcTransport()
        addr = _addr()
        server = await t.serve(addr)
        await server.close()
        await server.close()  # no raise
