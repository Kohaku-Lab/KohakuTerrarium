"""Catalog packages — list / install / uninstall / browse.

Replaces the legacy ``api.routes.registry`` module: every reader
projects from the canonical
``studio.catalog.packages_scan.scan_catalog`` and every operation
delegates to ``studio.catalog.packages``.

Mounted twice by ``api/app.py``:
- ``/api/catalog/packages`` (new canonical prefix)
- ``/api/registry``         (legacy URL preservation; same router)
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.studio.catalog.packages import (
    install_package_op,
    uninstall_package_op,
)
from kohakuterrarium.studio.catalog.packages_scan import scan_catalog
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


class InstallRequest(BaseModel):
    """Request body for installing a package."""

    url: str
    name: str | None = None


class UninstallRequest(BaseModel):
    """Request body for uninstalling a package."""

    name: str


@router.get("")
async def list_local():
    """List all locally available creature and terrarium configs with details.

    Off-loaded to the shared I/O executor — ``scan_catalog`` walks the
    packages dir and reads metadata for every installed entry.
    """
    entries = await run_in_io_executor(scan_catalog)
    return [entry.as_registry_dict() for entry in entries]


@router.post("/install")
async def install(req: InstallRequest):
    """Install a package from a git URL."""
    try:
        name = await run_in_io_executor(
            install_package_op, source=req.url, name=req.name
        )
        return {"status": "installed", "name": name}
    except Exception as e:
        logger.error("Install failed", url=req.url, error=str(e))
        raise HTTPException(400, f"Install failed: {e}")


@router.post("/uninstall")
async def uninstall(req: UninstallRequest):
    """Uninstall a package by name."""
    removed = await run_in_io_executor(uninstall_package_op, req.name)
    if not removed:
        raise HTTPException(404, f"Package not found: {req.name}")
    return {"status": "uninstalled", "name": req.name}
