"""WebSocket transport for the Laboratory layer.

Wraps the :mod:`websockets` library to implement the
:class:`~kohakuterrarium.laboratory._internal.transport_base.Transport`
protocol.

Address conventions:

- **Server** address is ``host:port`` (e.g. ``"127.0.0.1:8100"``).
  Port ``0`` selects an OS-chosen ephemeral port; the actual port is
  exposed via :attr:`WebSocketServer.local_addr`.
- **Client** address is a full WebSocket URL (e.g.
  ``"ws://127.0.0.1:8100/_lab"`` or ``"wss://host.example.com/_lab"``).

This transport runs a dedicated WebSocket server. In lab-host mode the
FastAPI lifespan starts the same :class:`HostEngine` alongside the API
server, and workers connect to this Lab listener.
"""

import asyncio

from websockets.asyncio.client import connect as _ws_connect
from websockets.asyncio.server import serve as _ws_serve
from websockets.exceptions import (
    ConnectionClosed as _WSConnectionClosed,
    InvalidHandshake,
    InvalidURI,
)

from kohakuterrarium.laboratory._internal.transport_base import (
    Connection,
    ConnectionClosed,
    ConnectionRefused,
    Server,
)
from kohakuterrarium.utils.logging import get_logger

_log = get_logger(__name__)

# Per-message size cap for the Lab transport.  The ``websockets``
# library defaults to 1 MiB — far too small for legitimate APP
# traffic: pushing a ``.kohakutr`` to a worker for resume, deploying
# a creature bundle, or a chunked file transfer all routinely exceed
# 1 MiB.  Before this cap was raised, an oversized message silently
# *killed the connection* ("client disconnected before responding")
# instead of erroring cleanly.  The Lab link is a trusted,
# token-authenticated channel between a host and its own workers, so
# a generous ceiling is appropriate; it still bounds a pathological
# message rather than allowing unbounded ones (``max_size=None``).
LAB_WS_MAX_SIZE = 64 * 1024 * 1024


def _parse_bind_addr(addr: str) -> tuple[str, int]:
    """Parse a ``host:port`` bind address. Trailing ``/path`` is ignored."""
    if ":" not in addr:
        raise ValueError(f"invalid bind addr (need host:port): {addr!r}")
    host, _, tail = addr.rpartition(":")
    port_str = tail.split("/", 1)[0]
    try:
        port = int(port_str)
    except ValueError as exc:
        raise ValueError(f"invalid port in addr {addr!r}: {exc}") from exc
    return host, port


class WebSocketConnection:
    """WebSocket-backed :class:`Connection`.

    Wraps a single ``websockets`` connection (server-side handler arg
    or client-side handle). Frames are sent and received as binary
    WebSocket messages.
    """

    def __init__(self, ws, name: str = "ws") -> None:
        self._ws = ws
        self._name = name
        self._closed_event = asyncio.Event()

    @property
    def is_alive(self) -> bool:
        return not self._closed_event.is_set()

    async def send_frame(self, data: bytes) -> None:
        if self._closed_event.is_set():
            raise ConnectionClosed(f"{self._name}: closed")
        try:
            await self._ws.send(data)
        except _WSConnectionClosed as exc:
            self._closed_event.set()
            raise ConnectionClosed(f"{self._name}: {exc}") from exc

    async def recv_frame(self) -> bytes:
        if self._closed_event.is_set():
            raise ConnectionClosed(f"{self._name}: closed")
        try:
            msg = await self._ws.recv()
        except _WSConnectionClosed as exc:
            self._closed_event.set()
            raise ConnectionClosed(f"{self._name}: {exc}") from exc
        if isinstance(msg, str):
            return msg.encode("utf-8")
        return bytes(msg)

    async def close(self) -> None:
        if self._closed_event.is_set():
            return
        self._closed_event.set()
        try:
            await self._ws.close()
        except Exception:
            # close() should always succeed from the user's perspective.
            pass
        _log.debug("ws connection closed", addr_label=self._name)


class WebSocketServer:
    """WebSocket-backed :class:`Server`.

    Owns the underlying :class:`websockets.asyncio.server.Server` and
    bridges its per-connection handler into our queue-based accept
    pattern.
    """

    def __init__(self) -> None:
        self._accept_queue: "asyncio.Queue[WebSocketConnection | None]" = (
            asyncio.Queue()
        )
        self._ws_server = None  # set by WebSocketTransport.serve()
        self._closed = False
        self._shutdown_event = asyncio.Event()
        self._addr_label = "ws-server"

    @property
    def local_addr(self) -> tuple[str, int] | None:
        """The (host, port) tuple of the listening socket, or ``None`` if not bound."""
        if self._ws_server is None:
            return None
        sockets = self._ws_server.sockets
        if not sockets:
            return None
        sockname = sockets[0].getsockname()
        return (sockname[0], sockname[1])

    async def connections(self):
        while True:
            conn = await self._accept_queue.get()
            if conn is None:
                return
            yield conn

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Wake up any per-connection handlers so they can exit; otherwise
        # wait_closed() below would deadlock waiting for them.
        self._shutdown_event.set()
        if self._ws_server is not None:
            self._ws_server.close()
            try:
                await self._ws_server.wait_closed()
            except Exception:
                pass
        await self._accept_queue.put(None)


class WebSocketTransport:
    """WebSocket-backed :class:`Transport`.

    Servers bind to a ``host:port`` address; clients connect via a
    ``ws://`` or ``wss://`` URL. The two halves use the same
    :class:`WebSocketConnection` adapter on top of the underlying
    websockets library.
    """

    async def serve(self, addr: str) -> Server:
        host, port = _parse_bind_addr(addr)
        server = WebSocketServer()
        server._addr_label = f"ws-server:{addr}"

        async def handler(ws):
            try:
                peer = getattr(ws, "remote_address", None)
            except Exception:
                peer = None
            _log.debug(
                "ws connection accepted",
                bind_addr=addr,
                peer=str(peer) if peer else "unknown",
            )
            conn = WebSocketConnection(ws, name=f"ws-server:{addr}")
            await server._accept_queue.put(conn)
            # websockets closes the underlying socket when the handler
            # returns. Keep it alive until either the wrapper signals
            # close OR the server is shutting down.
            close_wait = asyncio.create_task(conn._closed_event.wait())
            shutdown_wait = asyncio.create_task(server._shutdown_event.wait())
            try:
                await asyncio.wait(
                    [close_wait, shutdown_wait],
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                close_wait.cancel()
                shutdown_wait.cancel()
                if not conn._closed_event.is_set():
                    await conn.close()

        server._ws_server = await _ws_serve(
            handler, host, port, max_size=LAB_WS_MAX_SIZE
        )
        local = None
        try:
            sockets = server._ws_server.sockets
            if sockets:
                local = sockets[0].getsockname()
        except Exception:
            pass
        _log.info(
            "ws server listening",
            bind_addr=addr,
            local=str(local) if local else None,
        )
        return server

    async def connect(self, addr: str) -> Connection:
        try:
            ws = await _ws_connect(addr, max_size=LAB_WS_MAX_SIZE)
        except (OSError, InvalidURI, InvalidHandshake) as exc:
            _log.debug("ws connect failed", addr=addr, error=str(exc))
            raise ConnectionRefused(f"could not connect to {addr}: {exc}") from exc
        _log.debug("ws client connected", addr=addr)
        return WebSocketConnection(ws, name=f"ws-client:{addr}")


__all__ = [
    "WebSocketConnection",
    "WebSocketServer",
    "WebSocketTransport",
]
