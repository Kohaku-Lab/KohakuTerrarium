"""Guard for routes that act on the machine running the server."""

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def is_local(request: Request) -> bool:
    """Whether the request comes from this machine."""
    return (request.client.host if request.client else "") in LOOPBACK


def local_only(request: Request, message: str) -> None:
    """Refuse requests from other machines with ``message``."""
    if not is_local(request):
        raise HTTPException(403, message)


def is_source_development(connection: HTTPConnection) -> bool:
    """Whether this HTTP or WebSocket connection belongs to source desktop mode."""
    return getattr(connection.app.state, "desktop_source", False)
