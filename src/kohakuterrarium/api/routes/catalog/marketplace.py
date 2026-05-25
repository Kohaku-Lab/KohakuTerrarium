"""Catalog marketplace — read-only browse + admin-gated source mgmt + install.

Wraps :mod:`kohakuterrarium.packages.marketplace` for the frontend.
Source-list reads are public; mutating routes (add/remove sources,
install) require the L3 admin token when L3 is enabled.

``POST /install`` resolves the spec then delegates to the existing
``install_package_op`` (which is what ``/api/catalog/packages/install``
already calls), so the WebSocket-streaming install UX from topic 05
is reused unchanged.

Mounted by ``api/app.py`` at ``/api/catalog/marketplace``.
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


# ──────────────────────────────────────────────────────────────────
# Schemas
# ──────────────────────────────────────────────────────────────────


class AddSourceRequest(BaseModel):
    url: str
    alias: str | None = None


class InstallSpecRequest(BaseModel):
    spec: str
    name: str | None = None
    # Editable installs apply only to local paths.  Marketplace specs
    # raise ValueError → 400 here (mirroring ``install_package_spec``).
    editable: bool = False


# ──────────────────────────────────────────────────────────────────
# Projection
# ──────────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────────
# Reads
# ──────────────────────────────────────────────────────────────────


@router.get("/packages")
async def list_packages() -> dict[str, Any]:
    """List every package across all configured marketplace sources.

    Routes through :func:`marketplace.search` (with no filter) so the
    user-facing first-source-wins dedup applies — the frontend card
    grid otherwise would render shadowed duplicates as separate
    rows.  The detail route below calls :func:`marketplace.resolve`
    against the un-deduped raw list so explicit ``@source/name``
    resolution still works.
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
    """Force cache bust + re-fetch every source.

    Admin-gated when L3 is on: refresh triggers an outbound network
    round-trip and mutates the on-disk cache.  An anonymous caller
    could otherwise DoS the upstream by spamming this route on a
    multi-user host; the gate matches the other state-mutating
    routes (sources + install).
    """
    try:
        entries = await marketplace.fetch_marketplace(force=True)
    except MarketplaceUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"ok": True, "packages": len(entries)}


# ──────────────────────────────────────────────────────────────────
# Source management
# ──────────────────────────────────────────────────────────────────


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
    """Remove a source by URL or alias (passed via ``?target=...``).

    The query-param shape (rather than a path param) is deliberate:
    URL sources contain slashes that FastAPI's path-param routing
    does not handle cleanly even with ``{target:path}``.  Aliases
    work either way; URLs only work via query.  Frontend always
    sends ``target=`` so both cases route through the same shape.
    """
    if not marketplace.remove_source(target):
        raise HTTPException(404, f"No source matches {target!r}")
    return {"sources": [s.to_dict() for s in marketplace.list_sources()]}


# ──────────────────────────────────────────────────────────────────
# Install
# ──────────────────────────────────────────────────────────────────


@router.post("/install", dependencies=[Depends(verify_admin_token)])
async def install_by_spec(req: InstallSpecRequest) -> dict[str, Any]:
    """Resolve ``@name`` spec then install (delegates to install_package_op).

    For streaming progress, callers should use the existing WebSocket
    endpoint on ``/api/registry/install`` instead — this REST route
    blocks until the install completes (or fails).
    """
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
        # ``install_package_spec`` raises ValueError when ``editable``
        # is requested on a marketplace spec (git clones can't be -e).
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.error("Marketplace install failed", spec=spec, error=str(exc))
        raise HTTPException(500, f"Install failed: {exc}") from exc
    return {"status": "installed", "name": name, "spec": spec}
