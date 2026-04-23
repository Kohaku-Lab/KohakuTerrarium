"""
Package manager for KohakuTerrarium creature/terrarium packages.

Handles installing, listing, and resolving @package/path references.

Package layout:
  ~/.kohakuterrarium/packages/<name>/
    kohaku.yaml          # manifest
    creatures/           # creature configs
    terrariums/          # terrarium configs

Install methods:
  kt install <git-url>           # clone from git
  kt install <local-path>        # copy into packages dir
  kt install <local-path> -e     # editable (link file, no symlink)

Editable installs write a pointer file instead of copying:
  ~/.kohakuterrarium/packages/<name>.link   (contains absolute path)

Reference syntax:
  @<package>/<path>  resolves to  <real-package-dir>/<path>
"""

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import yaml

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

PACKAGES_DIR = Path.home() / ".kohakuterrarium" / "packages"
LINK_SUFFIX = ".link"


def _force_rmtree(path: Path) -> None:
    """Remove a directory tree, handling read-only files (e.g. .git on Windows)."""

    def _on_error(_func, fpath, _exc_info):
        os.chmod(fpath, stat.S_IWRITE)
        os.unlink(fpath)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_on_error)
    else:
        shutil.rmtree(path, onerror=_on_error)


def _read_link(name: str) -> Path | None:
    """Read a .link pointer file and return the target path, or None."""
    link_file = PACKAGES_DIR / f"{name}{LINK_SUFFIX}"
    if not link_file.exists():
        return None
    target = Path(link_file.read_text(encoding="utf-8").strip())
    if target.is_dir():
        return target
    logger.warning("Link target missing", package=name, target=str(target))
    return None


def _write_link(name: str, target: Path) -> None:
    """Write a .link pointer file."""
    link_file = PACKAGES_DIR / f"{name}{LINK_SUFFIX}"
    link_file.write_text(str(target.resolve()), encoding="utf-8")


def _remove_link(name: str) -> bool:
    """Remove a .link pointer file if it exists."""
    link_file = PACKAGES_DIR / f"{name}{LINK_SUFFIX}"
    if link_file.exists():
        link_file.unlink()
        return True
    return False


def _get_package_root(name: str) -> Path | None:
    """Get the real root directory of an installed package.

    Checks (in order):
      1. .link pointer file (editable install)
      2. Direct directory (cloned / copied)
      3. Symlink (legacy editable install)
    """
    # Editable: pointer file
    link_target = _read_link(name)
    if link_target is not None:
        return link_target

    # Cloned / copied directory
    pkg_dir = PACKAGES_DIR / name
    if pkg_dir.is_dir():
        return pkg_dir.resolve()

    # Legacy symlink (from older installs)
    if pkg_dir.is_symlink():
        real = pkg_dir.resolve()
        if real.is_dir():
            return real

    return None


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

    pkg_root = _get_package_root(package_name)
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


def install_package(
    source: str,
    editable: bool = False,
    name_override: str | None = None,
) -> str:
    """Install a creature/terrarium package.

    Args:
        source: Git URL or local path.
        editable: If True, store a pointer to the source directory
                  instead of copying (like pip -e).
        name_override: Override package name (default: from kohaku.yaml or dir name).

    Returns:
        Installed package name.
    """
    PACKAGES_DIR.mkdir(parents=True, exist_ok=True)

    source_path = Path(source).resolve()

    if (
        source.startswith("http://")
        or source.startswith("https://")
        or source.endswith(".git")
    ):
        # Git clone
        return _install_from_git(source, name_override)
    elif source_path.is_dir():
        # Local directory
        return _install_from_local(source_path, editable, name_override)
    else:
        raise ValueError(
            f"Cannot install from: {source}. "
            "Provide a git URL or local directory path."
        )


def update_package(name: str) -> str:
    """Pull latest changes for a git-installed package.

    Unlike :func:`install_package`, this is only valid for an *already*
    installed, non-editable, git-backed package. It runs
    ``git -C <pkg> pull --ff-only`` in place and re-runs the post-install
    hooks (manifest validation + python deps). The caller is expected to
    have already filtered out editable and non-git packages.

    Raises
    ------
    FileNotFoundError
        If no package with ``name`` exists under ``PACKAGES_DIR``.
    RuntimeError
        If the package is not a git clone, or ``git pull`` fails.
    """
    target = PACKAGES_DIR / name
    if not target.exists() or not target.is_dir():
        raise FileNotFoundError(f"Package not installed: {name}")
    if not (target / ".git").exists():
        raise RuntimeError(f"Package is not a git clone: {name}")

    logger.info("Updating package", package=name)
    try:
        subprocess.run(
            ["git", "-C", str(target), "pull", "--ff-only"],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="replace").strip() if e.stderr else str(e)
        raise RuntimeError(f"Git pull failed for {name}: {stderr}")

    _validate_package(target, name)
    _install_python_deps(target)
    logger.info("Package updated", package=name, path=str(target))
    return name


