"""Install / update / uninstall installed packages.

Git operations go through :mod:`kohakuterrarium.packages.git_backend`
which picks the best available implementation at call time:

    1. The native ``git`` binary via ``subprocess`` (fastest, used on
       desktop / CI where ``git`` is on ``$PATH``).
    2. The pure-Python ``dulwich`` library (slower but binary-free,
       used on **Android** Briefcase / Chaquopy where no ``git``
       binary ships in the APK).

Both backends present the same ``clone`` / ``pull`` API so the rest
of this module doesn't know which is running.
"""

import json
import os
import shutil
import time
import uuid
from pathlib import Path

from kohakuterrarium.packages import git_backend
from kohakuterrarium.packages import marketplace
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


def install_package_spec(
    spec: str,
    editable: bool = False,
    name_override: str | None = None,
) -> str:
    """Install by spec — ``@name`` / ``@name@version`` / ``@source/name`` / git URL / local path.

    Marketplace specs (``@``-prefixed) resolve through
    :func:`marketplace.resolve_sync` to a concrete git URL + the entry's
    canonical ``name`` (which is authoritative for the install id,
    regardless of the source repo's directory name) + the resolved
    version tag (which becomes the git ref the cloner checks out, so
    ``kt install @x@v1.2.0`` genuinely pins to that tag instead of
    silently grabbing default-branch HEAD).  Everything else falls
    through to :func:`install_package` unchanged.

    Editable installs of a marketplace package are unsupported — git
    clones cannot be ``-e`` linked; raise immediately rather than
    silently dropping the flag.
    """
    if marketplace.is_spec(spec):
        if editable:
            raise ValueError(
                "Cannot install a marketplace spec as editable; "
                "use `kt install -e <local-path>` instead"
            )
        entry, version = marketplace.resolve_sync(spec)
        url = marketplace.install_url(entry, version)
        # Prefer ``version.commit`` (immutable) over ``version.tag``
        # (mutable upstream — a tag can be force-moved).  CI on the
        # marketplace side fills in commit on PR merge; entries
        # without it fall back to the tag.
        ref = version.commit or version.tag
        logger.info(
            "Resolved marketplace spec",
            spec=spec,
            entry=entry.name,
            version=version.tag,
            ref=ref,
            url=url,
            source=entry.source_alias,
        )
        return install_package(
            url,
            editable=False,
            name_override=name_override or entry.name,
            ref=ref,
        )
    return install_package(spec, editable=editable, name_override=name_override)


