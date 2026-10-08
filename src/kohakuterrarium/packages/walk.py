"""Package enumeration helpers."""

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import local

from kohakuterrarium.packages.locations import LINK_SUFFIX
from kohakuterrarium.packages.locations import LOCAL_PROJECT_NAME
from kohakuterrarium.packages.locations import _packages_dir
from kohakuterrarium.packages.locations import get_package_root
from kohakuterrarium.packages.locations import read_link
from kohakuterrarium.packages.manifest import _load_manifest

_PACKAGE_SNAPSHOT = local()


@contextmanager
def package_snapshot():
    """Reuse one package enumeration within a bounded construction scope."""
    if getattr(_PACKAGE_SNAPSHOT, "packages", None) is not None:
        yield
        return
    _PACKAGE_SNAPSHOT.packages = _list_packages_uncached()
    try:
        yield
    finally:
        del _PACKAGE_SNAPSHOT.packages


def list_packages() -> list[dict]:
    """List installed packages, reusing the active build-scoped snapshot."""
    snapshot = getattr(_PACKAGE_SNAPSHOT, "packages", None)
    if snapshot is not None:
        return deepcopy(snapshot)
    return _list_packages_uncached()


def _conventional_entries(root: Path, kind: str, marker: tuple[str, ...]) -> list[dict]:
    """Every folder under ``root/kind`` holding one of ``marker`` files, as manifest entries."""
    base = root / kind
    if not base.is_dir():
        return []
    return [
        {"name": child.name, "path": f"{kind}/{child.name}"}
        for child in sorted(base.iterdir())
        if child.is_dir() and any((child / m).exists() for m in marker)
    ]


def _local_project_record() -> dict | None:
    """The local project as a package record, or None until it exists.

    Its creatures and terrariums need no manifest entries: every folder under
    ``creatures/`` / ``terrariums/`` counts, unless the manifest lists them.
    """
    root = get_package_root(LOCAL_PROJECT_NAME)
    if root is None:
        return None
    manifest = _load_manifest(root)
    record = _package_record(LOCAL_PROJECT_NAME, root, manifest, editable=True)
    record["local"] = True
    if not record["creatures"]:
        record["creatures"] = _conventional_entries(
            root, "creatures", ("config.yaml", "config.yml")
        )
    if not record["terrariums"]:
        record["terrariums"] = _conventional_entries(
            root, "terrariums", ("terrarium.yaml", "terrarium.yml")
        )
    return record


def _package_record(
    name: str, pkg_dir: Path, manifest: dict, *, editable: bool
) -> dict:
    return {
        # The install-dir name is the identity every lookup keys on; ``manifest_name`` is display only.
        "name": name,
        "manifest_name": manifest.get("name", name),
        "version": manifest.get("version", "?"),
        "description": manifest.get("description", ""),
        "path": str(pkg_dir),
        "editable": editable,
        "local": False,
        "creatures": manifest.get("creatures", []),
        "terrariums": manifest.get("terrariums", []),
        "tools": manifest.get("tools", []),
        "plugins": manifest.get("plugins", []),
        "llm_presets": manifest.get("llm_presets", []),
        "io": manifest.get("io", []),
        "triggers": manifest.get("triggers", []),
        "skills": manifest.get("skills", []),
        "commands": manifest.get("commands", []),
        "user_commands": manifest.get("user_commands", []),
        "prompts": manifest.get("prompts", []),
        "templates": manifest.get("templates", []),
        "drive_registrations": manifest.get("drive_registrations", []),
    }


def _list_packages_uncached() -> list[dict]:
    """List installed packages, then the local project when it exists."""
    results = _list_installed()
    local = _local_project_record()
    if local is not None:
        results.append(local)
    return results


def _list_installed() -> list[dict]:
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

        results.append(
            _package_record(name, pkg_dir, _load_manifest(pkg_dir), editable=editable)
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
