"""Studio catalog — package operations (install / uninstall / update / show).

Wraps the low-tier package library with transport-neutral operations shared by
HTTP and CLI adapters. Functions return plain values so presentation and error
rendering remain outside the catalog layer.
"""

import os
from pathlib import Path

import yaml

from kohakuterrarium.packages.install import (
    install_package_spec,
    uninstall_package,
    update_package,
)
from kohakuterrarium.packages.locations import packages_dir as _locations_packages_dir
from kohakuterrarium.packages.resolve import resolve_package_path
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.studio.catalog.packages_scan import invalidate_scan_caches

def list_installed_packages() -> list[dict]:
    """Return installed package manifests through the Studio catalog boundary."""
    return list_packages()


def packages_dir() -> Path:
    """Return the configured package root without exposing location internals."""
    return _locations_packages_dir()


def install_package_op(
    source: str,
    editable: bool = False,
    name: str | None = None,
    *,
    deps: str = "auto",
) -> str:
    """Install a package and return its registered name.

    Exceptions remain transport-neutral. Successful installation invalidates
    discovery caches so newly declared creatures and terrariums are visible on
    the next catalog read.
    """
    name = install_package_spec(
        source, editable=editable, name_override=name, deps=deps
    )
    invalidate_scan_caches()
    return name


def uninstall_package_op(name: str) -> bool:
    """Uninstall a package and invalidate discovery only when removal occurs."""
    removed = uninstall_package(name)
    if removed:
        invalidate_scan_caches()
    return removed


def normalize_package_name(target: str) -> str:
    """Normalize package references and paths to a bare package name."""
    target = target.strip()
    if not target:
        return ""
    if target.startswith("@"):
        target = target[1:]
    if "/" in target:
        target = target.split("/", 1)[0]
    return target


def update_package_op(name: str) -> tuple[int, str]:
    """Update one git-backed package and return a CLI-style result tuple.

    Editable and non-git packages are intentional skips rather than failures.
    """
    packages = {pkg["name"]: pkg for pkg in list_packages()}
    pkg = packages.get(name)
    if not pkg:
        return 1, f"Package not found: {name}"
    if pkg["editable"]:
        return 0, f"Skipped editable package: {name}"

    path = Path(pkg["path"])
    git_dir = path / ".git"
    if not git_dir.exists():
        return 0, f"Skipped non-git package: {name}"

    try:
        update_package(name)
    except Exception as e:
        return 1, f"Failed to update {name}: {e}"

    # A new revision may change manifest visibility, so cached discovery is no
    # longer authoritative after a successful update.
    invalidate_scan_caches()
    return 0, f"Updated: {name}"


def update_all_packages_op() -> tuple[int, list[str], int, int]:
    """Update all eligible packages and aggregate messages and counts."""
    packages = list_packages()
    if not packages:
        return 0, [f"No packages installed in {packages_dir()}"], 0, 0

    messages: list[str] = []
    exit_code = 0
    updated = 0
    skipped = 0
    for pkg in packages:
        if pkg["editable"]:
            messages.append(f"Skipped editable package: {pkg['name']}")
            skipped += 1
            continue
        path = Path(pkg["path"])
        if not (path / ".git").exists():
            messages.append(f"Skipped non-git package: {pkg['name']}")
            skipped += 1
            continue
        code, msg = update_package_op(pkg["name"])
        messages.append(msg)
        if code == 0:
            updated += 1
        else:
            exit_code = code
    return exit_code, messages, updated, skipped


def load_agent_info(agent_path: str) -> tuple[int, dict | str]:
    """Load summary metadata and sibling files for a creature directory.

    The result uses a CLI-style ``(return_code, payload)`` tuple so callers can
    render either the structured success value or an error string.
    """
    path = Path(agent_path)
    if not path.exists():
        return 1, f"Agent path not found: {agent_path}"

    config_file = path / "config.yaml"
    if not config_file.exists():
        config_file = path / "config.yml"
        if not config_file.exists():
            return 1, f"No config.yaml found in {agent_path}"

    try:
        with open(config_file, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        return 1, f"Error reading config: {e}"

    tools_out: list[str] = []
    for tool in config.get("tools", []) or []:
        if isinstance(tool, dict):
            tools_out.append(tool.get("name", "unknown"))
        else:
            tools_out.append(str(tool))

    subagents_out: list[str] = []
    for sa in config.get("subagents", []) or []:
        if isinstance(sa, dict):
            subagents_out.append(sa.get("name", "unknown"))
        else:
            subagents_out.append(str(sa))

    files_out: list[str] = sorted(f.name for f in path.iterdir() if f.is_file())

    return 0, {
        "name": config.get("name", path.name),
        "description": config.get("description", ""),
        "model": config.get("model", ""),
        "tools": tools_out,
        "subagents": subagents_out,
        "files": files_out,
    }


def resolve_edit_target(target: str) -> tuple[int, Path | str]:
    """Resolve a package reference to the first supported config file.

    The returned tuple contains either the resolved path or a transport-neutral
    error message.
    """
    if not target.startswith("@"):
        target = "@" + target

    try:
        path = resolve_package_path(target)
    except (FileNotFoundError, ValueError) as e:
        return 1, str(e)

    config_file: Path | None = None
    for name in ("config.yaml", "config.yml", "terrarium.yaml", "terrarium.yml"):
        candidate = path / name
        if candidate.exists():
            config_file = candidate
            break

    if config_file is None:
        # Direct file references are valid when no conventional config exists
        # beneath the resolved path.
        if path.is_file():
            config_file = path
        else:
            return 1, f"No config file found in: {path}"

    return 0, config_file


def open_in_editor(config_file: Path) -> None:
    """Replace the current process with the configured editor."""
    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "nano"))
    os.execvp(editor, [editor, str(config_file)])
