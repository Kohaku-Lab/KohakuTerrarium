"""Project operations shared by the HTTP routes and the Studio façade.

Each operation reads the session index once (``location_counts``), loads the
saved projects of the given L4 user (``None`` = shared file), and returns API
objects (see :meth:`ProjectRoot.to_public`).
"""

import os
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from kohakuterrarium.errors import (
    InvalidRequestError,
    NotFoundError,
    ProjectsFileError,
)
from kohakuterrarium.studio.persistence.projects.membership import (
    Location,
    ProjectRoot,
    ProjectView,
)
from kohakuterrarium.studio.persistence.projects.paths import (
    LOCAL_NODE,
    normalize_node,
    normalize_path,
    project_id,
    public_node,
)
from kohakuterrarium.studio.persistence.projects.store import (
    load_records,
    update_document,
)
from kohakuterrarium.studio.persistence.session_index.store import SessionIndex
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

LiveLocations = Iterable[tuple[str | None, str | None]]


def live_locations(open_rows: Iterable[dict[str, Any]]) -> list[Location]:
    """Raw ``(node_id, pwd)`` of the running rows of ``/api/sessions/open``."""
    return [
        (str(row.get("node_id") or ""), str(row.get("pwd") or ""))
        for row in open_rows
        if row.get("is_live")
    ]


def build_view(
    index: SessionIndex,
    *,
    user_id: int | None = None,
    live: LiveLocations = (),
    strict: bool = True,
) -> ProjectView:
    """Project membership over every indexed session plus running ones.

    With ``strict=False`` an unreadable projects file is logged and treated
    as empty (found projects only) instead of raising.
    """
    try:
        records = load_records(user_id)
    except ProjectsFileError as exc:
        if strict:
            raise
        logger.warning("Saved projects ignored", error=str(exc), path=exc.path)
        records = []
    return ProjectView(records, index.location_counts(), live)


def list_projects(
    index: SessionIndex,
    *,
    user_id: int | None = None,
    live: LiveLocations = (),
    include_hidden: bool = False,
) -> dict[str, Any]:
    """``{"projects": [...], "unassigned": {...}}`` for the projects list."""
    view = build_view(index, user_id=user_id, live=live)
    return {
        "projects": view.projects(include_hidden=include_hidden),
        "unassigned": view.unassigned.public_counts(),
    }


def _find_record(document: dict[str, Any], pid: str) -> dict[str, Any] | None:
    return next((r for r in document["projects"] if r.get("id") == pid), None)


def _new_record(pid: str, path: str, node: str) -> dict[str, Any]:
    return {
        "id": pid,
        "path": path,
        "node": public_node(node),
        "name": None,
        "pinned": False,
        "hidden": False,
        "added_at": datetime.now(timezone.utc).isoformat(),
    }


def _clean_name(name: Any) -> str | None:
    if name is None:
        return None
    if not isinstance(name, str):
        raise InvalidRequestError("Project name must be text or null")
    return name.strip() or None


def _project_after_write(
    index: SessionIndex, pid: str, user_id: int | None, live: LiveLocations
) -> dict[str, Any]:
    root = build_view(index, user_id=user_id, live=live).get(pid)
    if root is None:
        raise NotFoundError(f"Unknown project: {pid}")
    return root.to_public()


def add_project(
    index: SessionIndex,
    path: str,
    *,
    node: str | None = None,
    name: str | None = None,
    user_id: int | None = None,
    live: LiveLocations = (),
) -> dict[str, Any]:
    """Save the folder ``path`` on ``node`` as a project and return it.

    Adding a folder that is already saved returns that project, renamed when
    ``name`` is given and shown again if it was hidden. A folder on this host
    must be an existing absolute directory.
    """
    node_id = normalize_node(node)
    display, key = normalize_path(str(path or ""), node_id)
    if not key:
        raise InvalidRequestError("A project needs a folder path")
    if node_id == LOCAL_NODE:
        if not os.path.isabs(display):
            raise InvalidRequestError(f"Project folder must be absolute: {display}")
        if not os.path.isdir(display):
            raise InvalidRequestError(f"Folder does not exist on this host: {display}")
    pid = project_id(node_id, key)
    new_name = _clean_name(name)

    def mutate(document: dict[str, Any]) -> None:
        record = _find_record(document, pid)
        if record is None:
            record = _new_record(pid, display, node_id)
            document["projects"].append(record)
        record["hidden"] = False
        if new_name is not None:
            record["name"] = new_name

    update_document(mutate, user_id=user_id)
    return _project_after_write(index, pid, user_id, live)


def update_project(
    index: SessionIndex,
    pid: str,
    changes: dict[str, Any],
    *,
    user_id: int | None = None,
    live: LiveLocations = (),
) -> dict[str, Any]:
    """Apply ``name`` / ``pinned`` / ``hidden`` changes; a found project is saved.

    Only keys present in ``changes`` change; ``name: None`` restores the
    default name (the folder's last segment).
    """
    unknown = sorted(set(changes) - {"name", "pinned", "hidden"})
    if unknown:
        raise InvalidRequestError(f"Unknown project fields: {', '.join(unknown)}")
    root: ProjectRoot | None = build_view(index, user_id=user_id, live=live).get(pid)
    if root is None:
        raise NotFoundError(f"Unknown project: {pid}")
    new_name = _clean_name(changes.get("name"))

    def mutate(document: dict[str, Any]) -> None:
        record = _find_record(document, pid)
        if record is None:
            record = _new_record(pid, root.path, root.node)
            document["projects"].append(record)
        if "name" in changes:
            record["name"] = new_name
        for flag in ("pinned", "hidden"):
            if flag in changes:
                record[flag] = bool(changes[flag])

    update_document(mutate, user_id=user_id)
    return _project_after_write(index, pid, user_id, live)


def forget_project(pid: str, *, user_id: int | None = None) -> dict[str, str]:
    """Remove a saved project record; its sessions are untouched."""

    def mutate(document: dict[str, Any]) -> None:
        if _find_record(document, pid) is None:
            raise NotFoundError(f"No saved project: {pid}")
        document["projects"] = [r for r in document["projects"] if r["id"] != pid]

    update_document(mutate, user_id=user_id)
    return {"status": "forgotten", "id": pid}


def session_scope(
    index: SessionIndex, project: str | None, *, user_id: int | None = None
) -> tuple[ProjectView, set[Location] | None]:
    """The membership view plus the index locations ``project`` selects.

    ``project`` is a project id, ``"none"`` for unassigned sessions, or
    empty for no filter (``None`` locations). An unreadable projects file
    leaves found projects only.
    """
    view = build_view(index, user_id=user_id, strict=False)
    return view, (view.locations_for(project) if project else None)


def annotate_rows(view: ProjectView, rows: Iterable[dict[str, Any]]) -> None:
    """Set ``row["project"]`` to ``{"id", "name"}`` or ``None`` on each row."""
    for row in rows:
        row["project"] = view.ref_for(row.get("node_id"), row.get("pwd"))


__all__ = [
    "add_project",
    "annotate_rows",
    "build_view",
    "forget_project",
    "list_projects",
    "live_locations",
    "session_scope",
    "update_project",
]
