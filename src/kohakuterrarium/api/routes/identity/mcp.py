"""Manage, probe, and inspect usage of registered MCP servers."""

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
    """Describe a complete MCP server registration."""

    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    url: str = ""
    connect_timeout: float | None = None


class MCPServerPatch(BaseModel):
    """Describe a partial MCP server update.

    Server names are immutable resource identities. Sequence and mapping fields
    replace their existing values in full rather than merging element-wise.
    """

    transport: Literal["stdio", "http"] | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    connect_timeout: float | None = None


class MCPTestResult(BaseModel):
    """Report MCP connectivity, advertised tool count, and probe duration."""

    ok: bool
    error: str | None = None
    tool_count: int | None = None
    elapsed_ms: int | None = None


class CreatureRef(BaseModel):
    """Identify an installed creature or terrarium that references a server."""

    name: str
    kind: Literal["creature", "terrarium"]
    path: str


@router.get("/mcp")
async def list_mcp_servers():
    """Return all registered MCP server configurations."""
    return {"servers": load_servers()}


@router.post("/mcp", dependencies=[Depends(verify_admin_token)])
async def add_mcp_server(req: MCPServerRequest):
    """Validate and persist a complete MCP server registration."""
    try:
        upsert_server(req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"status": "saved", "name": req.name}


@router.patch("/mcp/{name}", dependencies=[Depends(verify_admin_token)])
async def patch_mcp_server(name: str, body: MCPServerPatch):
    """Overlay explicitly supplied fields onto an existing MCP server.

    Unknown servers return 404, and invalid merged configurations return 400.
    """
    existing = find_server(name)
    if existing is None:
        raise HTTPException(404, f"MCP server not found: {name}")
    patch = body.model_dump(exclude_unset=True)
    merged = {**existing, **patch}
    # The path identity is authoritative even if model behavior changes later.
    merged["name"] = name
    try:
        upsert_server(merged)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"status": "saved", "name": name, "server": merged}


@router.delete("/mcp/{name}", dependencies=[Depends(verify_admin_token)])
async def remove_mcp_server(name: str):
    """Delete a registered MCP server by immutable name."""
    if not delete_server(name):
        raise HTTPException(404, f"MCP server not found: {name}")
    return {"status": "removed", "name": name}


@router.post(
    "/mcp/{name}/test",
    response_model=MCPTestResult,
    dependencies=[Depends(verify_admin_token)],
)
async def test_mcp_server(name: str) -> MCPTestResult:
    """Connect, list advertised tools, and disconnect within 20 seconds.

    Probe and optional-dependency failures are returned as ``ok=False`` results
    rather than server errors so the caller receives diagnostic details.
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
    """List installed creatures and terrariums that reference this server.

    The catalog scan skips unreadable or missing configs because this endpoint
    reports references rather than catalog integrity.
    """
    refs = await asyncio.to_thread(find_creatures_using_server, name)
    return [CreatureRef(**r) for r in refs]


async def _probe_server(server: dict[str, Any]) -> dict[str, Any]:
    """Connect to a registry entry and return its advertised tool count.

    The client defers its optional SDK import until connection, and shutdown is
    attempted for every outcome.
    """
    # Registry entries may carry metadata that is not part of the connection
    # schema, so only constructor-supported fields cross this boundary.
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
