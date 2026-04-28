"""Request-scoped dependencies for catalog routes.

Tracks a single active workspace per process (local-only single-user
assumption for v1). ``set_workspace`` is called from the CLI / tests;
``get_workspace`` is the FastAPI dependency used by every catalog
route that needs filesystem access.
"""

from fastapi import HTTPException

from kohakuterrarium.studio.editors.workspace_manifest import Workspace

_active: Workspace | None = None


def set_workspace(ws: Workspace | None) -> None:
    """Set (or clear) the process-wide active workspace."""
    global _active
    _active = ws


def get_workspace() -> Workspace:
    """Return the active workspace or raise 409 if none is open."""
    if _active is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "no_workspace",
                "message": "No workspace open. POST /api/studio/workspace/open first.",
            },
        )
    return _active


def get_workspace_optional() -> Workspace | None:
    """Return the active workspace or None — for routes that still
    work without one (catalog listing, package browsing, etc.)."""
    return _active
