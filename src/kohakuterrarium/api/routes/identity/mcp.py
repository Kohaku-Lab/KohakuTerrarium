"""Identity MCP — MCP server registry."""

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.auth import verify_admin_token
from kohakuterrarium.mcp.client import MCPClientManager, MCPServerConfig
from kohakuterrarium.studio.identity.mcp_servers import (
    delete_server,
    find_server,
    load_servers,
    upsert_server,
)
from kohakuterrarium.studio.identity.mcp_usage import find_creatures_using_server

router = APIRouter()


class MCPServerRequest(BaseModel):
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    url: str = ""
    connect_timeout: float | None = None


class MCPServerPatch(BaseModel):
    """Partial update — every field is optional; only set fields apply.

    ``name`` is immutable (the resource identity); callers that want to
    rename should ``DELETE`` + ``POST``.  ``args`` / ``env`` are
    replace-in-full to keep the wire format predictable.
    """

    transport: Literal["stdio", "http"] | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    connect_timeout: float | None = None


class MCPTestResult(BaseModel):
    ok: bool
    error: str | None = None
    tool_count: int | None = None
    elapsed_ms: int | None = None


class CreatureRef(BaseModel):
    name: str
    kind: Literal["creature", "terrarium"]
    path: str


@router.get("/mcp")
async def list_mcp_servers():
    return {"servers": load_servers()}


@router.post("/mcp", dependencies=[Depends(verify_admin_token)])
async def add_mcp_server(req: MCPServerRequest):
    try:
        upsert_server(req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"status": "saved", "name": req.name}


@router.patch("/mcp/{name}", dependencies=[Depends(verify_admin_token)])
async def patch_mcp_server(name: str, body: MCPServerPatch):
    """Partial in-place edit of an existing MCP server.

    Loads the existing dict, overlays only the fields the client sent,
    then writes back. ``404`` if the server is unknown; ``400`` if the
    overlay produces an invalid configuration.
    """
    existing = find_server(name)
    if existing is None:
        raise HTTPException(404, f"MCP server not found: {name}")
    patch = body.model_dump(exclude_unset=True)
    merged = {**existing, **patch}
    merged["name"] = name  # immutable
    try:
        upsert_server(merged)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"status": "saved", "name": name, "server": merged}


@router.delete("/mcp/{name}", dependencies=[Depends(verify_admin_token)])
async def remove_mcp_server(name: str):
    if not delete_server(name):
        raise HTTPException(404, f"MCP server not found: {name}")
    return {"status": "removed", "name": name}


@router.post(
    "/mcp/{name}/test",
    response_model=MCPTestResult,
    dependencies=[Depends(verify_admin_token)],
)
async def test_mcp_server(name: str) -> MCPTestResult:
    """Probe the server: connect, list tools, disconnect.

    Hard timeout of 20 seconds. The MCP SDK is an optional dep — if
    it's missing we return ``ok=False`` with a clear error rather than
    500-ing.
    """
    server = find_server(name)
    if server is None:
        raise HTTPException(404, f"MCP server not found: {name}")
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(_probe_server(server), timeout=20.0)
    except asyncio.TimeoutError:
        return MCPTestResult(
            ok=False,
            error="probe timed out after 20s",
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as e:
        return MCPTestResult(
            ok=False,
            error=f"{type(e).__name__}: {e}",
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )
    elapsed = int((time.monotonic() - start) * 1000)
    return MCPTestResult(ok=True, tool_count=result["tool_count"], elapsed_ms=elapsed)


@router.get("/mcp/{name}/usage", response_model=list[CreatureRef])
async def mcp_server_usage(name: str) -> list[CreatureRef]:
    """List installed creatures + terrariums that reference this server.

    Scans configs from the catalog roots; missing reads are silently
    skipped (the catalog tier already exposes a richer surface).
    """
    refs = await asyncio.to_thread(find_creatures_using_server, name)
    return [CreatureRef(**r) for r in refs]


async def _probe_server(server: dict[str, Any]) -> dict[str, Any]:
    """Connect to the server, capture its advertised tool list, disconnect.

    The MCP SDK itself is optional, but :mod:`kohakuterrarium.mcp.client`
    defers the SDK import to the connect() call, so importing the
    manager class is cheap and safe — no top-of-file optional-dep
    weirdness is needed here.
    """
    # Drop keys ``MCPServerConfig`` doesn't accept; the registry dict
    # may carry extra metadata we don't want to forward.
    allowed = {
        "name",
        "transport",
        "command",
        "args",
        "env",
        "url",
        "connect_timeout",
    }
    cfg = MCPServerConfig(**{k: v for k, v in server.items() if k in allowed})
    mgr = MCPClientManager()
    try:
        info = await mgr.connect(cfg)
        return {"tool_count": len(info.tools or [])}
    finally:
        try:
            await mgr.shutdown()
        except Exception:  # pragma: no cover - defensive
            pass
