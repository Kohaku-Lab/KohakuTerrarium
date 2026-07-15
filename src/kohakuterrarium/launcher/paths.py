"""Resolve launcher settings, runtime, release, and bundle paths.

Helpers are side-effect free and derive their results at call time so tests and
embedded hosts can redirect configuration through ``KT_CONFIG_DIR``. Briefcase
bundle probing returns the first candidate containing a release archive.
"""

import os
import sys
from pathlib import Path


def config_home() -> Path:
    """Return the shared launcher and framework configuration directory."""
    env = os.environ.get("KT_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".kohakuterrarium"


def runtime_dir() -> Path:
    """Return the directory containing managed releases and launcher state."""
    return config_home() / "runtime"


def versions_dir() -> Path:
    """Return the parent directory for side-by-side release trees."""
    return runtime_dir() / "versions"


def version_dir(name: str) -> Path:
    """Tree for a specific version (e.g. ``"1.5.1"`` or ``"1.5.2.partial"``)."""
    return versions_dir() / name


def active_pointer_path() -> Path:
    """Pointer file resolved by the bootloader on every launch."""
    return runtime_dir() / "active"


def manifest_cache_dir() -> Path:
    """Where last-fetched channel manifests + ETag metadata live."""
    return runtime_dir() / "manifest-cache"


def settings_path() -> Path:
    """Path to the ``app-settings.json`` file."""
    return config_home() / "app-settings.json"


def lock_path() -> Path:
    """Path to the update-flock file."""
    return runtime_dir() / ".update.lock"


def legacy_venv_dir() -> Path:
    """Return the obsolete managed-venv path used for migration cleanup."""
    return runtime_dir() / "venv"


def _candidate_bundled_release_dirs() -> list[Path]:
    """Return offline-release locations in first-match precedence order.

    Candidates cover the Briefcase application layout, executable-relative
    Windows and legacy macOS layouts, and the repository development layout.
    """
    here = Path(__file__).resolve()
    exe = Path(sys.executable)
    return [
        here.parent.parent.parent / "bundled-release",
        exe.parent / "bundled-release",
        exe.parent.parent / "bundled-release",
        here.parents[3] / "bundled-release",
    ]


def _has_release_tarball(path: Path) -> bool:
    """Return whether a directory contains a framework release archive."""
    if not path.is_dir():
        return False
    return any(path.glob("kohakuterrarium-*.tar.*"))


def bundled_release_dir() -> Path | None:
    """Return the first valid bundled-release directory, if one exists."""
    for candidate in _candidate_bundled_release_dirs():
        if _has_release_tarball(candidate):
            return candidate
    return None


def site_packages_dir(version_root: Path) -> Path:
    """Return the release's flat site-packages directory used at handoff."""
    return version_root / "site-packages"


def manifest_path(version_root: Path) -> Path:
    """The ``manifest.json`` describing the version (version / abi / sha)."""
    return version_root / "manifest.json"


def python_for(version_root: Path) -> Path:
    """Return the ABI-matched interpreter used for a release.

    Releases currently share the embedding interpreter, so ``version_root`` is
    accepted for API symmetry but does not affect the result.
    """
    _ = version_root
    return Path(sys.executable)


__all__ = [
    "config_home",
    "runtime_dir",
    "versions_dir",
    "version_dir",
    "active_pointer_path",
    "manifest_cache_dir",
    "settings_path",
    "lock_path",
    "legacy_venv_dir",
    "bundled_release_dir",
    "_candidate_bundled_release_dirs",
    "_has_release_tarball",
    "site_packages_dir",
    "manifest_path",
    "python_for",
]
