"""Legacy detection + one-shot cleanup.

Two kinds of legacy we may encounter on startup:

1. **06 layout** — ``~/.kohakuterrarium/runtime/venv/`` from the old
   pip+venv-based launcher. The 06 launcher couldn't actually run on
   the briefcase shell (missing ``venv`` module), so any venv on disk
   was either built by a dev install or left dangling. Either way, the
   06b launcher doesn't read it; we wipe it on first encounter.

2. **Pre-launcher legacy bundle** — a frozen briefcase artifact from
   before the wrapper existed at all. Detected via the
   ``app_packages`` substring in ``sys.executable``.

Both probes are stateless. The wipe action is one-shot and idempotent.
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
    """True when ``runtime/venv/`` (06 layout) exists on disk."""
    return legacy_venv_dir().is_dir()


def wipe_legacy_venv() -> Path | None:
    """Delete the 06-layout venv if present.

    Idempotent — returns the path that was wiped, or ``None`` if
    nothing was there. Safe to call on every launcher startup.
    """
    if not legacy_venv_present():
        return None
    log = get_logger()
    target = legacy_venv_dir()
    log.info("migration: wiping legacy 06 venv at %s", target)
    shutil.rmtree(target, ignore_errors=True)
    return target


def is_launcher_install() -> bool:
    """True when the running framework process was exec'd by the launcher.

    Heuristic: a valid ``active`` pointer file exists AND
    ``sys.executable``'s ancestor includes ``runtime/versions/``. The
    framework's API layer uses this to gate ``/api/app/*`` (which is
    meaningless for dev installs or laboratory worker nodes).
    """
    if not active_pointer_path().is_file():
        return False
    exe = Path(sys.executable).resolve()
    for ancestor in exe.parents:
        if ancestor.name == "versions" and ancestor.parent.name == "runtime":
            return True
    return False


def is_legacy_bundle() -> bool:
    """Pre-launcher frozen briefcase bundle (no wrapper at all)."""
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
