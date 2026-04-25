"""Low-level package-location helpers shared by package modules.

This module intentionally contains only constants and filesystem lookup
primitives. Higher-level package management (`packages.py`) and manifest-slot
resolvers (`packages_manifest.py`) both depend on it, avoiding a cycle between
those two public modules.
"""

import sys
from pathlib import Path

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

PACKAGES_DIR = Path.home() / ".kohakuterrarium" / "packages"
LINK_SUFFIX = ".link"


def _packages_dir() -> Path:
    """Return the active packages directory.

    Tests and legacy callers historically monkeypatch
    ``kohakuterrarium.packages.PACKAGES_DIR``. Consult that public module
    when present so the new split storage layer remains compatible.
    """
    pkg_mod = sys.modules.get("kohakuterrarium.packages")
    if pkg_mod is not None:
        current = getattr(pkg_mod, "PACKAGES_DIR", PACKAGES_DIR)
        if isinstance(current, Path):
            return current
        return Path(current)
    return PACKAGES_DIR


def read_link(name: str) -> Path | None:
    """Read a package ``.link`` pointer file and return the target path."""
    link_file = _packages_dir() / f"{name}{LINK_SUFFIX}"
    if not link_file.exists():
        return None
    target = Path(link_file.read_text(encoding="utf-8").strip())
    if target.is_dir():
        return target
    logger.warning("Link target missing", package=name, target=str(target))
    return None


def write_link(name: str, target: Path) -> None:
    """Write a package ``.link`` pointer file."""
    link_file = _packages_dir() / f"{name}{LINK_SUFFIX}"
    link_file.write_text(str(target.resolve()), encoding="utf-8")


def remove_link(name: str) -> bool:
    """Remove a package ``.link`` pointer file if it exists."""
    link_file = _packages_dir() / f"{name}{LINK_SUFFIX}"
    if link_file.exists():
        link_file.unlink()
        return True
    return False


def get_package_root(name: str) -> Path | None:
    """Get the real root directory of an installed package.

    Checks, in order:

    1. ``.link`` pointer file for editable installs.
    2. Direct directory under :data:`PACKAGES_DIR`.
    3. Legacy symlink under :data:`PACKAGES_DIR`.
    """
    link_target = read_link(name)
    if link_target is not None:
        return link_target

    pkg_dir = _packages_dir() / name
    if pkg_dir.is_dir():
        return pkg_dir.resolve()

    if pkg_dir.is_symlink():
        real = pkg_dir.resolve()
        if real.is_dir():
            return real

    return None
