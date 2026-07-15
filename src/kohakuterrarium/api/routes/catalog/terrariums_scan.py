"""Discover terrarium configs under application-configured base directories."""

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
    """Replace scan roots after resolving and deduplicating absolute paths."""
    global _terrariums_dirs
    _terrariums_dirs = dedupe_dirs(terrariums)


@router.get("")
async def list_terrarium_configs():
    """Scan configured roots without blocking the event loop on filesystem I/O."""
    return await run_in_io_executor(scan_terrariums_in_dirs, _terrariums_dirs)
