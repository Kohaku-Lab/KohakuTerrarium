"""Catalog creatures-scan — config discovery in configured base dirs.

Replaces ``api.routes.configs.list_creature_configs``. The list of
base directories is populated by ``set_config_dirs`` (called once
by ``api.app.create_app``) and shared with
``terrariums_scan.set_config_dirs``.
"""

from pathlib import Path

from fastapi import APIRouter

from kohakuterrarium.studio.catalog.packages_scan import (
    dedupe_dirs,
    scan_creatures_in_dirs,
)

router = APIRouter()

# Configured base dirs. Wired once at startup; mutable so a future
# rescan endpoint can refresh.
_creatures_dirs: list[Path] = []


def set_creatures_dirs(creatures: list[str]) -> None:
    """Replace the list of creature base directories to scan.

    Resolved + deduplicated by absolute path, matching the legacy
    ``api.routes.configs.set_config_dirs`` behavior.
    """
    global _creatures_dirs
    _creatures_dirs = dedupe_dirs(creatures)


@router.get("")
async def list_creature_configs():
    """List available creature configs from configured directories."""
    return scan_creatures_in_dirs(_creatures_dirs)
