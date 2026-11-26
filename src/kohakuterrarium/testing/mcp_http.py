"""Local streamable-HTTP MCP server guarded by a bearer token, for tests.

:func:`bearer_mcp_server` runs a real ``FastMCP`` streamable-HTTP app
behind a ``Authorization: Bearer <token>`` check on an ephemeral
loopback port, in its own thread and event loop, so sync clients
(``TestClient``) and async tests can both reach it. Requests without
the right header get ``401``; the MCP endpoint is ``<base>/mcp``.
"""

import asyncio
import socket
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

import uvicorn
from mcp.server.fastmcp import FastMCP

_START_TIMEOUT_SECONDS = 15.0


def _bearer_app(token: str) -> Any:
    server = FastMCP("kt-bearer-test")

    @server.tool()
    def echo(text: str) -> str:
        """Echo the given text back."""
        return f"echo: {text}"

    inner = server.streamable_http_app()
    expected = f"Bearer {token}".encode()

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        headers = dict(scope.get("headers") or [])
        if scope["type"] == "http" and headers.get(b"authorization") != expected:
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"unauthorized"})
            return
        await inner(scope, receive, send)

    return app


def closed_loopback_port() -> int:
    """Return a loopback port that nothing listens on."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def bearer_mcp_server(token: str) -> Iterator[str]:
    """Serve the bearer-protected MCP app; yield its base URL (``http://127.0.0.1:<port>``)."""
    config = uvicorn.Config(
        _bearer_app(token),
        host="127.0.0.1",
        port=0,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(
        target=loop.run_until_complete, args=(server.serve(),), daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        while not server.started and time.monotonic() < deadline and thread.is_alive():
            time.sleep(0.02)
        if not server.started:
            raise RuntimeError("bearer MCP test server did not start")
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=_START_TIMEOUT_SECONDS)
        loop.close()