def install_package(
    source: str,
    editable: bool = False,
    name_override: str | None = None,
    ref: str | None = None,
) -> str:
    """Install a creature/terrarium package.

    Args:
        source: Git URL or local path.
        editable: If True, store a pointer to the source directory
                  instead of copying (like pip -e).
        name_override: Override package name (default: from kohaku.yaml or dir name).
        ref: For git installs only — branch / tag / SHA to check out
             after clone.  Ignored for local-path installs.  Used by
             :func:`install_package_spec` to pin marketplace versions.

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
        return _install_from_git(source, name_override, ref=ref)
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

    Refuses to update packages that were installed at a pinned ref
    (recorded in ``.kt_install_info.json``).  ``git pull --ff-only``
    on a detached-HEAD checkout fails with a confusing message; the
    user wanted reproducibility, so the correct next move is
    ``kt install @<name>@<newversion>``, not silent state mutation.

    Raises
    ------
    FileNotFoundError
        If no package with ``name`` exists under
        :data:`~kohakuterrarium.packages.locations.PACKAGES_DIR`.
    RuntimeError
        If the package is not a git clone, or ``git pull`` fails, or
        the install was pinned to a specific ref.
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

    # Refuse pinned installs cleanly — ``git pull --ff-only`` against
    # a detached HEAD (typical after ``git clone -b <tag>``) produces
    # a confusing "You are not currently on a branch" error.  Direct
    # the user at the right next move.
    info = _read_install_info(target)
    if info and info.get("ref"):
        raise RuntimeError(
            f"{name} was installed at pinned ref {info['ref']!r}; "
            f"`git pull` would be a no-op against a detached HEAD.  "
            f"To move to a newer version, run: "
            f"kt install @{name}@<newversion>"
        )

    logger.info(
        "Updating package",
        package=name,
        path=str(target),
        editable=is_editable,
    )
    try:
        git_backend.pull_repo(target)
    except RuntimeError as e:
        raise RuntimeError(f"Git pull failed for {name}: {e}") from e

    _validate_package(target, name)
    _install_python_deps(target)
    logger.info("Package updated", package=name, path=str(target))
    return name


def _install_from_git(
    url: str, name_override: str | None = None, ref: str | None = None
) -> str:
    """Clone a git repo into packages directory.

    Three branches based on (target exists, ref provided):

      * **fresh + no ref**: plain clone (default branch HEAD).
      * **fresh + ref**: clone-with-ref.  Cloner pins to the requested
        branch / tag / SHA.
      * **existing + no ref**: ``git pull --ff-only`` in place
        (unchanged behaviour — the user is "updating to latest").
      * **existing + ref**: rmtree + clone-fresh-with-ref.  Pulling
        in place could leave the working tree on the previously-
        installed ref (mutable tag, different branch, etc.) and the
        ``kt install @x@v1.0.0`` contract is "I want v1.0.0 of x" —
        not "update x if I already have it."  Throwing away the
        previous checkout is the simplest way to honour that
        contract cross-backend without per-backend "is this the same
        ref?" probing.
    """
    # Determine package name from URL
    repo_name = url.rstrip("/").split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]

    name = name_override or repo_name
    target = _packages_dir() / name

    # Remove any stale .link file (switching from editable to cloned)
    remove_link(name)

    if target.exists():
        if ref:
            # Pinned re-install — transactional: clone+validate in a
            # temp dir, swap atomically, keep the previous checkout
            # as a backup until the swap lands.  If the clone or
            # validation fails the user keeps their existing working
            # install.  Pull-in-place is wrong here because it would
            # silently keep the old ref.
            logger.info(
                "Replacing existing checkout with pinned ref",
                package=name,
                ref=ref,
            )
            _swap_in_clone(url, target, name, ref=ref)
        else:
            # Update existing — fast-forward against the tracked
            # branch.
            logger.info("Updating package", package=name)
            git_backend.pull_repo(target)
            _validate_package(target, name)
    else:
        # Fresh clone — pin to ref if provided.  No existing install
        # to protect, so a clone-in-place is fine.
        logger.info("Cloning package", package=name, url=url, ref=ref or "default")
        git_backend.clone_repo(url, target, ref=ref)
        try:
            _validate_package(target, name)
        except Exception:
            # Fresh install failed validation — tear it down so the
            # next attempt doesn't see a poisoned dir.
            _force_rmtree(target)
            raise

    _install_python_deps(target)
    _write_install_info(target, source=url, ref=ref)
    logger.info("Package installed", package=name, path=str(target))
    return name


def _swap_in_clone(url: str, target: Path, name: str, *, ref: str) -> None:
    """Clone ``url@ref`` into a temp dir, validate, then swap into ``target``.

    Guarantees: if the clone or manifest validation fails, ``target``
    is left untouched and the user's existing install keeps working.
    Only after a clean validated clone do we touch ``target``.  The
    swap uses two ``os.replace`` calls so the window where ``target``
    doesn't exist is just one filesystem op wide.
    """
    suffix = uuid.uuid4().hex[:8]
    staging = target.parent / f"{name}.tmp-{suffix}"
    backup = target.parent / f"{name}.bak-{suffix}"

    # Stage: clone + validate in isolation.
    try:
        git_backend.clone_repo(url, staging, ref=ref)
        _validate_package(staging, name)
    except Exception:
        if staging.exists():
            _force_rmtree(staging)
        raise

    # Move old out of the way.  If THIS fails (e.g. Windows file lock
    # on the old install), we never touch ``target`` — clean up the
    # validated staging clone so it doesn't leak.
    try:
        os.replace(target, backup)
    except OSError:
        if staging.exists():
            _force_rmtree(staging)
        raise

    # Move new into place.  If THIS fails, restore the old install
    # from backup so the user keeps something working.
    try:
        os.replace(staging, target)
    except OSError:
        os.replace(backup, target)
        if staging.exists():
            _force_rmtree(staging)
        raise

    # Swap succeeded — drop the backup.  Failure to remove the backup
    # is non-fatal; warn but keep the working new install.
    try:
        _force_rmtree(backup)
    except OSError as exc:
        logger.warning(
            "Failed to remove backup of previous install",
            package=name,
            backup=str(backup),
            error=str(exc),
        )


def _write_install_info(target: Path, *, source: str, ref: str | None) -> None:
    """Persist install metadata so update_package can reason about it.

    Currently records ``{source, ref, written}`` — enough for
    ``update_package`` to detect a pinned install (``ref`` set) and
    refuse a meaningless ``git pull`` against a detached-HEAD tree.
    Marketplace-aware update flow (re-resolve to the newest
    compatible version) can read the same file later without
    breaking the format.
    """
    info_path = target / ".kt_install_info.json"
    payload = {
        "source": source,
        "ref": ref,
        "written": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    try:
        info_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError as exc:
        # Non-fatal — install succeeded; we just lose the metadata
        # marker.  Log + move on.
        logger.warning(
            "Failed to write .kt_install_info.json",
            package=target.name,
            error=str(exc),
        )


def _read_install_info(target: Path) -> dict | None:
    """Read ``.kt_install_info.json`` if it exists.  None on missing/corrupt."""
    info_path = target / ".kt_install_info.json"
    if not info_path.exists():
        return None
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


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
