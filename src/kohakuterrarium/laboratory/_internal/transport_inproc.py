"""In-process transport for the Laboratory layer.

Host and client live in the same process; frames are shuffled between
:mod:`asyncio` queues. No network, no serialization-on-the-wire (frames
are passed by reference).

Used for:

- Unit tests of L3 and L4 layers without the WebSocket dependency.
- Embedded-client scenarios where a single process runs both host and
  client. The lab bridge can short-circuit through this transport with
  zero network overhead.

Address format is arbitrary: any string identifies a server slot in the
class-level registry. Tests typically use names like ``"test-host"``.
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
    """In-process :class:`Connection` implementation.

    Two queues — outbound and inbound — paired with a sister connection
    where the queues are swapped. ``send_frame`` puts to outbound;
    ``recv_frame`` gets from inbound. Closing either side signals the
    peer via a ``None`` sentinel on the recv queue.
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
        # Set by InProcTransport after pair construction.
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
            # Peer sent a close sentinel.
            self._peer_closed = True
            raise ConnectionClosed(f"{self._name}: peer closed")
        return frame

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Notify peer (if it exists and hasn't already closed) by
        # pushing a sentinel into the peer's recv queue (= our send
        # queue).
        if self._peer is not None and not self._peer._closed:
            await self._send_queue.put(None)


class InProcServer:
    """In-process :class:`Server` implementation.

    Accepts incoming connections into an asyncio queue; ``connections()``
    drains that queue until the server is closed.
    """

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
        # Unregister from transport registry first so new connects fail.
        InProcTransport._unregister(self._addr)
        # Wake up any blocked connections() iterator.
        await self._accept_queue.put(None)


class InProcTransport:
    """In-process :class:`Transport` implementation.

    Servers register their listening addresses in a class-level
    registry; clients look up servers by address.

    The registry is process-wide. Tests that want isolation should use
    distinct addresses (e.g. UUIDs, or per-test fixtures).
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
        # Build a connection pair.
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
        """Reset the registry. Test utility only."""
        cls._registry.clear()


__all__ = [
    "InProcConnection",
    "InProcServer",
    "InProcTransport",
]
