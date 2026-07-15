"""Discover creature configs under application-configured base directories."""

from pathlib import Path

from fastapi import APIRouter

from kohakuterrarium.api._io_executor import run_in_io_executor
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
async def list_creature_configs():
    """Scan configured roots without blocking the event loop on filesystem I/O."""
    return await run_in_io_executor(scan_creatures_in_dirs, _creatures_dirs)
