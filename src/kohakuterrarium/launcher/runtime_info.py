"""Describe the managed release trees for display."""

from typing import Any

from kohakuterrarium.launcher.paths import runtime_dir
from kohakuterrarium.launcher.tree_ops import (
    ActivePointer,
    list_installed_versions,
    read_active_pointer,
)
from kohakuterrarium.launcher.update_runner import bundled_version
from kohakuterrarium.launcher.versions import is_newer


def _public(pointer: ActivePointer) -> dict[str, str]:
    return {
        "version": pointer.version,
        "build_id": pointer.build_id,
        "installed_at": pointer.installed_at,
    }


def describe() -> dict[str, Any]:
    """Return the active release, every installed one and the release shipped with the shell.

    ``bundled_newer`` is true when the shipped release would replace the active
    one at the next start.
    """
    active = read_active_pointer()
    shipped = bundled_version()
    return {
        "active": _public(active) if active is not None else None,
        "installed": [
            {
                **_public(pointer),
                "active": active is not None and pointer.version == active.version,
            }
            for pointer in list_installed_versions()
        ],
        "bundled": shipped,
        "bundled_newer": shipped is not None
        and (active is None or is_newer(shipped, active.version)),
        "runtime_dir": str(runtime_dir()),
    }
