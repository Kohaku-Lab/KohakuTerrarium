"""Catalog terrariums-scan — config discovery in configured base dirs.

Replaces ``api.routes.configs.list_terrarium_configs``.
"""

from pathlib import Path

from fastapi import APIRouter

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
    """List available terrarium configs from configured directories."""
    return scan_terrariums_in_dirs(_terrariums_dirs)
