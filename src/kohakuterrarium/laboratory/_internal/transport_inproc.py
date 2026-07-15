"""In-process transport for the Laboratory layer.

Move frames between same-process endpoints through :mod:`asyncio` queues.
Frames are passed by reference rather than serialized. Any string can serve as
an address in the process-wide server registry.
"""

import asyncio
from collections.abc import AsyncIterator
from typing import ClassVar

from kohakuterrarium.laboratory._internal.transport_base import (
    AddressInUse,
    Connection,
    ConnectionClosed,
    ConnectionRefused,
    Server,
)


class InProcConnection:
    """Exchange frames with a paired endpoint over two queues.

    The paired connection swaps the send and receive queues, and ``None``
    signals that the peer has closed.
    """

    def __init__(
        self,
        send_queue: "asyncio.Queue[bytes | None]",
        recv_queue: "asyncio.Queue[bytes | None]",
        name: str = "inproc",
    ) -> None:
        self._send_queue = send_queue
        self._recv_queue = recv_queue
        self._closed = False
        self._peer_closed = False
        self._name = name
        # Pairing is deferred until both endpoints exist.
        self._peer: "InProcConnection | None" = None

    @property
    def is_alive(self) -> bool:
        return not self._closed and not self._peer_closed

    async def send_frame(self, data: bytes) -> None:
        if self._closed:
            raise ConnectionClosed(f"{self._name}: local side closed")
        if self._peer_closed:
            raise ConnectionClosed(f"{self._name}: peer closed")
        await self._send_queue.put(data)

    async def recv_frame(self) -> bytes:
        if self._closed:
            raise ConnectionClosed(f"{self._name}: local side closed")
        frame = await self._recv_queue.get()
        if frame is None:
            self._peer_closed = True
            raise ConnectionClosed(f"{self._name}: peer closed")
        return frame

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # A sentinel on our send queue wakes the peer's receiver.
        if self._peer is not None and not self._peer._closed:
            await self._send_queue.put(None)


class InProcServer:
    """Yield queued in-process connections until the server closes."""

    def __init__(self, addr: str) -> None:
        self._addr = addr
        self._accept_queue: "asyncio.Queue[InProcConnection | None]" = asyncio.Queue()
        self._closed = False

    @property
    def addr(self) -> str:
        return self._addr

    async def connections(self) -> AsyncIterator[Connection]:
        while True:
            conn = await self._accept_queue.get()
            if conn is None:
                return
            yield conn

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Unregister before waking the iterator so new connections fail immediately.
        InProcTransport._unregister(self._addr)
        await self._accept_queue.put(None)


class InProcTransport:
    """Resolve in-process servers through a process-wide address registry.

    Callers requiring isolation must use distinct addresses.
    """

    _registry: ClassVar[dict[str, InProcServer]] = {}

    async def serve(self, addr: str) -> Server:
        if addr in InProcTransport._registry:
            raise AddressInUse(f"in-process addr {addr!r} already bound")
        server = InProcServer(addr)
        InProcTransport._registry[addr] = server
        return server

    async def connect(self, addr: str) -> Connection:
        server = InProcTransport._registry.get(addr)
        if server is None or server._closed:
            raise ConnectionRefused(f"no in-process server at {addr!r}")
        client_to_server: "asyncio.Queue[bytes | None]" = asyncio.Queue()
        server_to_client: "asyncio.Queue[bytes | None]" = asyncio.Queue()
        client_conn = InProcConnection(
            send_queue=client_to_server,
            recv_queue=server_to_client,
            name=f"inproc:{addr}:client",
        )
        server_conn = InProcConnection(
            send_queue=server_to_client,
            recv_queue=client_to_server,
            name=f"inproc:{addr}:server",
        )
        client_conn._peer = server_conn
        server_conn._peer = client_conn
        await server._accept_queue.put(server_conn)
        return client_conn

    @classmethod
    def _unregister(cls, addr: str) -> None:
        cls._registry.pop(addr, None)

    @classmethod
    def _clear_registry(cls) -> None:
        """Remove all registered servers for test isolation."""
        cls._registry.clear()


__all__ = [
    "InProcConnection",
    "InProcServer",
    "InProcTransport",
]
