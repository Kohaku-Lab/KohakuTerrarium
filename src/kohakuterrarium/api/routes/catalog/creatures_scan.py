"""Discover creature configs under application-configured base directories."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.api.deps import get_service
from kohakuterrarium.studio.catalog.packages_scan import (
    dedupe_dirs,
    scan_creatures_in_dirs,
)

router = APIRouter()

# Startup configuration remains replaceable so rescans can change search roots.
_creatures_dirs: list[Path] = []


def set_creatures_dirs(creatures: list[str]) -> None:
    """Replace scan roots after resolving and deduplicating absolute paths."""
    global _creatures_dirs
    _creatures_dirs = dedupe_dirs(creatures)


@router.get("")
async def list_creature_configs(
    on_node: str = "_host", service: Any = Depends(get_service)
):
    """Discover configurations on the selected execution node."""
    if on_node and on_node != "_host":
        host = getattr(service, "host", None)
        nodes = service.connected_nodes() if hasattr(service, "connected_nodes") else ()
        if host is None or on_node not in nodes:
            raise HTTPException(404, f"Node {on_node!r} is not connected")
        try:
            response = await host.request(
                to_node=on_node,
                namespace="studio.catalog",
                type="creatures",
                body={},
                timeout=10.0,
            )
        except Exception as exc:
            raise HTTPException(
                502, f"Cannot load creature configs from {on_node}: {exc}"
            ) from exc
        if not isinstance(response, dict) or not isinstance(
            response.get("creatures"), list
        ):
            error = response.get("error", {}) if isinstance(response, dict) else {}
            message = (
                error.get("message", "Invalid catalog response")
                if isinstance(error, dict)
                else str(error)
            )
            raise HTTPException(
                502, f"Cannot load creature configs from {on_node}: {message}"
            )
        return response["creatures"]
    return await run_in_io_executor(scan_creatures_in_dirs, _creatures_dirs)
