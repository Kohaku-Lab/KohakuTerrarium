"""Projects: working folders that group saved and running sessions.

``paths`` makes project keys and ids, ``store`` persists saved projects,
``membership`` assigns sessions to projects, ``ops`` is the shared surface
used by ``/api/projects``, the session lists, and ``studio.persistence``.
"""

from kohakuterrarium.studio.persistence.projects.membership import (
    UNASSIGNED,
    ProjectRoot,
    ProjectView,
)
from kohakuterrarium.studio.persistence.projects.ops import (
    add_project,
    annotate_rows,
    build_view,
    forget_project,
    list_projects,
    live_locations,
    session_scope,
    update_project,
)
from kohakuterrarium.studio.persistence.projects.paths import (
    normalize_node,
    normalize_path,
    project_id,
)
from kohakuterrarium.studio.persistence.projects.store import projects_path

__all__ = [
    "UNASSIGNED",
    "ProjectRoot",
    "ProjectView",
    "add_project",
    "annotate_rows",
    "build_view",
    "forget_project",
    "list_projects",
    "live_locations",
    "normalize_node",
    "normalize_path",
    "project_id",
    "projects_path",
    "session_scope",
    "update_project",
]
