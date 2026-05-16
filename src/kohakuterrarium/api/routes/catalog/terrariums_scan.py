"""Catalog terrariums-scan — config discovery in configured base dirs.

Replaces ``api.routes.configs.list_terrarium_configs``.
"""

from pathlib import Path

from fastapi import APIRouter

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.studio.catalog.packages_scan import (
    dedupe_dirs,
    scan_terrariums_in_dirs,
)

router = APIRouter()

_terrariums_dirs: list[Path] = []


def set_terrariums_dirs(terrariums: list[str]) -> None:
    """Replace the list of terrarium base directories to scan."""
    global _terrariums_dirs
    _terrariums_dirs = dedupe_dirs(terrariums)


@router.get("")
async def list_terrarium_configs():
    """List available terrarium configs from configured directories.

    Off-loaded to the shared I/O executor — same cold-cache + YAML
    parse cost per entry as the creatures scan; running synchronously
    on the event loop blocked every concurrent request.
    """
    return await run_in_io_executor(scan_terrariums_in_dirs, _terrariums_dirs)
