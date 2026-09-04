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

from kohakuterrarium.errors import PackageError
from kohakuterrarium.packages import git_backend
from kohakuterrarium.packages import marketplace
from kohakuterrarium.packages.locations import _packages_dir
from kohakuterrarium.packages.locations import get_package_root
from kohakuterrarium.packages.locations import remove_link
from kohakuterrarium.packages.locations import write_link
from kohakuterrarium.packages.manifest import DEP_POLICIES
from kohakuterrarium.packages.manifest import _force_rmtree
from kohakuterrarium.packages.manifest import _install_python_deps
from kohakuterrarium.packages.manifest import _load_manifest
from kohakuterrarium.packages.manifest import _validate_package
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _check_deps_policy(deps: str) -> None:
    """Reject an unknown ``deps`` policy BEFORE any clone/copy happens."""
    if deps not in DEP_POLICIES:
        raise PackageError(
            f"Unknown deps policy: {deps!r} (expected one of {DEP_POLICIES})"
        )


def ensure(spec: str, *, deps: str = "auto") -> str:
    """Idempotent install — return the package name, installing if needed.

    The missing primitive for scripts: ``kt.packages.ensure("@kt-biome")``
    at the top of a batch job guarantees the package is present without
    re-resolving the marketplace (or re-cloning) on every run.

    If a package with the spec's name is already installed, returns
    immediately — **no version check is performed**, even for pinned
    specs like ``@pkg@v1.2.0``. Use :func:`install_package_spec` to
    force a specific version onto an existing install.

    Args:
        spec: Anything :func:`install_package_spec` accepts —
            ``@name`` / ``@name@version`` / ``@source/name``, a git
            URL, or a local directory path.
        deps: Dependency policy for a fresh install (``"auto"`` /
            ``"never"``); ignored when the package is already present.

    Returns:
        The installed package name (usable in ``@<name>/...`` refs).
    """
    _check_deps_policy(deps)
    name: str | None = None
    if marketplace.is_spec(spec):
        _source, name, _version = marketplace.parse_spec(spec)
        if "/" in name:
            # ``@pkg/sub/path`` parses as ``@source/name`` — for the
            # idempotency check, the candidate package is the first
            # segment. install_package_spec raises the disambiguating
            # error if it really is a path reference.
            name = spec[1:].split("/", 1)[0]
    elif not (
        spec.startswith("http://")
        or spec.startswith("https://")
        or spec.endswith(".git")
    ):
        source_path = Path(spec)
        if source_path.is_dir():
            name = _load_manifest(source_path).get("name", source_path.name)
    else:
        repo_name = spec.rstrip("/").split("/")[-1]
        name = repo_name[:-4] if repo_name.endswith(".git") else repo_name

    if name and get_package_root(name) is not None:
        logger.debug("Package already installed", package=name, spec=spec)
        return name
    return install_package_spec(spec, deps=deps)


