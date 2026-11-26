"""Projects: list, add, change, and forget working-folder projects.

Mounted at ``/api/projects``. Thin adapter over
:mod:`kohakuterrarium.studio.persistence.projects`: the session directory and
the saved-projects file follow the request's L4 user, and running counts come
from the same rows ``GET /api/sessions/open`` returns.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kohakuterrarium.api.auth import User, get_optional_user
from kohakuterrarium.api.deps import get_service, resolve_request_session_dir
from kohakuterrarium.api.routes.persistence._executor import (
    run_in_persistence_executor,
)
from kohakuterrarium.api.routes.persistence.open_sessions import (
    build_open_session_rows,
)
from kohakuterrarium.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    ProjectsFileError,
)
from kohakuterrarium.studio.persistence import projects
from kohakuterrarium.studio.persistence.session_index import (
    get_session_index_default,
)
from kohakuterrarium.terrarium.service.service import TerrariumService

router = APIRouter()


class ProjectCreateRequest(BaseModel):
    """Body of ``POST /api/projects``."""

    path: str
    node: str | None = None
    name: str | None = None


class ProjectPatchRequest(BaseModel):
    """Body of ``PATCH /api/projects/{id}``; only sent fields change."""

    name: str | None = None
    pinned: bool = False
    hidden: bool = False


def _user_id(user: User | None) -> int | None:
    return user.id if user is not None else None


def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a projects operation, mapping its typed errors to HTTP."""
    try:
        return fn(*args, **kwargs)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except InvalidRequestError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectsFileError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _with_live(
    service: TerrariumService,
    session_dir: Path,
    op: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Call ``op(index, *args, live=..., **kwargs)`` for the request's sessions."""
    rows = build_open_session_rows(service, session_dir)
    index = get_session_index_default(session_dir)
    return _call(op, index, *args, live=projects.live_locations(rows), **kwargs)


@router.get("")
async def list_projects(
    include_hidden: bool = False,
    session_dir: Path = Depends(resolve_request_session_dir),
    service: TerrariumService = Depends(get_service),
    user: User | None = Depends(get_optional_user),
):
    """Every project with session counts, plus the unassigned tally."""
    return await run_in_persistence_executor(
        _with_live,
        service,
        session_dir,
        projects.list_projects,
        user_id=_user_id(user),
        include_hidden=include_hidden,
    )


@router.post("")
async def add_project(
    req: ProjectCreateRequest,
    session_dir: Path = Depends(resolve_request_session_dir),
    service: TerrariumService = Depends(get_service),
    user: User | None = Depends(get_optional_user),
):
    """Save a folder as a project; an already saved folder is returned."""
    return await run_in_persistence_executor(
        _with_live,
        service,
        session_dir,
        projects.add_project,
        req.path,
        node=req.node,
        name=req.name,
        user_id=_user_id(user),
    )


@router.patch("/{project_id}")
async def update_project(
    project_id: str,
    req: ProjectPatchRequest,
    session_dir: Path = Depends(resolve_request_session_dir),
    service: TerrariumService = Depends(get_service),
    user: User | None = Depends(get_optional_user),
):
    """Rename, pin, or hide a project; ``name: null`` restores the default."""
    return await run_in_persistence_executor(
        _with_live,
        service,
        session_dir,
        projects.update_project,
        project_id,
        req.model_dump(exclude_unset=True),
        user_id=_user_id(user),
    )


@router.delete("/{project_id}")
async def forget_project(
    project_id: str,
    session_dir: Path = Depends(resolve_request_session_dir),
    user: User | None = Depends(get_optional_user),
):
    """Forget a saved project; its sessions stay and it may reappear as found.

    ``session_dir`` is unused; resolving it applies the same L4 gate as the
    other project routes.
    """
    del session_dir
    return await run_in_persistence_executor(
        _call, projects.forget_project, project_id, user_id=_user_id(user)
    )
