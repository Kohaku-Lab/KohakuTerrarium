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

Smoke test: :func:`smoke_test_tree` spawns ``<dir>/scripts/kt --version``
under the briefcase-shell python and checks for exit 0. The launcher
runs this BEFORE the pointer swap so a broken extract never becomes
the live install.
"""

import datetime as _dt
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.paths import (
    active_pointer_path,
    kt_script,
    python_for,
    version_dir,
    versions_dir,
)

SMOKE_TIMEOUT_SECONDS = 30.0


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


def smoke_test_tree(version_root: Path) -> str:
    """Spawn ``<root>/scripts/kt --version`` and verify exit 0.

    Returns the trimmed version string the script printed. Raises
    :class:`TreeOpError` on missing shim, non-zero exit, timeout.
    """
    kt = kt_script(version_root)
    if not kt.is_file():
        raise TreeOpError(f"kt shim missing at {kt}")
    try:
        out = subprocess.run(
            [str(kt), "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=SMOKE_TIMEOUT_SECONDS,
            env=_smoke_env(version_root),
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise TreeOpError(f"smoke `kt --version` failed: {e}") from e
    return (out.stdout or "").strip() or "<no-version>"


def _smoke_env(version_root: Path) -> dict:
    """Build the env for the smoke subprocess.

    Points PYTHONPATH at the version's site-packages so the briefcase
    python finds the framework. Disables user site to keep the smoke
    deterministic across user machines.
    """
    env = os.environ.copy()
    site = version_root / "site-packages"
    existing = env.get("PYTHONPATH", "")
    parts = [str(site)]
    if existing:
        parts.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env["PYTHONNOUSERSITE"] = "1"
    env["KT_LAUNCHER_SMOKE"] = "1"
    return env


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
    "SMOKE_TIMEOUT_SECONDS",
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
