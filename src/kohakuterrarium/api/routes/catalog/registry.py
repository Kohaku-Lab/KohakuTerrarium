"""Catalog registry — bundled ``registry.json`` + future remote sources.

Replaces ``api.routes.registry.list_remote``. Reader-only — install
and uninstall live in ``catalog.packages`` because they share the
operation layer.
"""

from fastapi import APIRouter

from kohakuterrarium.studio.catalog.packages_remote import load_remote_registry

router = APIRouter()


@router.get("")
async def list_remote():
    """List known remote repos from the bundled registry.json."""
    return load_remote_registry()