def install_package_spec(
    spec: str,
    editable: bool = False,
    name_override: str | None = None,
    *,
    deps: str = "auto",
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

    ``deps`` is the Python-dependency policy (``"auto"`` / ``"never"``)
    threaded down to :func:`install_package`.
    """
    _check_deps_policy(deps)
    if marketplace.is_spec(spec):
        if editable:
            raise ValueError(
                "Cannot install a marketplace spec as editable; "
                "use `kt install -e <local-path>` instead"
            )
        try:
            entry, version = marketplace.resolve_sync(spec)
        except marketplace.MarketplaceNotFoundError as exc:
            # ``@pkg/sub/path`` is a config *path reference*, not an
            # install spec — the slash makes parse_spec read it as
            # ``@source/name``.  Point the user at the right command.
            _source, name, _version = marketplace.parse_spec(spec)
            if "/" in name:
                pkg = spec[1:].split("/", 1)[0]
                raise marketplace.MarketplaceNotFoundError(
                    f"{exc} — {spec!r} looks like a package path "
                    f"reference (used in configs / kt run), not an "
                    f"install spec. To install the package, run: "
                    f"kt install @{pkg}"
                ) from exc
            raise
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
        _alias, _name, requested = marketplace.parse_spec(spec)
        return install_package(
            url,
            editable=False,
            name_override=name_override or entry.name,
            ref=ref,
            deps=deps,
            intent={
                "spec": spec,
                "source_alias": entry.source_alias or None,
                "pinned": requested is not None,
                "version": version.tag,
            },
        )
    return install_package(
        spec, editable=editable, name_override=name_override, deps=deps
    )


def install_package(
    source: str,
    editable: bool = False,
    name_override: str | None = None,
    ref: str | None = None,
    *,
    deps: str = "auto",
    intent: dict | None = None,
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
        deps: Python-dependency policy — ``"auto"`` installs the
              manifest's ``python_dependencies`` + ``requirements.txt``
              via ``sys.executable -m pip``; ``"never"`` skips them.

    Returns:
        Installed package name.
    """
    _check_deps_policy(deps)
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
        return _install_from_git(
            source, name_override, ref=ref, deps=deps, intent=intent
        )
    elif source_path.is_dir():
        # Local directory
        return _install_from_local(source_path, editable, name_override, deps=deps)
    else:
        raise ValueError(
            f"Cannot install from: {source}. "
            "Provide a git URL or local directory path."
        )


def update_package(name: str, *, deps: str = "auto") -> str:
    """Move an installed, non-editable, git-backed package to its newest version.

    Four cases. A package the user pinned to an explicit version is refused,
    because that is what a pin means. A marketplace package installed without a
    version is re-resolved and swapped transactionally — resolution is what
    "newest" means, and ``git pull`` cannot express it against the detached
    HEAD every pinned clone leaves behind. A plain git clone is fast-forwarded.
    Editable and non-git packages are the caller's to filter.

    Raises FileNotFoundError when nothing by that name is installed, and
    PackageError for a refusal or a failed update.
    """
    _check_deps_policy(deps)
    # Resolve through ``.link`` pointers / symlinks to the real checkout so
    # ``git -C`` sees the literal path a submodule's gitdir resolves against.
    target = get_package_root(name)
    if target is None:
        raise FileNotFoundError(f"Package not installed: {name}")
    target = target.resolve()
    if not (target / ".git").exists():
        raise PackageError(f"Package is not a git clone: {name}")

    info = _read_install_info(target) or {}

    # A missing ``pinned`` key predates intent tracking. Those installs were
    # overwhelmingly auto-resolved, and reading them as pinned would keep the
    # long-standing "update always refuses" behaviour for every one of them.
    if info.get("pinned"):
        raise PackageError(
            f"{name} was installed at pinned version "
            f"{info.get('version') or info.get('ref')!r}. "
            f"To move it, run: kt install @{name}@<newversion>"
        )

    spec = info.get("spec")
    if spec and marketplace.is_spec(spec):
        return _update_from_marketplace(name, target, info, deps=deps)

    # Installs predating intent tracking record a ref but no spec. They were
    # cloned at that ref, so they sit on a detached HEAD where `git pull`
    # exits 0 and changes nothing. Re-resolve them by name instead.
    if spec is None and info.get("ref") and marketplace.has_entry(name):
        return _update_from_marketplace(name, target, info, deps=deps)

    if git_backend.is_dirty(target):
        raise PackageError(
            f"{name} has local modifications at {target}. `kt update` will not "
            f"overwrite them. Commit or discard them, or reinstall with "
            f"`kt install {info.get('source') or name}`."
        )

    # A detached HEAD cannot fast-forward. Git reports success and does
    # nothing, so refuse rather than claim an update that did not happen.
    if git_backend.is_detached(target):
        raise PackageError(
            f"{name} is checked out at a fixed commit, not a branch, so "
            f"`git pull` cannot advance it. Reinstall it instead: "
            f"kt install {info.get('source') or name}"
        )

    logger.info("Updating package", package=name, path=str(target))
    try:
        git_backend.pull_repo(target)
    except RuntimeError as e:
        raise PackageError(f"Git pull failed for {name}: {e}") from e

    _validate_package(target, name)
    _install_python_deps(target, deps=deps)
    logger.info("Package updated", package=name, path=str(target))
    return name


def _update_from_marketplace(
    name: str, target: Path, info: dict, *, deps: str = "auto"
) -> str:
    """Re-resolve an unpinned marketplace install and swap in the new version.

    Uses the recorded source alias so ``@myfork/pkg`` cannot silently
    re-resolve against the default source.
    """
    alias = info.get("source_alias")
    lookup = f"@{alias}/{name}" if alias else f"@{name}"
    try:
        entry, version = marketplace.resolve_sync(lookup)
    except marketplace.MarketplaceError as exc:
        raise PackageError(f"Could not resolve {lookup} for update: {exc}") from exc

    if version.tag and version.tag == info.get("version"):
        logger.info("Package already current", package=name, version=version.tag)
        return name

    url = marketplace.install_url(entry, version)
    ref = version.commit or version.tag
    logger.info(
        "Updating package from marketplace",
        package=name,
        from_version=info.get("version"),
        to_version=version.tag,
    )
    # Stage, validate, then swap: a failed clone leaves the working install.
    _swap_in_clone(url, target, name, ref=ref)
    _install_python_deps(target, deps=deps)
    _write_install_info(
        target,
        source=url,
        ref=ref,
        spec=info.get("spec"),
        source_alias=alias,
        pinned=False,
        version=version.tag,
    )
    logger.info("Package updated", package=name, version=version.tag)
    return name


def _install_from_git(
    url: str,
    name_override: str | None = None,
    ref: str | None = None,
    *,
    deps: str = "auto",
    intent: dict | None = None,
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

    _install_python_deps(target, deps=deps)
    _write_install_info(target, source=url, ref=ref, **(intent or {}))
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


def _write_install_info(
    target: Path,
    *,
    source: str,
    ref: str | None,
    spec: str | None = None,
    source_alias: str | None = None,
    pinned: bool = False,
    version: str | None = None,
) -> None:
    """Persist what the user asked for, not only what was resolved.

    ``pinned`` is true only when the spec named an explicit version. Every
    marketplace install resolves to a concrete ref, so ``ref`` alone cannot
    distinguish "give me the newest" from "give me exactly this".
    """
    info_path = target / ".kt_install_info.json"
    payload = {
        "source": source,
        "ref": ref,
        "spec": spec,
        "source_alias": source_alias,
        "pinned": pinned,
        "version": version,
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
    source: Path,
    editable: bool,
    name_override: str | None = None,
    *,
    deps: str = "auto",
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
    _install_python_deps(source if editable else target, deps=deps)
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
