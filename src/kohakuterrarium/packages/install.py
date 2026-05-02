"""Install / update / uninstall installed packages."""

import shutil
import subprocess
from pathlib import Path

from kohakuterrarium.packages.locations import _packages_dir
from kohakuterrarium.packages.locations import get_package_root
from kohakuterrarium.packages.locations import read_link
from kohakuterrarium.packages.locations import remove_link
from kohakuterrarium.packages.locations import write_link
from kohakuterrarium.packages.manifest import _force_rmtree
from kohakuterrarium.packages.manifest import _install_python_deps
from kohakuterrarium.packages.manifest import _load_manifest
from kohakuterrarium.packages.manifest import _validate_package
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


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
    # Reference PACKAGES_DIR through the locations module so test
    # monkeypatches against ``locations.PACKAGES_DIR`` are honoured.
    _packages_dir().mkdir(parents=True, exist_ok=True)

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
        If no package with ``name`` exists under
        :data:`~kohakuterrarium.packages.locations.PACKAGES_DIR`.
    RuntimeError
        If the package is not a git clone, or ``git pull`` fails.
    """
    # Resolve through ``.link`` pointers / symlinks to the real
    # checkout. ``_packages_dir() / name`` alone misses editable
    # installs (which live as ``<name>.link`` siblings) and resolves
    # to the *symlinked* path on Windows junctions — both of which
    # break ``git -C`` when the checkout is a submodule. Submodule
    # ``.git`` files carry a relative ``gitdir: ../.git/modules/<name>``
    # that git resolves against the literal cwd; if that cwd is the
    # symlink, git looks for the gitdir under the wrong parent and
    # bails with "fatal: not a git repository".
    target = get_package_root(name)
    if target is None:
        raise FileNotFoundError(f"Package not installed: {name}")
    target = target.resolve()
    is_editable = read_link(name) is not None
    if not (target / ".git").exists():
        raise RuntimeError(f"Package is not a git clone: {name}")

    logger.info(
        "Updating package",
        package=name,
        path=str(target),
        editable=is_editable,
    )
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
    target = _packages_dir() / name

    # Remove any stale .link file (switching from editable to cloned)
    remove_link(name)

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
    target = _packages_dir() / name

    # Clean up previous install of either kind
    remove_link(name)
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            target.unlink()
        else:
            _force_rmtree(target)

    if editable:
        # Write a .link pointer file (no symlink, works without admin on Windows)
        write_link(name, source)
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
    if remove_link(name):
        removed = True

    # Remove cloned/copied directory
    target = _packages_dir() / name
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            target.unlink()
        else:
            _force_rmtree(target)
        removed = True

    if removed:
        logger.info("Package uninstalled", package=name)
    return removed
