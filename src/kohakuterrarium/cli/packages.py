"""CLI package management commands — list, info, install, uninstall, edit."""

import os
from pathlib import Path

import yaml

from kohakuterrarium.packages import (
    PACKAGES_DIR,
    install_package,
    list_packages,
    resolve_package_path,
    uninstall_package,
    update_package,
)


def list_cli(agents_path: str = "agents") -> int:
    """List installed packages and available agents/terrariums."""
    # Show installed packages
    packages = list_packages()
    if packages:
        print("Installed packages:")
        print("=" * 50)
        for pkg in packages:
            editable_tag = " (editable)" if pkg["editable"] else ""
            print(f"  {pkg['name']} v{pkg['version']}{editable_tag}")
            print(f"    {pkg['path']}")
            if pkg["description"]:
                print(f"    {pkg['description']}")
            if pkg["creatures"]:
                names = [c["name"] for c in pkg["creatures"]]
                print(f"    Creatures: {', '.join(names)}")
            if pkg["terrariums"]:
                names = [t["name"] for t in pkg["terrariums"]]
                print(f"    Terrariums: {', '.join(names)}")
            print()
    else:
        print(f"No packages installed in {PACKAGES_DIR}")
        print()

    # Also show local agents if directory exists
    path = Path(agents_path)
    if path.exists():
        print(f"Local agents in {agents_path}:")
        print("-" * 40)
        found = False
        for agent_dir in sorted(path.iterdir()):
            if not agent_dir.is_dir():
                continue
            config_file = agent_dir / "config.yaml"
            if not config_file.exists():
                config_file = agent_dir / "config.yml"
            if config_file.exists():
                found = True
                print(f"  {agent_dir.name}")
        if not found:
            print("  (none)")

    return 0


def show_agent_info_cli(agent_path: str) -> int:
    """Show agent information."""
    path = Path(agent_path)
    if not path.exists():
        print(f"Error: Agent path not found: {agent_path}")
        return 1

    config_file = path / "config.yaml"
    if not config_file.exists():
        config_file = path / "config.yml"
        if not config_file.exists():
            print(f"Error: No config.yaml found in {agent_path}")
            return 1

    try:

        with open(config_file, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        print(f"Agent: {config.get('name', path.name)}")
        print("-" * 40)

        if config.get("description"):
            print(f"Description: {config['description']}")

        if config.get("model"):
            print(f"Model: {config['model']}")

        # Tools
        tools = config.get("tools", [])
        if tools:
            print(f"\nTools ({len(tools)}):")
            for tool in tools:
                if isinstance(tool, dict):
                    print(f"  - {tool.get('name', 'unknown')}")
                else:
                    print(f"  - {tool}")

        # Sub-agents
        subagents = config.get("subagents", [])
        if subagents:
            print(f"\nSub-agents ({len(subagents)}):")
            for sa in subagents:
                if isinstance(sa, dict):
                    print(f"  - {sa.get('name', 'unknown')}")
                else:
                    print(f"  - {sa}")

        # Files
        print(f"\nFiles:")
        for f in sorted(path.iterdir()):
            if f.is_file():
                print(f"  - {f.name}")

        return 0

    except Exception as e:
        print(f"Error reading config: {e}")
        return 1


def install_cli(source: str, editable: bool = False, name: str | None = None) -> int:
    """Install a creature/terrarium package."""
    try:
        pkg_name = install_package(source, editable=editable, name_override=name)
        tag = " (editable)" if editable else ""
        print(f"Installed: {pkg_name}{tag}")
        print()
        print("Usage:")
        print(f"  kt run @{pkg_name}/creatures/<name>")
        print(f"  kt terrarium run @{pkg_name}/terrariums/<name>")
        print(f"  kt list")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def uninstall_cli(name: str) -> int:
    """Remove an installed package."""
    if uninstall_package(name):
        print(f"Uninstalled: {name}")
        return 0
    else:
        print(f"Package not found: {name}")
        return 1


def _normalize_package_name(target: str) -> str:
    target = target.strip()
    if not target:
        return ""
    if target.startswith("@"):
        target = target[1:]
    if "/" in target:
        target = target.split("/", 1)[0]
    return target


def _update_package(name: str) -> int:
    packages = {pkg["name"]: pkg for pkg in list_packages()}
    pkg = packages.get(name)
    if not pkg:
        print(f"Package not found: {name}")
        return 1
    if pkg["editable"]:
        print(f"Skipped editable package: {name}")
        return 0

    path = Path(pkg["path"])
    git_dir = path / ".git"
    if not git_dir.exists():
        print(f"Skipped non-git package: {name}")
        return 0

    try:
        update_package(name)
    except Exception as e:
        print(f"Failed to update {name}: {e}")
        return 1

    print(f"Updated: {name}")
    return 0


def update_cli(target: str | None = None, update_all: bool = False) -> int:
    """Update installed git-backed packages."""
    if update_all:
        packages = list_packages()
        if not packages:
            print(f"No packages installed in {PACKAGES_DIR}")
            return 0

        exit_code = 0
        updated = 0
        skipped = 0
        for pkg in packages:
            if pkg["editable"]:
                print(f"Skipped editable package: {pkg['name']}")
                skipped += 1
                continue
            path = Path(pkg["path"])
            if not (path / ".git").exists():
                print(f"Skipped non-git package: {pkg['name']}")
                skipped += 1
                continue
            code = _update_package(pkg["name"])
            if code == 0:
                updated += 1
            else:
                exit_code = code
        print()
        print(f"Update summary: {updated} updated, {skipped} skipped")
        return exit_code

    if not target:
        print("Usage: kt update @package")
        print("   or: kt update --all")
        return 1

    name = _normalize_package_name(target)
    if not name:
        print("Package name is required.")
        return 1
    return _update_package(name)


def edit_cli(target: str) -> int:
    """Open a creature/terrarium config in editor."""
    if not target.startswith("@"):
        target = "@" + target

    try:
        path = resolve_package_path(target)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}")
        return 1

    # Find config file
    config_file = None
    for name in ("config.yaml", "config.yml", "terrarium.yaml", "terrarium.yml"):
        candidate = path / name
        if candidate.exists():
            config_file = candidate
            break

    if not config_file:
        # Maybe they pointed to the file directly
        if path.is_file():
            config_file = path
        else:
            print(f"No config file found in: {path}")
            return 1

    editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "nano"))
    print(f"Opening: {config_file}")
    os.execvp(editor, [editor, str(config_file)])
    return 0  # unreachable after execvp
