"""Expose the read-only bundled registry of known remote package sources."""

from fastapi import APIRouter

from kohakuterrarium.studio.catalog.packages_remote import load_remote_registry

router = APIRouter()


@router.get("")
async def list_remote():
    """List remote repositories declared in the bundled registry."""
    return load_remote_registry()
