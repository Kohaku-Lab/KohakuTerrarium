"""Manage, probe, and inspect usage of registered MCP servers."""

import asyncio
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.auth import verify_admin_token
from kohakuterrarium.errors import MCPError
from kohakuterrarium.mcp.client import (
    DEFAULT_MCP_CONNECT_TIMEOUT,
    MCPClientManager,
    MCPServerConfig,
)
from kohakuterrarium.mcp.diagnose import leaf_exceptions
from kohakuterrarium.studio.identity.mcp.servers import (
    delete_server,
    find_server,
    load_servers,
    upsert_server,
)
from kohakuterrarium.studio.identity.mcp.usage import find_creatures_using_server

router = APIRouter()

_PROBE_GRACE_SECONDS = 5.0


class MCPServerRequest(BaseModel):
    """Describe a complete MCP server registration."""

    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    url: str = ""
    headers: dict[str, str] = {}
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
    headers: dict[str, str] | None = None
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
    """Connect, list advertised tools, and disconnect.

    The connect uses the server's ``connect_timeout`` (20 seconds when
    unset). Failures are returned as ``ok=False`` with the root cause
    rather than as server errors.
    """
    server = find_server(name)
    if server is None:
        raise HTTPException(404, f"MCP server not found: {name}")
    limit = _probe_timeout(server) + _PROBE_GRACE_SECONDS
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(_probe_server(server), timeout=limit)
    except MCPError as e:
        return MCPTestResult(
            ok=False,
            error=str(e),
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )
    except asyncio.TimeoutError:
        return MCPTestResult(
            ok=False,
            error=f"probe timed out after {limit:g}s",
            elapsed_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as e:
        return MCPTestResult(
            ok=False,
            error=_probe_error_text(e),
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

    ``${VAR}`` markers are expanded for the connection only, and shutdown
    is attempted for every outcome.
    """
    cfg = MCPServerConfig.from_entry(server)
    cfg.connect_timeout = _probe_timeout(server)
    mgr = MCPClientManager()
    try:
        info = await mgr.connect(cfg)
        return {"tool_count": len(info.tools or [])}
    finally:
        try:
            await mgr.shutdown()
        except Exception:  # pragma: no cover - defensive
            pass


def _probe_timeout(server: dict[str, Any]) -> float:
    """Return the entry's positive ``connect_timeout`` or the default."""
    try:
        value = float(server.get("connect_timeout") or 0)
    except (TypeError, ValueError):
        value = 0.0
    return value if value > 0 else float(DEFAULT_MCP_CONNECT_TIMEOUT)


def _probe_error_text(error: BaseException) -> str:
    """Render a probe failure as its root cause, typed MCP errors verbatim."""
    if isinstance(error, MCPError):
        return str(error)
    root = leaf_exceptions(error)[0]
    if isinstance(root, MCPError):
        return str(root)
    return f"{type(root).__name__}: {root}" if str(root) else type(root).__name__
