"""Request-scoped dependencies for legacy studio routes.

Delegates to ``api.routes.catalog._deps`` so every route mount shares one
active workspace state. The shim preserves the established Studio import path
for remaining routes and tests.
"""

from kohakuterrarium.api.routes.catalog._deps import (
    get_workspace,
    get_workspace_optional,
    set_workspace,
)
from kohakuterrarium.studio.editors.workspace_manifest import Workspace

__all__ = [
    "Workspace",
    "get_workspace",
    "get_workspace_optional",
    "set_workspace",
]
