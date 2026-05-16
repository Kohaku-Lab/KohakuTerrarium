"""L1 transport abstraction for the Laboratory layer.

The transport moves opaque frames (bytes) between two endpoints. It
knows nothing about envelopes, channels, or nodes — those are L2+
concerns.

Three pieces:

- :class:`Transport` — host-side ``serve()`` and client-side ``connect()``.
- :class:`Server` — a listening host; iterate ``connections()`` to
  receive incoming :class:`Connection` objects.
- :class:`Connection` — a bidirectional, framed byte channel between
  two endpoints.

Two implementations ship with 1.5.0:

- :class:`~kohakuterrarium.laboratory._internal.transport_inproc.InProcTransport`
  — in-process, for tests and embedded clients.
- :class:`~kohakuterrarium.laboratory._internal.transport_ws.WebSocketTransport`
  — real network transport for production.
"""

from collections.abc import AsyncIterator
from typing import Protocol


class ConnectionClosed(Exception):
    """Raised when an I/O operation is attempted on a closed connection.

    Either side may have closed: ``send_frame`` after local close,
    ``recv_frame`` after peer close, or both. The error message
    identifies which side initiated the close when known.
    """


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
    """A two-sided transport.

    Hosts call :meth:`serve` to listen. Clients call :meth:`connect`
    to dial out. Both halves of a transport produce :class:`Connection`
    objects with identical semantics.
    """

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
