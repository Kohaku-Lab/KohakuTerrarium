"""Config discovery routes - scan directories for available creature/terrarium configs."""

import os
import sys
from pathlib import Path

import yaml
from fastapi import APIRouter

from kohakuterrarium.builtins.user_commands import (
    get_builtin_user_command,
    list_builtin_user_commands,
)
from kohakuterrarium.llm.profiles import list_all as list_all_models
from kohakuterrarium.packages import PACKAGES_DIR, _get_package_root, list_packages

router = APIRouter()

# Directories to scan — set by create_app() or auto-detected from packages
_creatures_dirs: list[Path] = []
_terrariums_dirs: list[Path] = []

# Package root → package name mapping (for @package/path references)
_package_roots: dict[str, str] = {}


def set_config_dirs(creatures: list[str], terrariums: list[str]) -> None:
    """Set directories to scan for configs.

    Dirs are deduplicated by resolved path.
    Also builds the package root mapping for @package/path references.
    """
    global _creatures_dirs, _terrariums_dirs, _package_roots
    seen_c: set[str] = set()
    seen_t: set[str] = set()
    _creatures_dirs = []
    _terrariums_dirs = []
    for d in creatures:
        p = Path(d).resolve()
        if str(p) not in seen_c:
            _creatures_dirs.append(p)
            seen_c.add(str(p))
    for d in terrariums:
        p = Path(d).resolve()
        if str(p) not in seen_t:
            _terrariums_dirs.append(p)
            seen_t.add(str(p))

    # Build package root mapping
    _package_roots = {}
    if PACKAGES_DIR.exists():
        for pkg in list_packages():
            pkg_root = _get_package_root(pkg["name"])
            if pkg_root:
                _package_roots[str(pkg_root.resolve())] = pkg["name"]


def _to_ref(path: Path) -> str:
    """Convert absolute path to @package/... reference if inside a package.

    Returns the @package reference for installed packages, or the absolute
    path string for local directories.
    """
    resolved = str(path.resolve())
    for root, name in _package_roots.items():
        if resolved.startswith(root):
            rel = resolved[len(root) :].lstrip("/").lstrip("\\").replace("\\", "/")
            return f"@{name}/{rel}"
    return str(path)


def _scan_creature_configs() -> list[dict]:
    """Scan creature directories for config.yaml files."""
    results = []
    for base_dir in _creatures_dirs:
        if not base_dir.is_dir():
            continue
        for child in sorted(base_dir.iterdir()):
            if not child.is_dir():
                continue
            config_file = child / "config.yaml"
            if not config_file.exists():
                config_file = child / "config.yml"
            if not config_file.exists():
                continue
            try:
                data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
                results.append(
                    {
                        "name": data.get("name", child.name),
                        "path": _to_ref(child),
                        "description": data.get("description", ""),
                    }
                )
            except Exception as e:
                _ = e  # fallback: creature config parse failed, return minimal entry
                results.append(
                    {
                        "name": child.name,
                        "path": _to_ref(child),
                        "description": "",
                    }
                )
    return results


def _scan_terrarium_configs() -> list[dict]:
    """Scan terrarium directories for terrarium.yaml files."""
    results = []
    for base_dir in _terrariums_dirs:
        if not base_dir.is_dir():
            continue
        for child in sorted(base_dir.iterdir()):
            if not child.is_dir():
                continue
            config_file = child / "terrarium.yaml"
            if not config_file.exists():
                config_file = child / "terrarium.yml"
            if not config_file.exists():
                continue
            try:
                data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
                terrarium = data.get("terrarium", data)
                results.append(
                    {
                        "name": terrarium.get("name", child.name),
                        "path": _to_ref(child),
                        "description": terrarium.get("description", ""),
                    }
                )
            except Exception as e:
                _ = e  # fallback: terrarium config parse failed, return minimal entry
                results.append(
                    {
                        "name": child.name,
                        "path": _to_ref(child),
                        "description": "",
                    }
                )
    return results


@router.get("/creatures")
async def list_creature_configs():
    """List available creature configs from configured directories."""
    return _scan_creature_configs()


@router.get("/terrariums")
async def list_terrarium_configs():
    """List available terrarium configs from configured directories."""
    return _scan_terrarium_configs()


@router.get("/server-info")
async def server_info():
    """Return server environment info (cwd, platform, etc.)."""
    return {
        "cwd": os.getcwd(),
        "platform": sys.platform,
    }


@router.get("/models")
async def list_models():
    """List available LLM models/profiles with availability status."""
    return list_all_models()


@router.get("/commands")
async def list_commands():
    """List available user slash commands."""
    result = []
    for name in list_builtin_user_commands():
        cmd = get_builtin_user_command(name)
        if cmd:
            result.append(
                {
                    "name": cmd.name,
                    "aliases": cmd.aliases,
                    "description": cmd.description,
                    "layer": cmd.layer.value,
                }
            )
    return result