def _install_from_git(url: str, name_override: str | None = None) -> str:
    """Clone a git repo into packages directory."""
    # Determine package name from URL
    repo_name = url.rstrip("/").split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]

    name = name_override or repo_name
    target = PACKAGES_DIR / name

    # Remove any stale .link file (switching from editable to cloned)
    _remove_link(name)

    if target.exists():
        # Update existing
        logger.info("Updating package", package=name)
        try:
            subprocess.run(
                ["git", "-C", str(target), "pull", "--ff-only"],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Git pull failed: {e.stderr.decode()}")
    else:
        # Fresh clone
        logger.info("Cloning package", package=name, url=url)
        try:
            subprocess.run(
                ["git", "clone", url, str(target)],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Git clone failed: {e.stderr.decode()}")

    _validate_package(target, name)
    _install_python_deps(target)
    logger.info("Package installed", package=name, path=str(target))
    return name


def _install_from_local(
    source: Path, editable: bool, name_override: str | None = None
) -> str:
    """Install from local directory (pointer file or copy)."""
    manifest = _load_manifest(source)
    name = name_override or manifest.get("name", source.name)
    target = PACKAGES_DIR / name

    # Clean up previous install of either kind
    _remove_link(name)
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            target.unlink()
        else:
            _force_rmtree(target)

    if editable:
        # Write a .link pointer file (no symlink, works without admin on Windows)
        _write_link(name, source)
        logger.info("Package linked (editable)", package=name, source=str(source))
    else:
        # Copy
        shutil.copytree(source, target)
        logger.info("Package installed (copy)", package=name, source=str(source))

    _validate_package(source if editable else target, name)
    _install_python_deps(source if editable else target)
    return name


def uninstall_package(name: str) -> bool:
    """Remove an installed package."""
    removed = False

    # Remove .link pointer
    if _remove_link(name):
        removed = True

    # Remove cloned/copied directory
    target = PACKAGES_DIR / name
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            target.unlink()
        else:
            _force_rmtree(target)
        removed = True

    if removed:
        logger.info("Package uninstalled", package=name)
    return removed


def list_packages() -> list[dict]:
    """List all installed packages with their creatures and terrariums."""
    if not PACKAGES_DIR.exists():
        return []

    seen: set[str] = set()
    results = []

    for entry in sorted(PACKAGES_DIR.iterdir()):
        # Determine package name from either dir or .link file
        if entry.suffix == LINK_SUFFIX:
            name = entry.stem
            link_target = _read_link(name)
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


def ensure_package_importable(package_name: str) -> bool:
    """Add a package's root to sys.path so its Python modules are importable.

    Called before importing plugin/tool modules from a package.
    Returns True if the path was added (or already present).
    """
    pkg_root = _get_package_root(package_name)
    if pkg_root is None:
        return False
    root_str = str(pkg_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
        logger.debug("Added package to sys.path", package=package_name, path=root_str)
    return True


def get_package_modules(package_name: str, module_type: str) -> list[dict]:
    """Get modules of a specific type from a package manifest.

    Args:
        package_name: Name of the installed package.
        module_type: One of "tools", "plugins", "llm_presets", "creatures", "terrariums".

    Returns:
        List of module definition dicts from the manifest, or [] if not found.
    """
    pkg_root = _get_package_root(package_name)
    if pkg_root is None:
        return []
    manifest = _load_manifest(pkg_root)
    return manifest.get(module_type, [])


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


# Cluster 1 manifest slots (A.2 / A.3 / A.4 / A.5) live in
# ``packages_manifest`` but are re-exported here so callers keep a
# single import surface (``from kohakuterrarium.packages import
# resolve_package_skills``). Safe against circular import because the
# re-export runs after ``list_packages`` is defined.
from kohakuterrarium.packages_manifest import (  # noqa: E402,F401
    list_package_commands,
    list_package_prompts,
    list_package_skills,
    list_package_user_commands,
    resolve_package_command,
    resolve_package_prompt,
    resolve_package_skills,
    resolve_package_user_command,
)


def get_package_path(name: str) -> Path | None:
    """Get the path to an installed package."""
    return _get_package_root(name)


def find_package_root_for_path(path: Path | None) -> Path | None:
    """Walk up from ``path`` until a directory containing a manifest is found.

    Returns the first ancestor directory that contains ``kohaku.yaml`` (or
    ``kohaku.yml``), or ``None`` if no such ancestor exists. Used to resolve
    package-level defaults for a creature whose config lives in
    ``<pkg_root>/creatures/<name>/``.
    """
    if path is None:
        return None
    try:
        current = path.resolve()
    except OSError:
        return None
    # Start from path if it's a directory, else from its parent.
    if current.is_file():
        current = current.parent
    for _ in range(20):  # safety bound against pathological paths
        if (current / "kohaku.yaml").exists() or (current / "kohaku.yml").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


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
