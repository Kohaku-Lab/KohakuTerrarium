"""Per-version tree primitives — side-by-side installs + pointer file.

Replaces the old ``venv_ops.py`` (which created venvs via stdlib
``venv``, broken on briefcase shells that strip it). The new model:
every installed version lives at ``runtime/versions/<X.Y.Z>/`` and the
``runtime/active`` pointer file names the one to launch.

The pointer file is a JSON dict::

    {"version": "1.5.1", "build_id": "...", "installed_at": "..."}

Atomic ops:

- :func:`write_active_pointer` — tmp+rename, POSIX + Windows atomic.
- :func:`promote_partial` — rename ``X.partial/`` to ``X/`` after smoke.
- :func:`revert_active_pointer` — pick the previous version dir and
  point at it (rollback).

GC: :func:`gc_old_versions` keeps the active + previous + N most-recent
on disk; the rest get ``shutil.rmtree``'d.

Smoke test: :func:`smoke_test_tree` spawns the briefcase-shell python
with PYTHONPATH pointing at ``<dir>/site-packages`` and asserts that
``import kohakuterrarium`` succeeds. The launcher runs this BEFORE the
pointer swap so a broken extract never becomes the live install. No
shim files involved — there's nothing inside the version tree that
needs to be invoked as an executable.
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
    """Anything tree-lifecycle related that should surface to UI."""


# ── Pointer file ────────────────────────────────────────────────────


@dataclass
class ActivePointer:
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


# ── Version directory lifecycle ─────────────────────────────────────


def partial_dir_for(version: str) -> Path:
    return version_dir(f"{version}.partial")


def promote_partial(version: str) -> Path:
    """Rename ``<v>.partial/`` to ``<v>/``. Returns the final path."""
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
    """Idempotently remove a ``<v>.partial/`` dir."""
    p = partial_dir_for(version)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)


def sweep_stale_partials() -> list[str]:
    """Remove every ``*.partial/`` under ``versions/``. Returns the names."""
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


# ── Smoke + swap + rollback ─────────────────────────────────────────


_VERSION_RE = re.compile(
    r"""^__version__\s*=\s*['"]([^'"]+)['"]""",
    re.MULTILINE,
)


def smoke_test_tree(version_root: Path) -> str:
    """Validate the freshly-extracted version tree by file inspection.

    Returns the framework's ``__version__`` from
    ``site-packages/kohakuterrarium/__init__.py``. Raises
    :class:`TreeOpError` when site-packages or kohakuterrarium is
    missing.

    **Why file inspection instead of an import subprocess.** Briefcase
    Windows shells ship a ``python313._pth`` with ``import site``
    disabled, which means ``PYTHONPATH`` doesn't take effect — there's
    no way to point a subprocess at the version tree's site-packages
    from outside the process. And ``sys.executable`` on briefcase is
    the stub exe (``KohakuTerrarium.exe``), which dispatches into the
    framework's CLI parser rather than acting as a plain Python.
    Both make subprocess-based smoke impossible on the briefcase
    target. CI ensures ABI matching at build time; the runtime check
    that genuinely matters here is "did the extract land structurally"
    — present-on-disk verification covers that.

    The version string the function returns is informational; the
    launcher persists it into the active pointer's ``build_id`` slot
    only when the manifest didn't carry one. Failing to parse it is
    not a fatal error.
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
    """Delete old version dirs, retaining ``always_keep`` + ``keep`` most recent.

    Returns the list of version names that were removed.
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


# ── Standalone use by API / CLI ─────────────────────────────────────


def active_install_path() -> Path | None:
    """Return ``versions/<active>/`` if the pointer resolves, else ``None``."""
    ptr = read_active_pointer()
    if ptr is None:
        return None
    candidate = version_dir(ptr.version)
    return candidate if candidate.is_dir() else None


def python_for_active() -> Path:
    """Convenience — the python interpreter to spawn for smoke / probes."""
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
