"""Capability URL authentication for the standalone MCP transport."""

import re
import secrets

from starlette.responses import PlainTextResponse


class MCPSecretPath:
    """Gate all HTTP methods and hide credential paths from downstream logging."""

    def __init__(self, app, *, secret: str):
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", secret):
            raise ValueError("MCP secret must be a high-entropy URL-safe token")
        self.app = app
        self.path = ("/mcp/" + secret).encode("ascii")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        if not secrets.compare_digest(scope["path"].encode("utf-8"), self.path):
            await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return
        clean = dict(scope, path="/mcp", raw_path=b"/mcp", query_string=b"")
        await self.app(clean, receive, send)
