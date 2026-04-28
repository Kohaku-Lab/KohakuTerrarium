"""Identity MCP — MCP server registry."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kohakuterrarium.studio.identity.mcp_servers import (
    delete_server,
    load_servers,
    upsert_server,
)

router = APIRouter()


class MCPServerRequest(BaseModel):
    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = []
    env: dict[str, str] = {}
    url: str = ""
    connect_timeout: float | None = None


@router.get("/mcp")
async def list_mcp_servers():
    return {"servers": load_servers()}


@router.post("/mcp")
async def add_mcp_server(req: MCPServerRequest):
    try:
        upsert_server(req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"status": "saved", "name": req.name}


@router.delete("/mcp/{name}")
async def remove_mcp_server(name: str):
    if not delete_server(name):
        raise HTTPException(404, f"MCP server not found: {name}")
    return {"status": "removed", "name": name}
