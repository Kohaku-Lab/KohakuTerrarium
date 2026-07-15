"""Orchestrate first installation, updates, rollback, and reset.

Operations coordinate feed resolution, archive handling, structural validation,
and atomic active-pointer changes under a shared update lock. Public entry
points return :class:`UpdateResult` for consistent CLI, API, and splash output.
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
    """Describe the outcome and launch implications of an update operation."""

    ok: bool
    version: str | None = None
    build_id: str | None = None
    error: str | None = None
    restart_required: bool = False
    skipped_reason: str | None = None


# Progress reporting must not change the outcome of an update operation.
ProgressCallback = Callable[[str, float, str], None]


def _noop_progress(phase: str, percent: float, message: str) -> None:
    return


def _iso_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _safe_progress(cb: ProgressCallback, phase: str, percent: float, msg: str) -> None:
    try:
        cb(phase, percent, msg)
    except Exception as e:  # pragma: no cover - callback isolation
        get_logger().debug("progress callback raised: %s", e)


def _pick_bundled_tarball() -> Path | None:
    """Return the single ``kohakuterrarium-*.tar.*`` in the bundled-release dir."""
    root = bundled_release_dir()
    if root is None:
        return None
    candidates = sorted(root.glob("kohakuterrarium-*.tar.*"))
    return candidates[0] if candidates else None


def _bundled_version_from_filename(tarball: Path) -> str:
    """Parse the release version from a bundled archive filename."""
    stem = tarball.name
    # Extensions are removed before splitting because platform tags contain dashes.
    for ext in (".tar.zst", ".tar.gz", ".tgz", ".tzst", ".tar"):
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    parts = stem.split("-")
    if len(parts) >= 2 and parts[0] == "kohakuterrarium":
        return parts[1]
    return "bundled"


def _install_from_bundled(progress: ProgressCallback) -> UpdateResult:
    """Validate and activate the bundled offline release archive."""
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


def _install_from_feed(
    cfg: _settings.AppSettings,
    progress: ProgressCallback,
    *,
    is_update: bool,
) -> UpdateResult:
    """Resolve and install a feed release unless an update is current.

    First installation always proceeds. Update mode returns a successful
    ``up-to-date`` result when the resolved version is already active.
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
    """Download, validate, promote, and activate a release target."""
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


def _first_install_locked(progress: ProgressCallback) -> UpdateResult:
    """Install the initial release while the caller holds the update lock."""
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
    """Install and activate the initial release under the update lock.

    A bundled archive is preferred for offline startup; feed installation is
    used when no bundle exists or the bundled release fails validation.
    """
    progress = progress or _noop_progress
    try:
        with UpdateLock(lock_path()):
            return _first_install_locked(progress)
    except LockBusy as e:
        return UpdateResult(ok=False, error=f"another update is in progress: {e}")


def run_update(progress: ProgressCallback | None = None) -> UpdateResult:
    """Install a newer release from the active feed and persist the result."""
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
                # Collection runs only after activation so the live release is retained.
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
    """Apply the configured launch-time update policy."""
    cfg = _settings.load()
    if cfg.update.mode == "manual":
        return UpdateResult(ok=True, skipped_reason="manual")
    if cfg.update.mode == "notify-on-launch":
        return UpdateResult(ok=True, skipped_reason="notify-only")
    return run_update(progress)


def rollback() -> UpdateResult:
    """Activate the most recent non-active release and require a restart."""
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
    """Remove all managed releases and perform a locked initial install."""
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
    """Resolve the latest feed target without downloading or activating it."""
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
