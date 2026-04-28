"""Package enumeration helpers."""

from kohakuterrarium.packages.locations import LINK_SUFFIX
from kohakuterrarium.packages.locations import _packages_dir
from kohakuterrarium.packages.locations import get_package_root
from kohakuterrarium.packages.locations import read_link
from kohakuterrarium.packages.manifest import _load_manifest


def list_packages() -> list[dict]:
    """List all installed packages with their creatures and terrariums."""
    # Honour test monkeypatches against ``locations.PACKAGES_DIR`` by
    # consulting ``_packages_dir()`` rather than the captured constant.
    packages_dir = _packages_dir()
    if not packages_dir.exists():
        return []

    seen: set[str] = set()
    results = []

    for entry in sorted(packages_dir.iterdir()):
        # Determine package name from either dir or .link file
        if entry.suffix == LINK_SUFFIX:
            name = entry.stem
            link_target = read_link(name)
            if link_target is None:
                continue
            pkg_dir = link_target
            editable = True
        elif entry.is_dir() or entry.is_symlink():
            name = entry.name
            pkg_dir = entry.resolve() if entry.is_symlink() else entry
            editable = entry.is_symlink()
        else:
            continue

        if name in seen:
            continue
        seen.add(name)

        manifest = _load_manifest(pkg_dir)
        results.append(
            {
                "name": manifest.get("name", name),
                "version": manifest.get("version", "?"),
                "description": manifest.get("description", ""),
                "path": str(pkg_dir),
                "editable": editable,
                "creatures": manifest.get("creatures", []),
                "terrariums": manifest.get("terrariums", []),
                "tools": manifest.get("tools", []),
                "plugins": manifest.get("plugins", []),
                "llm_presets": manifest.get("llm_presets", []),
                "io": manifest.get("io", []),
                "triggers": manifest.get("triggers", []),
                # Cluster 1 manifest slots (A.2 / A.3 / A.4 / A.5):
                # skills + controller commands + user slash commands +
                # shared prompt fragments. The ``templates`` field is
                # surfaced as an alias for ``prompts`` so resolvers can
                # scan both without two round-trips through
                # list_packages().
                "skills": manifest.get("skills", []),
                "commands": manifest.get("commands", []),
                "user_commands": manifest.get("user_commands", []),
                "prompts": manifest.get("prompts", []),
                "templates": manifest.get("templates", []),
            }
        )
    return results


def get_package_modules(package_name: str, module_type: str) -> list[dict]:
    """Get modules of a specific type from a package manifest.

    Args:
        package_name: Name of the installed package.
        module_type: One of "tools", "plugins", "llm_presets", "creatures", "terrariums".

    Returns:
        List of module definition dicts from the manifest, or [] if not found.
    """
    pkg_root = get_package_root(package_name)
    if pkg_root is None:
        return []
    manifest = _load_manifest(pkg_root)
    return manifest.get(module_type, [])
