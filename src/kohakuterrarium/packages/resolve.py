"""``@pkg/path`` references and per-kind manifest resolvers."""

import sys
from pathlib import Path

from kohakuterrarium.packages.locations import get_package_root
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def resolve_package_path(ref: str) -> Path:
    """Resolve a @package/path reference to an absolute path.

    Args:
        ref: Reference like "@kt-biome/creatures/swe"

    Returns:
        Absolute path to the resolved location.

    Raises:
        FileNotFoundError: If the package or path doesn't exist.
    """
    if not ref.startswith("@"):
        raise ValueError(f"Not a package reference (must start with @): {ref}")

    ref = ref[1:]  # strip @
    parts = ref.split("/", 1)
    package_name = parts[0]
    sub_path = parts[1] if len(parts) > 1 else ""

    pkg_root = get_package_root(package_name)
    if pkg_root is None:
        raise FileNotFoundError(
            f"Package not installed: {package_name}. Run: kt install <url-or-path>"
        )

    resolved = pkg_root / sub_path if sub_path else pkg_root
    if not resolved.exists():
        raise FileNotFoundError(f"Path not found in package {package_name}: {sub_path}")

    return resolved.resolve()


def is_package_ref(path: str) -> bool:
    """Check if a path is a @package reference."""
    return isinstance(path, str) and path.startswith("@")


def ensure_package_importable(package_name: str) -> bool:
    """Add a package's root to sys.path so its Python modules are importable.

    Called before importing plugin/tool modules from a package.
    Returns True if the path was added (or already present).
    """
    pkg_root = get_package_root(package_name)
    if pkg_root is None:
        return False
    root_str = str(pkg_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
        logger.debug("Added package to sys.path", package=package_name, path=root_str)
    return True


def resolve_package_tool(tool_name: str) -> tuple[str, str] | None:
    """Scan installed packages for a tool with the given name.

    Returns:
        (module_path, class_name) tuple if found, or None.
    """
    for pkg in list_packages():
        for tool_def in pkg.get("tools", []):
            if not isinstance(tool_def, dict):
                continue
            if tool_def.get("name") == tool_name:
                module_path = tool_def.get("module")
                class_name = tool_def.get("class") or tool_def.get("class_name")
                if module_path and class_name:
                    return (module_path, class_name)
    return None


def _resolve_manifest_entry(
    kind: str,
    entry_name: str,
) -> tuple[str, str] | None:
    """Scan installed packages for a manifest entry of ``kind`` with the given name.

    Shared helper for :func:`resolve_package_io` and
    :func:`resolve_package_trigger`. Collisions (two packages exporting the
    same ``entry_name`` for the same ``kind``) raise ``ValueError`` with both
    package names listed — per cluster 1.1 of the extension-point decisions,
    io / trigger name clashes are a hard error at load time.

    Args:
        kind: Manifest field to scan (e.g. ``"io"`` or ``"triggers"``).
        entry_name: The short name requested by the agent config.

    Returns:
        ``(module_path, class_name)`` if exactly one package declares
        ``entry_name`` under ``kind``; ``None`` if no package declares it.

    Raises:
        ValueError: If more than one installed package declares the same
            ``entry_name`` under ``kind``.
    """
    matches: list[tuple[str, str, str]] = []  # (package_name, module, class)
    for pkg in list_packages():
        for entry in pkg.get(kind, []):
            if not isinstance(entry, dict):
                continue
            if entry.get("name") != entry_name:
                continue
            module_path = entry.get("module")
            class_name = entry.get("class") or entry.get("class_name")
            if not module_path or not class_name:
                continue
            matches.append((pkg.get("name", "?"), module_path, class_name))

    if not matches:
        return None
    if len(matches) > 1:
        conflicting = ", ".join(sorted({m[0] for m in matches}))
        raise ValueError(
            f"Collision for {kind} name {entry_name!r}: declared by packages "
            f"[{conflicting}]. Uninstall one or rename the entry in its "
            f"kohaku.yaml to resolve the conflict."
        )
    _, module_path, class_name = matches[0]
    return (module_path, class_name)


def resolve_package_io(io_name: str) -> tuple[str, str] | None:
    """Scan installed packages for an IO module with the given name.

    Looks up ``io:`` entries declared in each package's ``kohaku.yaml``.
    Collisions across packages raise ``ValueError`` at lookup time.

    Returns:
        (module_path, class_name) tuple if found, or None.
    """
    return _resolve_manifest_entry("io", io_name)


def resolve_package_trigger(trigger_name: str) -> tuple[str, str] | None:
    """Scan installed packages for a trigger module with the given name.

    Looks up ``triggers:`` entries declared in each package's ``kohaku.yaml``.
    Collisions across packages raise ``ValueError`` at lookup time.

    Returns:
        (module_path, class_name) tuple if found, or None.
    """
    return _resolve_manifest_entry("triggers", trigger_name)
