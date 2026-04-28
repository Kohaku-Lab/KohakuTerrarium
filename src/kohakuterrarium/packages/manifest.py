"""Manifest IO + validation + dependency installation helpers."""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import yaml

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


def _install_python_deps(pkg_dir: Path) -> None:
    """Install Python dependencies and the package itself if applicable."""
    manifest = _load_manifest(pkg_dir)
    deps = manifest.get("python_dependencies", [])
    if deps:
        logger.info("Installing Python dependencies", count=len(deps))
        try:
            subprocess.run(
                ["pip", "install", *deps],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            logger.warning("Dependency install failed", error=e.stderr.decode()[:200])

    req_file = pkg_dir / "requirements.txt"
    if req_file.exists():
        try:
            subprocess.run(
                ["pip", "install", "-r", str(req_file)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            logger.warning(
                "requirements.txt install failed", error=e.stderr.decode()[:200]
            )


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
