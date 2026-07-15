"""Manage side-by-side release trees and the active-version pointer.

Installed releases live under ``runtime/versions`` while an atomically replaced
JSON pointer selects the release to launch. Partial directories are promoted
only after structural validation, and rollback and garbage collection operate
without mutating release contents.
"""

import datetime as _dt
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.paths import (
    active_pointer_path,
    python_for,
    site_packages_dir,
    version_dir,
    versions_dir,
)


class TreeOpError(RuntimeError):
    """Report a release-tree lifecycle failure suitable for user display."""


@dataclass
class ActivePointer:
    """Identify an installed release selected by an active pointer."""

    version: str
    build_id: str
    installed_at: str


def read_active_pointer() -> ActivePointer | None:
    """Return the current pointer, or ``None`` if missing / unparseable."""
    p = active_pointer_path()
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    version = raw.get("version")
    if not isinstance(version, str) or not version:
        return None
    return ActivePointer(
        version=version,
        build_id=str(raw.get("build_id") or ""),
        installed_at=str(raw.get("installed_at") or ""),
    )


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def write_active_pointer(version: str, build_id: str = "") -> None:
    """Atomically write the active pointer."""
    p = active_pointer_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": version,
        "build_id": build_id,
        "installed_at": _iso_now(),
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def clear_active_pointer() -> None:
    """Remove the pointer file if present (used by ``reset``)."""
    p = active_pointer_path()
    if p.is_file():
        p.unlink()


def partial_dir_for(version: str) -> Path:
    return version_dir(f"{version}.partial")


def promote_partial(version: str) -> Path:
    """Replace any existing release with its validated partial tree."""
    partial = partial_dir_for(version)
    final = version_dir(version)
    if not partial.is_dir():
        raise TreeOpError(f"no partial dir to promote: {partial}")
    if final.exists():
        shutil.rmtree(final, ignore_errors=True)
    try:
        partial.replace(final)
    except OSError as e:
        raise TreeOpError(f"promote_partial failed: {e}") from e
    return final


def remove_partial(version: str) -> None:
    """Remove a version's partial directory if it exists."""
    p = partial_dir_for(version)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


def sweep_stale_partials() -> list[str]:
    """Remove all partial release directories and return their names."""
    root = versions_dir()
    if not root.is_dir():
        return []
    removed: list[str] = []
    for entry in root.iterdir():
        if entry.is_dir() and entry.name.endswith(".partial"):
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(entry.name)
    return removed


def list_installed_versions() -> list[ActivePointer]:
    """All installed versions (excluding partials), newest install first.

    Each entry's ``installed_at`` is read from the version's own
    ``manifest.json`` if present, else from the directory mtime.
    """
    root = versions_dir()
    if not root.is_dir():
        return []
    out: list[ActivePointer] = []
    for entry in root.iterdir():
        if not entry.is_dir() or entry.name.endswith(".partial"):
            continue
        ptr = _read_version_manifest(entry)
        if ptr is None:
            ptr = ActivePointer(
                version=entry.name,
                build_id="",
                installed_at=_iso_from_mtime(entry),
            )
        out.append(ptr)
    out.sort(key=lambda p: p.installed_at, reverse=True)
    return out


def _read_version_manifest(version_root: Path) -> ActivePointer | None:
    manifest = version_root / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    version = raw.get("version") or version_root.name
    return ActivePointer(
        version=str(version),
        build_id=str(raw.get("build_id") or ""),
        installed_at=str(raw.get("generated_at") or _iso_from_mtime(version_root)),
    )


def _iso_from_mtime(p: Path) -> str:
    try:
        ts = _dt.datetime.fromtimestamp(p.stat().st_mtime, tz=_dt.timezone.utc)
    except OSError:
        return ""
    return ts.isoformat(timespec="seconds")


_VERSION_RE = re.compile(
    r"""^__version__\s*=\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)


def smoke_test_tree(version_root: Path) -> str:
    """Validate an extracted release structurally and return its version.

    The release must contain site-packages and the framework package. Briefcase
    cannot reliably launch an import subprocess because its isolated path file
    ignores ``PYTHONPATH`` and its executable is an application stub, so this
    check inspects package files directly. A missing version assignment returns
    ``<no-version>``; missing or unreadable package structure raises
    :class:`TreeOpError`.
    """
    site = site_packages_dir(version_root)
    if not site.is_dir():
        raise TreeOpError(f"site-packages missing at {site}")
    init = site / "kohakuterrarium" / "__init__.py"
    if not init.is_file():
        raise TreeOpError(f"kohakuterrarium package not found at {init}")
    try:
        text = init.read_text(encoding="utf-8")
    except OSError as e:
        raise TreeOpError(f"could not read {init}: {e}") from e
    match = _VERSION_RE.search(text)
    if match:
        return match.group(1)
    return "<no-version>"


def gc_old_versions(*, keep: int, always_keep: set[str]) -> list[str]:
    """Delete old releases while retaining required and recent versions.

    Return the names of directories removed. ``keep`` counts additional recent
    releases beyond the versions in ``always_keep``.
    """
    log = get_logger()
    installed = list_installed_versions()
    kept = set(always_keep)
    for ptr in installed:
        if len(kept) >= len(always_keep) + keep:
            break
        kept.add(ptr.version)
    removed: list[str] = []
    for ptr in installed:
        if ptr.version in kept:
            continue
        target = version_dir(ptr.version)
        log.info("tree_ops: gc removing %s", target)
        shutil.rmtree(target, ignore_errors=True)
        removed.append(ptr.version)
    return removed


def revert_active_pointer() -> ActivePointer:
    """Find the latest non-active version and point at it.

    Returns the new pointer. Raises :class:`TreeOpError` when there's
    no candidate (only the active version is installed, or none at all).
    """
    current = read_active_pointer()
    candidates = [
        p
        for p in list_installed_versions()
        if p.version != (current.version if current else None)
    ]
    if not candidates:
        raise TreeOpError("no prior version available to roll back to")
    target = candidates[0]
    write_active_pointer(target.version, target.build_id)
    return target


def active_install_path() -> Path | None:
    """Return ``versions/<active>/`` if the pointer resolves, else ``None``."""
    ptr = read_active_pointer()
    if ptr is None:
        return None
    candidate = version_dir(ptr.version)
    return candidate if candidate.is_dir() else None


def python_for_active() -> Path:
    """Return the interpreter path used for active-release probes."""
    return python_for(versions_dir())


__all__ = [
    "TreeOpError",
    "ActivePointer",
    "read_active_pointer",
    "write_active_pointer",
    "clear_active_pointer",
    "partial_dir_for",
    "promote_partial",
    "remove_partial",
    "sweep_stale_partials",
    "list_installed_versions",
    "smoke_test_tree",
    "gc_old_versions",
    "revert_active_pointer",
    "active_install_path",
    "python_for_active",
]
