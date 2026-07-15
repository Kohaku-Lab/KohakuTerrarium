"""Detect obsolete launcher layouts and remove legacy runtime state.

The migration probes distinguish the retired managed-venv layout from frozen
Briefcase bundles that predate the launcher. Cleanup is idempotent and does not
alter active versioned releases.
"""

import shutil
import sys
from pathlib import Path

from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.paths import (
    active_pointer_path,
    legacy_venv_dir,
)


def legacy_venv_present() -> bool:
    """Return whether the obsolete managed-venv directory exists."""
    return legacy_venv_dir().is_dir()


def wipe_legacy_venv() -> Path | None:
    """Idempotently remove the obsolete venv and return its path if found."""
    if not legacy_venv_present():
        return None
    log = get_logger()
    target = legacy_venv_dir()
    log.info("migration: wiping legacy 06 venv at %s", target)
    shutil.rmtree(target, ignore_errors=True)
    return target


def is_launcher_install() -> bool:
    """Return whether the current process appears to use a managed release.

    Detection requires both an active pointer and an executable located beneath
    the launcher runtime's versions directory.
    """
    if not active_pointer_path().is_file():
        return False
    exe = Path(sys.executable).resolve()
    for ancestor in exe.parents:
        if ancestor.name == "versions" and ancestor.parent.name == "runtime":
            return True
    return False


def is_legacy_bundle() -> bool:
    """Return whether the process appears to be a pre-launcher frozen bundle."""
    if is_launcher_install():
        return False
    exe = sys.executable.replace("\\", "/").lower()
    return "/app_packages/" in exe or exe.endswith("/app_packages")


__all__ = [
    "legacy_venv_present",
    "wipe_legacy_venv",
    "is_launcher_install",
    "is_legacy_bundle",
]
