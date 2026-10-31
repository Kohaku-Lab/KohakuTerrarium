"""Guard for routes that act on the machine running the server."""

from fastapi import HTTPException, Request

LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def is_local(request: Request) -> bool:
    """Whether the request comes from this machine."""
    return (request.client.host if request.client else "") in LOOPBACK


def local_only(request: Request, message: str) -> None:
    """Refuse requests from other machines with ``message``."""
    if not is_local(request):
        raise HTTPException(403, message)
