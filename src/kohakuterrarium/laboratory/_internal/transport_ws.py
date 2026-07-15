"""Implement Laboratory framed transport over binary WebSocket messages."""

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

# Session transfer and deployment frames exceed the library's 1 MiB default;
# retain a finite ceiling to bound malformed or pathological messages.
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
    """Adapt one WebSocket connection to the framed transport protocol."""

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
            # Closing is idempotent from the transport caller's perspective.
            pass
        _log.debug("ws connection closed", addr_label=self._name)


class WebSocketServer:
    """Bridge WebSocket connection handlers into the server accept iterator."""

    def __init__(self) -> None:
        self._accept_queue: "asyncio.Queue[WebSocketConnection | None]" = (
            asyncio.Queue()
        )
        self._ws_server = None
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
        # Wake connection handlers before waiting for server closure.
        self._shutdown_event.set()
        if self._ws_server is not None:
            self._ws_server.close()
            try:
                await self._ws_server.wait_closed()
            except Exception:
                pass
        await self._accept_queue.put(None)


class WebSocketTransport:
    """Create WebSocket servers from bind addresses and clients from URLs."""

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
            # The handler must remain alive or websockets closes its socket.
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
