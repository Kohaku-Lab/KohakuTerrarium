"""Manifest IO + validation + dependency installation helpers."""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import yaml

from kohakuterrarium.errors import PackageError
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _force_rmtree(path: Path) -> None:
    """Remove a directory tree, handling read-only files (e.g. .git on Windows)."""

    def _on_error(_func, fpath, _exc_info):
        os.chmod(fpath, stat.S_IWRITE)
        os.unlink(fpath)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_error)
    else:
        shutil.rmtree(path, onerror=_on_error)


def _load_manifest(pkg_dir: Path) -> dict:
    """Load kohaku.yaml manifest from a package directory."""
    manifest_file = pkg_dir / "kohaku.yaml"
    if not manifest_file.exists():
        manifest_file = pkg_dir / "kohaku.yml"
    if not manifest_file.exists():
        return {"name": pkg_dir.name}

    with open(manifest_file, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _validate_package(pkg_dir: Path, name: str) -> None:
    """Basic validation of a package structure.

    A package is valid if it has at least one of: creatures/, terrariums/,
    or manifest entries for tools, plugins, or llm_presets.
    """
    has_creatures = (pkg_dir / "creatures").is_dir()
    has_terrariums = (pkg_dir / "terrariums").is_dir()
    if not has_creatures and not has_terrariums:
        # Check manifest for extension modules
        manifest = _load_manifest(pkg_dir)
        has_tools = bool(manifest.get("tools"))
        has_plugins = bool(manifest.get("plugins"))
        has_presets = bool(manifest.get("llm_presets"))
        if not has_tools and not has_plugins and not has_presets:
            logger.warning(
                "Package has no creatures/, terrariums/, or extension modules",
                package=name,
            )


DEP_POLICIES = ("auto", "never")


def _pip_install(args: list[str], *, what: str) -> None:
    """Run ``<this interpreter> -m pip install <args>``; raise on failure.

    Always targets the running interpreter's environment —
    a bare PATH ``pip`` may belong to a different Python entirely.
    """
    cmd = [sys.executable, "-m", "pip", "install", *args]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or b"").decode(errors="replace")[-500:]
        raise PackageError(
            f"Dependency install failed ({what}). The package files are "
            f"installed but its Python dependencies are not — install them "
            f"manually or re-run with the issue fixed.\npip stderr: {stderr}"
        ) from e


def _install_python_deps(pkg_dir: Path, *, deps: str = "auto") -> None:
    """Install a package's declared Python dependencies.

    Args:
        pkg_dir: Package root holding ``kohaku.yaml`` /
            ``requirements.txt``.
        deps: ``"auto"`` installs ``python_dependencies`` +
            ``requirements.txt`` into the *running interpreter's*
            environment (``sys.executable -m pip``); ``"never"`` skips
            dependency installation entirely.

    Raises:
        PackageError: ``deps`` is not a known policy, or pip failed.
    """
    if deps not in DEP_POLICIES:
        raise PackageError(
            f"Unknown deps policy: {deps!r} (expected one of {DEP_POLICIES})"
        )
    manifest = _load_manifest(pkg_dir)
    declared = manifest.get("python_dependencies", [])
    req_file = pkg_dir / "requirements.txt"

    if deps == "never":
        if declared or req_file.exists():
            logger.info(
                "Skipping Python dependency install (deps='never')",
                package=pkg_dir.name,
            )
        return

    if declared:
        logger.info("Installing Python dependencies", count=len(declared))
        _pip_install([*declared], what="python_dependencies in kohaku.yaml")

    if req_file.exists():
        _pip_install(["-r", str(req_file)], what="requirements.txt")


def get_package_framework_hints(pkg_root: Path | None) -> dict[str, str]:
    """Read the ``framework_hints:`` block from a package manifest.

    Returns an empty dict if the package has no manifest, no
    ``framework_hints`` section, or the section is malformed.
    """
    if pkg_root is None:
        return {}
    manifest = _load_manifest(pkg_root)
    raw = manifest.get("framework_hints") or manifest.get("framework_hint_overrides")
    if not isinstance(raw, dict):
        return {}
    # Coerce all values to strings so downstream doesn't have to guess.
    return {str(k): ("" if v is None else str(v)) for k, v in raw.items()}
