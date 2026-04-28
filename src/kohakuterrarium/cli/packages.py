"""CLI package management commands — list, info, install, uninstall, edit.

This module is now a thin formatter around
``studio.catalog.packages``. All scan / operation logic lives there
so the HTTP API and the CLI cannot drift.
"""

from pathlib import Path

from kohakuterrarium.studio.catalog.packages import (
    install_package_op,
    list_installed_packages,
    load_agent_info,
    normalize_package_name,
    open_in_editor,
    packages_dir,
    resolve_edit_target,
    uninstall_package_op,
    update_all_packages_op,
    update_package_op,
)


def list_cli(agents_path: str = "agents") -> int:
    """List installed packages and available agents/terrariums."""
    packages = list_installed_packages()
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
        print(f"No packages installed in {packages_dir()}")
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
    rc, payload = load_agent_info(agent_path)
    if rc != 0:
        print(f"Error: {payload}")
        return rc

    info: dict = payload  # type: ignore[assignment]
    print(f"Agent: {info['name']}")
    print("-" * 40)
    if info["description"]:
        print(f"Description: {info['description']}")
    if info["model"]:
        print(f"Model: {info['model']}")

    if info["tools"]:
        print(f"\nTools ({len(info['tools'])}):")
        for tool in info["tools"]:
            print(f"  - {tool}")

    if info["subagents"]:
        print(f"\nSub-agents ({len(info['subagents'])}):")
        for sa in info["subagents"]:
            print(f"  - {sa}")

    print("\nFiles:")
    for fname in info["files"]:
        print(f"  - {fname}")
    return 0


def install_cli(source: str, editable: bool = False, name: str | None = None) -> int:
    """Install a creature/terrarium package."""
    try:
        pkg_name = install_package_op(source, editable=editable, name=name)
    except Exception as e:
        print(f"Error: {e}")
        return 1
    tag = " (editable)" if editable else ""
    print(f"Installed: {pkg_name}{tag}")
    print()
    print("Usage:")
    print(f"  kt run @{pkg_name}/creatures/<name>")
    print(f"  kt terrarium run @{pkg_name}/terrariums/<name>")
    print(f"  kt list")
    return 0


def uninstall_cli(name: str) -> int:
    """Remove an installed package."""
    if uninstall_package_op(name):
        print(f"Uninstalled: {name}")
        return 0
    print(f"Package not found: {name}")
    return 1


def update_cli(target: str | None = None, update_all: bool = False) -> int:
    """Update installed git-backed packages."""
    if update_all:
        rc, messages, updated, skipped = update_all_packages_op()
        for msg in messages:
            print(msg)
        print()
        print(f"Update summary: {updated} updated, {skipped} skipped")
        return rc

    if not target:
        print("Usage: kt update @package")
        print("   or: kt update --all")
        return 1

    name = normalize_package_name(target)
    if not name:
        print("Package name is required.")
        return 1
    rc, msg = update_package_op(name)
    print(msg)
    return rc


def edit_cli(target: str) -> int:
    """Open a creature/terrarium config in editor."""
    rc, payload = resolve_edit_target(target)
    if rc != 0:
        print(f"Error: {payload}")
        return rc
    config_file: Path = payload  # type: ignore[assignment]
    print(f"Opening: {config_file}")
    open_in_editor(config_file)
    return 0  # unreachable after execvp
