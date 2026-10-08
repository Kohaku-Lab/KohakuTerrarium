"""Low-level package-location helpers shared by package modules.

This module intentionally contains only constants and filesystem lookup
primitives. Higher-level package management (:mod:`.install`,
:mod:`.walk`) and manifest-slot resolvers (:mod:`.slots`) all depend on
it, avoiding a cycle between those public modules.
"""

from pathlib import Path

from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Override hook — tests and embedders may set this (directly or via
# ``monkeypatch.setattr``) to pin the packages directory.  When left at
# its default, :func:`packages_dir` resolves fresh from ``config_dir()``
# so ``KT_CONFIG_DIR`` re-homing applies to packages too.
PACKAGES_DIR = Path.home() / ".kohakuterrarium" / "packages"
_DEFAULT_PACKAGES_DIR = PACKAGES_DIR
LINK_SUFFIX = ".link"

# The local project is the package with the empty name (``@/sub/path``); None derives its dir.
PROJECT_DIR: Path | None = None
LOCAL_PROJECT_NAME = ""
_PROJECT_MANIFEST = (
    "name: local\n"
    "version: 0.1.0\n"
    "description: Your local KohakuTerrarium project. Reference it as @/...\n"
)


def packages_dir() -> Path:
    """Return the active packages directory.

    Resolution order:

    1. A monkeypatched / reassigned :data:`PACKAGES_DIR` (the documented
       test hook) wins.
    2. Otherwise ``config_dir() / "packages"`` — which honours the
       ``KT_CONFIG_DIR`` environment variable and falls back to
       ``~/.kohakuterrarium/packages``.

    The directory is NOT created here — read paths check existence,
    install paths ``mkdir`` it themselves.
    """
    current = PACKAGES_DIR
    if current != _DEFAULT_PACKAGES_DIR:
        # Legacy callers may assign a str — coerce.
        return current if isinstance(current, Path) else Path(current)
    return config_dir() / "packages"


# Internal alias kept for the package-local modules that predate the
# public name.
_packages_dir = packages_dir


def project_dir() -> Path:
    """Return the local project's directory (it may not exist yet).

    A set :data:`PROJECT_DIR` wins. Otherwise it sits beside the packages
    directory, so a pinned PACKAGES_DIR (tests, embedders) pins it too, and
    the default is ``config_dir() / "project"``.
    """
    if PROJECT_DIR is not None:
        return Path(PROJECT_DIR)
    return packages_dir().parent / "project"


def ensure_local_project() -> Path:
    """Create the local project (folders and a minimal ``kohaku.yaml``) if missing; return its root."""
    root = project_dir()
    for sub in ("creatures", "terrariums", "modules"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    manifest = root / "kohaku.yaml"
    if not manifest.exists() and not (root / "kohaku.yml").exists():
        manifest.write_text(_PROJECT_MANIFEST, encoding="utf-8")
    return root.resolve()


def is_local_project(name: str) -> bool:
    """True for the local project's (empty) package name."""
    return name.strip() == LOCAL_PROJECT_NAME


def read_link(name: str) -> Path | None:
    """Read a package ``.link`` pointer file and return the target path."""
    link_file = _packages_dir() / f"{name}{LINK_SUFFIX}"
    if not link_file.exists():
        return None
    target = Path(link_file.read_text(encoding="utf-8").strip())
    if target.is_dir():
        return target
    logger.warning("Link target missing", package=name, target=str(target))
    return None


def write_link(name: str, target: Path) -> None:
    """Write a package ``.link`` pointer file."""
    link_file = _packages_dir() / f"{name}{LINK_SUFFIX}"
    link_file.write_text(str(target.resolve()), encoding="utf-8")


def remove_link(name: str) -> bool:
    """Remove a package ``.link`` pointer file if it exists."""
    link_file = _packages_dir() / f"{name}{LINK_SUFFIX}"
    if link_file.exists():
        link_file.unlink()
        return True
    return False


def get_package_root(name: str) -> Path | None:
    """Get the real root directory of an installed package.

    Checks, in order:

    0. The empty name is the local project (``None`` until it exists).
    1. ``.link`` pointer file for editable installs.
    2. Direct directory under :data:`PACKAGES_DIR`.
    3. Legacy symlink under :data:`PACKAGES_DIR`.
    """
    if is_local_project(name):
        root = project_dir()
        return root.resolve() if root.is_dir() else None

    link_target = read_link(name)
    if link_target is not None:
        return link_target

    pkg_dir = _packages_dir() / name
    if pkg_dir.is_dir():
        return pkg_dir.resolve()

    if pkg_dir.is_symlink():
        real = pkg_dir.resolve()
        if real.is_dir():
            return real

    return None


def get_package_path(name: str) -> Path | None:
    """Get the path to an installed package."""
    return get_package_root(name)


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
