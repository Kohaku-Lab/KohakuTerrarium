"""Orchestration of first-launch + update + rollback.

Glue layer over :mod:`launcher.settings`, :mod:`launcher.feeds`,
:mod:`launcher.downloader`, and :mod:`launcher.tree_ops`. Callers
(bootloader, API, CLI verb) drive one of:

- :func:`first_install`  — no active pointer; install from bundled
  release if present, else from the configured feed.
- :func:`run_update`     — user-triggered: probe feed, download
  newer release, smoke, atomic-pointer-swap.
- :func:`maybe_update`   — honour ``update.mode`` on launch.
- :func:`rollback`       — revert pointer to the previous version.
- :func:`reset`          — wipe ``versions/`` and re-run first_install.

All entry points produce :class:`UpdateResult` so callers can render
the outcome consistently (CLI, API, splash UI).
"""

import datetime as _dt
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kohakuterrarium.launcher import settings as _settings
from kohakuterrarium.launcher._lock import LockBusy, UpdateLock
from kohakuterrarium.launcher.downloader import (
    DownloadError,
    extract_tarball,
    fetch_and_extract,
)
from kohakuterrarium.launcher.feeds import FeedError, ReleaseTarget, resolve_feed
from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.paths import (
    bundled_release_dir,
    lock_path,
    runtime_dir,
    versions_dir,
)
from kohakuterrarium.launcher.tree_ops import (
    TreeOpError,
    clear_active_pointer,
    gc_old_versions,
    partial_dir_for,
    promote_partial,
    read_active_pointer,
    remove_partial,
    revert_active_pointer,
    smoke_test_tree,
    sweep_stale_partials,
    write_active_pointer,
)


@dataclass
class UpdateResult:
    """Outcome of a runner entry point."""

    ok: bool
    version: str | None = None
    build_id: str | None = None
    error: str | None = None
    restart_required: bool = False
    skipped_reason: str | None = None


# Progress callback: (phase, percent, message). Fire-and-forget;
# exceptions inside it are swallowed.
ProgressCallback = Callable[[str, float, str], None]


def _noop_progress(phase: str, percent: float, message: str) -> None:
    return


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _safe_progress(cb: ProgressCallback, phase: str, percent: float, msg: str) -> None:
    try:
        cb(phase, percent, msg)
    except Exception as e:  # pragma: no cover - defensive
        get_logger().debug("progress callback raised: %s", e)


# ── Bundled-release first-install ───────────────────────────────────


def _pick_bundled_tarball() -> Path | None:
    """Return the single ``kohakuterrarium-*.tar.*`` in the bundled-release dir."""
    root = bundled_release_dir()
    if root is None:
        return None
    candidates = sorted(root.glob("kohakuterrarium-*.tar.*"))
    return candidates[0] if candidates else None


