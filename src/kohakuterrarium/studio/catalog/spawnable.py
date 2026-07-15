"""Spawnable creature catalog — what a privileged creature can spawn.

Combines workspace and installed-package creature declarations into
``{ref, name, description, source}`` records used by group status and Studio
selection surfaces. The catalog describes all currently reachable creatures;
spawn authorization is enforced elsewhere.
"""

from typing import Any

from kohakuterrarium.packages.walk import list_packages


def list_spawnable_creatures(workspace: Any | None = None) -> list[dict]:
    """Return workspace and package creatures as spawnable references.

    ``workspace`` may be any object exposing ``list_creatures``; when absent,
    only installed package declarations are included.
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
            # Catalog enrichment is best-effort. A later spawn still performs
            # authoritative path resolution and reports a concrete failure.
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
