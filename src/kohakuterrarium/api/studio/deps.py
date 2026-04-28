"""Request-scoped dependencies for legacy studio routes.

Delegates to ``api.routes.catalog._deps`` so the new and legacy mounts
share a single ``_active`` workspace state. Kept as a thin shim because
the test suite + remaining studio routes (``meta``, ``packages``) still
import these helpers from the legacy path.
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
