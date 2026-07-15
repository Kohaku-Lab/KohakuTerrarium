"""Expose cheap liveness and mode-aware readiness checks.

Liveness reports whether the HTTP process is serving requests. Readiness also
requires the lab host transport to be listening in ``lab-host`` mode. Neither
route performs disk or LLM I/O so infrastructure can poll them frequently.
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
    """Report whether all mode-specific services can accept work.

    Lab-host readiness requires both a host engine and a bound transport;
    incomplete startup returns 503 so upstream traffic remains withheld.
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
        # ``is_running`` becomes true only after the listening socket is bound,
        # so a false value means startup is incomplete.
        if not getattr(host_engine, "is_running", True):
            body["status"] = "not_ready"
            body["reason"] = "lab transport not listening"
            return JSONResponse(body, status_code=503)
        body["lab_bind"] = getattr(app.state, "lab_bind", None)

    body["status"] = "ready"
    return JSONResponse(body, status_code=200)
