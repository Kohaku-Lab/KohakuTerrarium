"""L1 transport abstraction for the Laboratory layer.

Define transport protocols for opaque framed-byte communication.

Transports preserve frame boundaries but intentionally know nothing about
higher-level envelopes, channels, or nodes.
"""

from collections.abc import AsyncIterator
from typing import Protocol


class ConnectionClosed(Exception):
    """Indicate that frame I/O cannot continue because either side closed."""


class ConnectionRefused(Exception):
    """Raised by :meth:`Transport.connect` when no server is listening at addr."""


class AddressInUse(Exception):
    """Raised by :meth:`Transport.serve` when ``addr`` is already bound."""


class Connection(Protocol):
    """A bidirectional, framed byte channel between two endpoints.

    Frames are arbitrary ``bytes`` payloads. The transport guarantees
    frame boundaries: ``recv_frame`` returns exactly one frame per call,
    matching one ``send_frame`` on the peer.
    """

    @property
    def is_alive(self) -> bool:
        """True until :meth:`close` completes or the peer closes."""
        ...

    async def send_frame(self, data: bytes) -> None:
        """Send one frame.

        Raises:
            ConnectionClosed: if this side or the peer has closed.
        """
        ...

    async def recv_frame(self) -> bytes:
        """Receive one frame.

        Blocks until a frame arrives or the connection closes.

        Raises:
            ConnectionClosed: if the connection is closed and no frame
                is pending.
        """
        ...

    async def close(self) -> None:
        """Close the connection. Idempotent."""
        ...


class Server(Protocol):
    """A listening server.

    Iterate :meth:`connections` to receive new :class:`Connection`
    objects as peers connect. The iterator terminates when
    :meth:`close` is called.
    """

    def connections(self) -> AsyncIterator[Connection]:
        """Yield :class:`Connection` objects as peers connect."""
        ...

    async def close(self) -> None:
        """Stop accepting new connections. Idempotent.

        Existing connections continue to function until each side closes
        them independently.
        """
        ...


class Transport(Protocol):
    """Provide server-side listening and client-side connection creation."""

    async def serve(self, addr: str) -> Server:
        """Bind to ``addr`` and start accepting connections.

        Raises:
            AddressInUse: if ``addr`` is already bound by this or another
                server on the same transport.
        """
        ...

    async def connect(self, addr: str) -> Connection:
        """Dial a server at ``addr``.

        Raises:
            ConnectionRefused: if no server is listening at ``addr``.
        """
        ...


__all__ = [
    "AddressInUse",
    "Connection",
    "ConnectionClosed",
    "ConnectionRefused",
    "Server",
    "Transport",
]