def _bundled_version_from_filename(tarball: Path) -> str:
    """Parse ``kohakuterrarium-<version>-<plat>-py<X.Y>.tar.<ext>`` for the version."""
    stem = tarball.name
    # Strip trailing ``.tar.<ext>``
    for ext in (".tar.zst", ".tar.gz", ".tgz", ".tzst", ".tar"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    parts = stem.split("-")
    if len(parts) >= 2 and parts[0] == "kohakuterrarium":
        return parts[1]
    return "bundled"


def _install_from_bundled(progress: ProgressCallback) -> UpdateResult:
    """Extract the offline tarball into ``versions/<v>/`` and point at it."""
    log = get_logger()
    tarball = _pick_bundled_tarball()
    if tarball is None:
        return UpdateResult(
            ok=False, error="no bundled release tarball found in the briefcase shell"
        )
    version = _bundled_version_from_filename(tarball)
    _safe_progress(progress, "extract", 20.0, f"Unpacking bundled {version}")
    partial = partial_dir_for(version)
    if partial.exists():
        shutil.rmtree(partial, ignore_errors=True)
    try:
        extract_tarball(tarball, partial)
    except DownloadError as e:
        remove_partial(version)
        return UpdateResult(ok=False, error=f"bundled extract failed: {e}")
    _safe_progress(progress, "smoke", 70.0, "Smoke testing")
    try:
        smoke_test_tree(partial)
    except TreeOpError as e:
        remove_partial(version)
        return UpdateResult(ok=False, error=f"bundled smoke failed: {e}")
    try:
        final = promote_partial(version)
    except TreeOpError as e:
        remove_partial(version)
        return UpdateResult(ok=False, error=str(e))
    write_active_pointer(version, build_id="bundled")
    log.info("runner: bundled first_install promoted %s", final)
    _safe_progress(progress, "done", 100.0, f"Installed {version}")
    return UpdateResult(ok=True, version=version, build_id="bundled")


# ── Feed-driven install / update ────────────────────────────────────


def _install_from_feed(
    cfg: _settings.AppSettings,
    progress: ProgressCallback,
    *,
    is_update: bool,
) -> UpdateResult:
    """Resolve feed → download → smoke → swap pointer.

    Used by both first_install (when no bundled tarball) and run_update.
    Skips re-install when the resolved version equals the active one
    (``is_update=True`` only).
    """
    _safe_progress(progress, "resolve", 5.0, "Checking for updates")
    try:
        target = resolve_feed(cfg)
    except FeedError as e:
        return UpdateResult(ok=False, error=f"feed resolution failed: {e}")

    if is_update:
        current = read_active_pointer()
        if current is not None and current.version == target.version:
            _safe_progress(progress, "done", 100.0, f"Already on {target.version}")
            return UpdateResult(
                ok=True,
                version=target.version,
                skipped_reason="up-to-date",
            )

    return _download_smoke_swap(target, progress)


def _download_smoke_swap(
    target: ReleaseTarget, progress: ProgressCallback
) -> UpdateResult:
    log = get_logger()
    partial = partial_dir_for(target.version)
    cache_dir = runtime_dir() / "downloads"
    cache_dir.mkdir(parents=True, exist_ok=True)
    tarball = cache_dir / Path(target.url).name

    def _dl_progress(done: int, total: int) -> None:
        pct = (done * 60.0 / total + 10.0) if total > 0 else 35.0
        _safe_progress(progress, "download", pct, f"{done // 1024} KiB")

    try:
        fetch_and_extract(
            target.url, target.sha256, tarball, partial, progress=_dl_progress
        )
    except DownloadError as e:
        remove_partial(target.version)
        if tarball.exists():
            try:
                tarball.unlink()
            except OSError:
                pass
        return UpdateResult(ok=False, error=str(e))

    _safe_progress(progress, "smoke", 80.0, "Smoke testing")
    try:
        smoke_test_tree(partial)
    except TreeOpError as e:
        remove_partial(target.version)
        return UpdateResult(ok=False, error=f"smoke failed: {e}")

    try:
        final = promote_partial(target.version)
    except TreeOpError as e:
        remove_partial(target.version)
        return UpdateResult(ok=False, error=str(e))

    write_active_pointer(target.version, target.build_id)
    log.info("runner: promoted %s (build %s)", final, target.build_id)
    try:
        tarball.unlink()
    except OSError:
        pass
    _safe_progress(progress, "done", 100.0, f"Installed {target.version}")
    return UpdateResult(
        ok=True,
        version=target.version,
        build_id=target.build_id,
        restart_required=True,
    )


# ── Public entry points ─────────────────────────────────────────────


def _first_install_locked(progress: ProgressCallback) -> UpdateResult:
    """First-install body — caller must already hold the update flock."""
    cfg = _settings.load()
    sweep_stale_partials()
    if bundled_release_dir() is not None:
        result = _install_from_bundled(progress)
        if result.ok:
            cfg.runtime.active_version = result.version
            cfg.runtime.active_build_id = result.build_id
            cfg.runtime.last_check_at = _iso_now()
            cfg.runtime.last_check_error = None
            _settings.save(cfg)
            return result
        get_logger().warning(
            "runner: bundled first_install failed (%s); falling through to feed",
            result.error,
        )
    result = _install_from_feed(cfg, progress, is_update=False)
    if result.ok:
        cfg.runtime.active_version = result.version
        cfg.runtime.active_build_id = result.build_id
        cfg.runtime.last_check_at = _iso_now()
        cfg.runtime.last_check_error = None
    else:
        cfg.runtime.last_check_at = _iso_now()
        cfg.runtime.last_check_error = result.error
    _settings.save(cfg)
    return result


def first_install(progress: ProgressCallback | None = None) -> UpdateResult:
    """Build ``versions/<v>/`` + write pointer. Called when no pointer.

    Prefers the bundled-release tarball when the briefcase shell
    shipped one; falls back to the configured feed when not (dev
    install / minimal bundle).
    """
    progress = progress or _noop_progress
    try:
        with UpdateLock(lock_path()):
            return _first_install_locked(progress)
    except LockBusy as e:
        return UpdateResult(ok=False, error=f"another update is in progress: {e}")


def run_update(progress: ProgressCallback | None = None) -> UpdateResult:
    """User-triggered update via the active feed."""
    progress = progress or _noop_progress
    cfg = _settings.load()
    try:
        with UpdateLock(lock_path()):
            sweep_stale_partials()
            result = _install_from_feed(cfg, progress, is_update=True)
            cfg.runtime.last_check_at = _iso_now()
            if result.ok and result.skipped_reason is None:
                cfg.runtime.active_version = result.version
                cfg.runtime.active_build_id = result.build_id
                cfg.runtime.last_check_error = None
                # GC after successful promote.
                ptr = read_active_pointer()
                installed = []
                if ptr is not None:
                    installed.append(ptr.version)
                gc_old_versions(
                    keep=cfg.update.keep_versions,
                    always_keep=set(installed),
                )
            elif not result.ok:
                cfg.runtime.last_check_error = result.error
            else:
                cfg.runtime.last_check_error = None
            _settings.save(cfg)
            return result
    except LockBusy as e:
        return UpdateResult(ok=False, error=f"another update is in progress: {e}")


def maybe_update(progress: ProgressCallback | None = None) -> UpdateResult:
    """Honour ``update.mode`` on launch."""
    cfg = _settings.load()
    if cfg.update.mode == "manual":
        return UpdateResult(ok=True, skipped_reason="manual")
    if cfg.update.mode == "notify-on-launch":
        return UpdateResult(ok=True, skipped_reason="notify-only")
    # auto-on-launch
    return run_update(progress)


def rollback() -> UpdateResult:
    """Revert pointer to the previous installed version."""
    try:
        with UpdateLock(lock_path()):
            try:
                prev = revert_active_pointer()
            except TreeOpError as e:
                return UpdateResult(ok=False, error=str(e))
            cfg = _settings.load()
            cfg.runtime.active_version = prev.version
            cfg.runtime.active_build_id = prev.build_id
            cfg.runtime.last_check_at = _iso_now()
            _settings.save(cfg)
            return UpdateResult(
                ok=True,
                version=prev.version,
                build_id=prev.build_id,
                restart_required=True,
            )
    except LockBusy as e:
        return UpdateResult(ok=False, error=f"another update is in progress: {e}")


def reset(progress: ProgressCallback | None = None) -> UpdateResult:
    """Wipe ``versions/`` and re-run first_install."""
    progress = progress or _noop_progress
    try:
        with UpdateLock(lock_path()):
            clear_active_pointer()
            root = versions_dir()
            if root.exists():
                shutil.rmtree(root, ignore_errors=True)
            return _first_install_locked(progress)
    except LockBusy as e:
        return UpdateResult(ok=False, error=f"another update is in progress: {e}")


def probe_only() -> UpdateResult:
    """Resolve the feed without installing; report what would be installed.

    Used by ``kt self-update --check-only`` and the API's status endpoint.
    """
    cfg = _settings.load()
    try:
        target = resolve_feed(cfg, force_refresh=True)
    except FeedError as e:
        return UpdateResult(ok=False, error=str(e))
    current = read_active_pointer()
    skipped = "up-to-date" if current and current.version == target.version else None
    return UpdateResult(
        ok=True,
        version=target.version,
        build_id=target.build_id,
        skipped_reason=skipped,
    )


# Re-export Path import name for type hints in tests
__all__ = [
    "UpdateResult",
    "ProgressCallback",
    "first_install",
    "run_update",
    "maybe_update",
    "rollback",
    "reset",
    "probe_only",
]
