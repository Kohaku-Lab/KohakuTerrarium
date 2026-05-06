"""Spawnable creature catalog — what a privileged creature can spawn.

Unions a workspace's local ``creatures/`` folder with every installed
package's ``creatures: [...]`` manifest entries. Returns a list of
``{ref, name, description, source}`` records suitable for inclusion in
``group_status.spawnable[]`` and the Studio UI.

Unrestricted in v1 — every workspace + package creature is reachable.
Allowlist hardening is a follow-up.
"""

from typing import Any

from kohakuterrarium.packages.walk import list_packages


def list_spawnable_creatures(workspace: Any | None = None) -> list[dict]:
    """Return the union of workspace creatures and package creatures.

    ``workspace`` is a :class:`WorkspaceFs` (or any object exposing
    ``list_creatures()``) — when ``None``, only package creatures appear.
    """
    out: list[dict] = []
    if workspace is not None:
        try:
            for c in workspace.list_creatures():
                out.append(
                    {
                        "ref": c.get("path", c.get("name", "")),
                        "name": c.get("name", ""),
                        "description": c.get("description", ""),
                        "source": "workspace",
                    }
                )
        except Exception:
            # Workspace failures shouldn't break tool calls; the model
            # gets an empty list and continues. The actual spawn call
            # surfaces a clean error if the path doesn't resolve.
            pass

    for pkg in list_packages():
        pkg_name = pkg.get("name", "")
        if not pkg_name:
            continue
        for c in pkg.get("creatures", []) or []:
            cname = c.get("name", "") if isinstance(c, dict) else ""
            if not cname:
                continue
            out.append(
                {
                    "ref": f"@{pkg_name}/creatures/{cname}",
                    "name": cname,
                    "description": (
                        c.get("description", "") if isinstance(c, dict) else ""
                    ),
                    "source": "package",
                }
            )
    return out
