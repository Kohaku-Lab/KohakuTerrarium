"""Expose marketplace browsing with admin-gated source and install mutations.

Reads remain public, while source changes, refreshes, and installs require the
configured admin token because they mutate local state or perform outbound work.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api._io_executor import run_in_io_executor
from kohakuterrarium.api.auth import verify_admin_token
from kohakuterrarium.packages import marketplace
from kohakuterrarium.packages.marketplace_types import (
    IncompatibleFrameworkError,
    InvalidSpecError,
    MarketplaceEntry,
    MarketplaceNotFoundError,
    MarketplaceUnavailableError,
)
from kohakuterrarium.studio.catalog.packages import install_package_op
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()


class AddSourceRequest(BaseModel):
    url: str
    alias: str | None = None


class InstallSpecRequest(BaseModel):
    spec: str
    name: str | None = None
    # Marketplace specs cannot be editable because they resolve to git clones.
    editable: bool = False


def _entry_to_dict(entry: MarketplaceEntry) -> dict[str, Any]:
    return {
        "name": entry.name,
        "repo": entry.repo,
        "description": entry.description,
        "tags": list(entry.tags),
        "author": entry.author,
        "license": entry.license,
        "framework": entry.framework,
        "homepage": entry.homepage,
        "source_alias": entry.source_alias,
        "source_url": entry.source_url,
        "versions": [
            {
                "tag": v.tag,
                "released": v.released,
                "framework": v.framework,
                "notes": v.notes,
                "notes_url": v.notes_url,
                "yanked": v.yanked,
                "commit": v.commit,
            }
            for v in entry.versions
        ],
    }


@router.get("/packages")
async def list_packages() -> dict[str, Any]:
    """List packages using first-source-wins deduplication.

    Detail resolution still uses the raw source set so explicit ``@source/name``
    specifications can select shadowed entries.
    """
    try:
        entries = await marketplace.search()
    except MarketplaceUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "packages": [_entry_to_dict(e) for e in entries],
        "sources": [s.to_dict() for s in marketplace.list_sources()],
    }


@router.get("/packages/{name}")
async def get_package(name: str) -> dict[str, Any]:
    """Detail view for a single package (resolves to newest non-yanked version)."""
    try:
        entry, version = await marketplace.resolve(f"@{name}")
    except (MarketplaceNotFoundError, IncompatibleFrameworkError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except MarketplaceUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "entry": _entry_to_dict(entry),
        "resolved_version": version.tag,
    }


@router.get("/search")
async def search(
    q: str = "", tag: str | None = None, author: str | None = None
) -> dict[str, Any]:
    """Substring + tag + author filter."""
    try:
        results = await marketplace.search(q, tag=tag, author=author)
    except MarketplaceUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"packages": [_entry_to_dict(e) for e in results]}


@router.post("/refresh", dependencies=[Depends(verify_admin_token)])
async def refresh() -> dict[str, Any]:
    """Refetch every source and replace the on-disk cache.

    Admin gating prevents anonymous callers from repeatedly forcing outbound work.
    """
    try:
        entries = await marketplace.fetch_marketplace(force=True)
    except MarketplaceUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"ok": True, "packages": len(entries)}


@router.get("/sources")
async def get_sources() -> dict[str, Any]:
    """Configured source list (in lookup order)."""
    return {"sources": [s.to_dict() for s in marketplace.list_sources()]}


@router.post("/sources", dependencies=[Depends(verify_admin_token)])
async def add_source(req: AddSourceRequest) -> dict[str, Any]:
    try:
        added = marketplace.add_source(req.url, alias=req.alias)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "added": added.to_dict(),
        "sources": [s.to_dict() for s in marketplace.list_sources()],
    }


@router.delete("/sources", dependencies=[Depends(verify_admin_token)])
async def remove_source(target: str) -> dict[str, Any]:
    """Remove a source by URL or alias supplied as a query parameter.

    URLs contain slashes, so a query parameter handles both URLs and aliases without
    ambiguous path routing.
    """
    if not marketplace.remove_source(target):
        raise HTTPException(404, f"No source matches {target!r}")
    return {"sources": [s.to_dict() for s in marketplace.list_sources()]}


@router.post("/install", dependencies=[Depends(verify_admin_token)])
async def install_by_spec(req: InstallSpecRequest) -> dict[str, Any]:
    """Resolve and install a package spec, blocking until completion."""
    spec = req.spec.strip()
    if not spec:
        raise HTTPException(400, "spec is required")
    try:
        name = await run_in_io_executor(
            install_package_op,
            source=spec,
            name=req.name,
            editable=req.editable,
        )
    except (MarketplaceNotFoundError, InvalidSpecError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except IncompatibleFrameworkError as exc:
        raise HTTPException(409, str(exc)) from exc
    except MarketplaceUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        # Editable mode is valid only for local paths, not resolved marketplace clones.
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.error("Marketplace install failed", spec=spec, error=str(exc))
        raise HTTPException(500, f"Install failed: {exc}") from exc
    return {"status": "installed", "name": name, "spec": spec}
