"""Studio catalog — package operations (install / uninstall / update / show).

Wraps the low-tier ``packages/`` library so HTTP routes and the CLI
share a single set of operation entry points. Each function returns
plain dicts / value tuples so transport-specific formatting (Rich
console output, JSON for HTTP) lives at the route or CLI layer.
"""

import os
from pathlib import Path

import yaml

from kohakuterrarium.packages.install import (
    install_package,
    uninstall_package,
    update_package,
)
from kohakuterrarium.packages.locations import PACKAGES_DIR
from kohakuterrarium.packages.resolve import resolve_package_path
from kohakuterrarium.packages.walk import list_packages

# ---------------------------------------------------------------------------
# Package summaries
# ---------------------------------------------------------------------------


def list_installed_packages() -> list[dict]:
    """Thin pass-through to ``list_packages`` for the HTTP route.

    Provides a single Studio-tier symbol so downstream code never has
    to reach into ``packages.walk`` directly.
    """
    return list_packages()


def packages_dir() -> Path:
    """Return the configured packages directory.

    Indirection so the CLI / API don't import ``packages.locations``.
    """
    return PACKAGES_DIR


# ---------------------------------------------------------------------------
# Installation operations
# ---------------------------------------------------------------------------


def install_package_op(
    source: str, editable: bool = False, name: str | None = None
) -> str:
    """Install a creature/terrarium package; returns its package name.

    Verbatim wrapper around ``packages.install.install_package`` —
    propagates exceptions to the caller for transport-specific
    error rendering.
    """
    return install_package(source, editable=editable, name_override=name)


def uninstall_package_op(name: str) -> bool:
    """Uninstall a package by name; returns ``True`` if it was removed."""
    return uninstall_package(name)


def normalize_package_name(target: str) -> str:
    """Normalize ``@pkg`` / ``@pkg/path`` / ``pkg`` to the bare package name.

    Verbatim port of ``cli.packages._normalize_package_name``.
    """
    target = target.strip()
    if not target:
        return ""
    if target.startswith("@"):
        target = target[1:]
    if "/" in target:
        target = target.split("/", 1)[0]
    return target


def update_package_op(name: str) -> tuple[int, str]:
    """Update a single git-backed package.

    Returns ``(rc, message)`` where ``rc`` is a CLI-style exit code
    (0 = success or skipped, 1 = error). Skips editable and non-git
    packages with rc=0, matching ``cli.packages._update_package``.
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

    return 0, f"Updated: {name}"


def update_all_packages_op() -> tuple[int, list[str], int, int]:
    """Update every git-backed installed package.

    Returns ``(exit_code, messages, updated_count, skipped_count)``.
    Verbatim port of the ``--all`` branch of ``cli.packages.update_cli``.
    """
    packages = list_packages()
    if not packages:
        return 0, [f"No packages installed in {PACKAGES_DIR}"], 0, 0

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


# ---------------------------------------------------------------------------
# Inspection / editing
# ---------------------------------------------------------------------------


def load_agent_info(agent_path: str) -> tuple[int, dict | str]:
    """Load a creature/agent's config + file listing for ``kt info``.

    Returns ``(rc, payload)``. On success, ``payload`` is a dict with
    ``{name, description, model, tools, subagents, files}``. On
    failure, ``payload`` is an error message string.

    Verbatim port of ``cli.packages.show_agent_info_cli`` — config
    parsing and field extraction unchanged.
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
    """Resolve an ``@pkg/...`` reference (or local path) to a config file.

    Returns ``(rc, payload)`` where payload is the resolved Path on
    success or an error string on failure. Verbatim port of the
    resolution branch of ``cli.packages.edit_cli``.
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
        # Maybe they pointed to the file directly
        if path.is_file():
            config_file = path
        else:
            return 1, f"No config file found in: {path}"

    return 0, config_file


def open_in_editor(config_file: Path) -> None:
    """Hand off ``config_file`` to ``$EDITOR`` (never returns).

    Wraps the ``os.execvp`` call from ``cli.packages.edit_cli`` so
    transport code doesn't have to import ``os`` for this helper.
    """
    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "nano"))
    os.execvp(editor, [editor, str(config_file)])
