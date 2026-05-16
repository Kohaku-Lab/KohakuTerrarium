"""Liveness + readiness endpoints — operator + healthcheck surface.

``/healthz``  — liveness: always 200 as long as the Python process is
servicing requests.  Used by Docker ``HEALTHCHECK``, kubelet liveness
probes, and reverse-proxy upstream health checks.

``/readyz``   — readiness: 200 only when the process is ready to
*accept work*.  In ``standalone`` mode that is "the engine is up".
In ``lab-host`` mode it additionally requires the Lab WebSocket
transport to be bound and accepting client connections.  A 503
response tells the reverse-proxy to keep traffic away (and tells the
operator the AIO entry-script is still mid-boot).

Neither endpoint touches the LLM or the disk — both must stay cheap
enough that a 1 Hz polling loop is free.
"""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: process is up and serving HTTP."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: mode-aware "ready to accept work" check.

    ``200`` ``{"status": "ready", "mode": ..., ...}`` when the relevant
    subsystem is fully started; ``503`` ``{"status": "not_ready", ...}``
    while still booting (the Lab transport may take a second to bind).
    """
    app = request.app
    lab_mode = getattr(app.state, "lab_mode", "standalone")
    body: dict[str, Any] = {"mode": lab_mode}

    if lab_mode == "lab-host":
        host_engine = getattr(app.state, "lab_host_engine", None)
        if host_engine is None:
            body["status"] = "not_ready"
            body["reason"] = "lab host engine not started"
            return JSONResponse(body, status_code=503)
        # ``HostEngine`` exposes ``is_running`` once ``start()`` has
        # bound the listening socket.  Anything else means we're still
        # mid-boot and the reverse-proxy should hold traffic.
        if not getattr(host_engine, "is_running", True):
            body["status"] = "not_ready"
            body["reason"] = "lab transport not listening"
            return JSONResponse(body, status_code=503)
        body["lab_bind"] = getattr(app.state, "lab_bind", None)

    body["status"] = "ready"
    return JSONResponse(body, status_code=200)
