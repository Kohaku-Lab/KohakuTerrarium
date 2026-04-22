"""Workspace abstraction.

``Workspace`` is the Protocol every studio route talks to — the
seam that lets v2 ship a ``RemoteWorkspace`` without touching
route code. v1 ships ``LocalWorkspace`` only.
"""

from kohakuterrarium.api.studio.workspace.base import Workspace

__all__ = ["Workspace"]
