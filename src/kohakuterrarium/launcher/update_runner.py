"""Orchestration of the first-launch + update flows.

Glue between :mod:`launcher.settings`, :mod:`launcher.sources`,
:mod:`launcher.venv_ops`, and :mod:`launcher._lock`.  Higher layers
(the bootloader, the backend API, the CLI verb) call into one of:

- :func:`first_install` — first launch, no venv present.
- :func:`run_update`    — user-triggered update; replaces current venv
                          via atomic swap with rollback on failure.
- :func:`maybe_update`  — honour ``update.mode`` on launch (notify or
                          auto).  Returns whether an update happened.
- :func:`rollback_to_previous` — swap ``venv.old`` back into place.
- :func:`reset_to_bundled`     — C2 recovery via bundled wheels.

All entry points produce ``UpdateResult`` so callers can render the
outcome consistently (CLI, API, splash UI).
"""

import datetime as _dt
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kohakuterrarium.launcher import settings as _settings
from kohakuterrarium.launcher._lock import LockBusy, UpdateLock
from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.paths import (
    bundled_wheels_dir,
    lock_path,
    venv_dir,
    venv_new_dir,
    venv_old_dir,
)
from kohakuterrarium.launcher.sources import (
    PACKAGE_NAME,
    is_auto_update_allowed,
    resolve_pip_args,
)
from kohakuterrarium.launcher.venv_ops import (
    VenvOpError,
    atomic_swap,
    create_venv,
    install_into,
    rollback as venv_rollback,
    smoke_test,
    write_wrapper_marker,
)


@dataclass
class UpdateResult:
    """Outcome of a runner entry point."""

    ok: bool
    version: str | None = None
    error: str | None = None
    restart_required: bool = False
    skipped_reason: str | None = None


# A progress callback takes (phase, percent, message) and is fire-and-
# forget — exceptions inside it are swallowed so a flaky UI can never
# break the update flow.  The bootloader / API wires the splash server
# in via this hook.
ProgressCallback = Callable[[str, float, str], None]


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _is_default_source(source: _settings.SourceConfig) -> bool:
    """True when the user hasn't explicitly chosen a non-default source.

    Bundled-first first-install only kicks in for default-source
    configs — if the user explicitly set ``source.kind = "git"`` or
    pinned a version with ``source.spec``, we honour that intent.
    """
    return source.kind == "pypi" and not source.spec


def _bundled_pip_args(wheels: Path, extras: list[str]) -> list[str]:
    """Build the offline-install pip arg list for the bundled wheels dir.

    Mirrors ``sources.resolve_pip_args`` for ``kind="bundled"`` but
    takes the wheels dir + extras directly so callers can skip the
    settings round-trip when they already know the wheels are present.
    """
    target = PACKAGE_NAME if not extras else f"{PACKAGE_NAME}[{','.join(extras)}]"
    return ["--no-index", "--find-links", str(wheels), target]


def _build_and_smoke(target: Path, pip_args: list[str]) -> str:
    """Create ``target/`` venv, install ``pip_args`` into it, smoke-test.

    Returns the framework version on success.  Cleans up ``target/`` on
    failure so the caller never has to handle half-built directories.
    """
    log = get_logger()
    try:
        create_venv(target)
        install_into(target, pip_args)
        version = smoke_test(target)
        write_wrapper_marker(target)
        log.info("runner: built %s at version %s", target, version)
        return version
    except VenvOpError:
        shutil.rmtree(target, ignore_errors=True)
        raise


def first_install() -> UpdateResult:
    """Build the managed venv from the current settings' source.

    Called when no venv exists at launcher startup.  Holds the update
    lock for the duration so a second simultaneous launch (rare but
    possible) doesn't race.

    **Bundled-first behaviour** (topic 06 / sub-plan 01): when the
    Briefcase bundle ships ``wheels-bundle/`` AND the user hasn't
    explicitly chosen a non-default source, install offline from the
    bundled wheels regardless of ``cfg.source.kind``.  Falls through
    to the configured source if the bundled install fails (so a
    corrupt wheel doesn't brick first launch).  ``cfg.source`` is
    unchanged — it represents the *update* source, which stays PyPI
    by default.
    """
    log = get_logger()
    cfg = _settings.load()

    wheels = bundled_wheels_dir()
    bundled_eligible = wheels is not None and _is_default_source(cfg.source)
    using_bundled = False

    if bundled_eligible:
        pip_args = _bundled_pip_args(wheels, cfg.source.extras)
        using_bundled = True
    else:
        try:
            pip_args = resolve_pip_args(cfg.source)
        except (ValueError, FileNotFoundError) as e:
            return UpdateResult(ok=False, error=f"source resolution failed: {e}")

    try:
        with UpdateLock(lock_path()):
            target = venv_dir()
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            try:
                version = _build_and_smoke(target, pip_args)
            except VenvOpError as e:
                # Bundled install failed (corrupt wheel, etc.) — fall
                # through to the configured source as recovery.  Only
                # when the user hasn't explicitly overridden source,
                # because a custom git/local source failing is a real
                # user-facing failure, not something to silently paper
                # over with PyPI.
                if using_bundled and _is_default_source(cfg.source):
                    log.warning(
                        "runner: bundled first_install failed (%s); "
                        "falling through to PyPI",
                        e,
                    )
                    try:
                        pip_args = resolve_pip_args(cfg.source)
                        version = _build_and_smoke(target, pip_args)
                        using_bundled = False
                    except (VenvOpError, ValueError, FileNotFoundError) as e2:
                        log.error("runner: PyPI fallback also failed: %s", e2)
                        return UpdateResult(ok=False, error=str(e2))
                else:
                    log.error("runner: first_install failed: %s", e)
                    return UpdateResult(ok=False, error=str(e))
            cfg.runtime.last_installed_version = version
            cfg.runtime.last_check_at = _now_iso()
            cfg.runtime.venv_path = str(target)
            cfg.runtime.install_source = "bundled" if using_bundled else cfg.source.kind
            _settings.save(cfg)
            return UpdateResult(ok=True, version=version, restart_required=False)
    except LockBusy as e:
        return UpdateResult(ok=False, error=f"another update is in progress: {e}")


def run_update() -> UpdateResult:
    """User-triggered atomic update: install fresh venv.new, swap, persist."""
    log = get_logger()
    cfg = _settings.load()
    try:
        pip_args = resolve_pip_args(cfg.source)
    except (ValueError, FileNotFoundError) as e:
        return UpdateResult(ok=False, error=f"source resolution failed: {e}")

    try:
        with UpdateLock(lock_path()):
            new = venv_new_dir()
            if new.exists():
                shutil.rmtree(new, ignore_errors=True)
            try:
                version = _build_and_smoke(new, pip_args)
            except VenvOpError as e:
                log.error("runner: update build failed: %s", e)
                return UpdateResult(ok=False, error=str(e))
            try:
                atomic_swap(venv_dir(), new, venv_old_dir())
            except VenvOpError as e:
                log.error("runner: atomic_swap failed: %s", e)
                shutil.rmtree(new, ignore_errors=True)
                return UpdateResult(ok=False, error=str(e))
            cfg.runtime.last_installed_version = version
            cfg.runtime.last_check_at = _now_iso()
            cfg.runtime.install_source = cfg.source.kind
            _settings.save(cfg)
            return UpdateResult(ok=True, version=version, restart_required=True)
    except LockBusy as e:
        return UpdateResult(ok=False, error=f"another update is in progress: {e}")


def maybe_update() -> UpdateResult:
    """Honour ``update.mode`` on launch.

    - ``manual`` → no-op (``skipped_reason="manual"``).
    - ``notify-on-launch`` → no install; caller surfaces the banner
      after independently checking version availability.
    - ``auto-on-launch`` → run :func:`run_update` if source allows
      auto-update; else no-op.
    """
    cfg = _settings.load()
    if cfg.update.mode == "manual":
        return UpdateResult(ok=True, skipped_reason="manual")
    if cfg.update.mode == "notify-on-launch":
        return UpdateResult(ok=True, skipped_reason="notify-only")
    if not is_auto_update_allowed(cfg.source):
        return UpdateResult(
            ok=True,
            skipped_reason=f"auto-update disabled for source.kind={cfg.source.kind}",
        )
    return run_update()


def rollback_to_previous() -> UpdateResult:
    """Swap ``venv.old/`` back into place.  No source resolution needed."""
    log = get_logger()
    try:
        with UpdateLock(lock_path()):
            try:
                venv_rollback(
                    current=venv_dir(),
                    backup=venv_old_dir(),
                    broken=Path(str(venv_dir()) + ".bad"),
                )
            except VenvOpError as e:
                log.error("runner: rollback failed: %s", e)
                return UpdateResult(ok=False, error=str(e))
            cfg = _settings.load()
            # We don't know the previous version exactly — leave the
            # field as-is for now; a follow-up smoke could read the
            # restored venv's ``__version__`` but adds latency we
            # don't need for rollback hot-path.
            _settings.save(cfg)
            return UpdateResult(ok=True, restart_required=True)
    except LockBusy as e:
        return UpdateResult(ok=False, error=f"another update is in progress: {e}")


def reset_to_bundled() -> UpdateResult:
    """C2 recovery — wipe ``venv/`` and reinstall from bundled wheels."""
    log = get_logger()
    cfg = _settings.load()
    # Force the source to bundled for this one operation.
    bundled_source = _settings.SourceConfig(kind="bundled", spec=None, extras=[])
    try:
        pip_args = resolve_pip_args(bundled_source)
    except FileNotFoundError as e:
        return UpdateResult(ok=False, error=str(e))

    try:
        with UpdateLock(lock_path()):
            target = venv_dir()
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            try:
                version = _build_and_smoke(target, pip_args)
            except VenvOpError as e:
                log.error("runner: reset_to_bundled failed: %s", e)
                return UpdateResult(ok=False, error=str(e))
            cfg.runtime.last_installed_version = version
            cfg.runtime.last_check_at = _now_iso()
            cfg.runtime.venv_path = str(target)
            cfg.runtime.install_source = "bundled"
            _settings.save(cfg)
            return UpdateResult(ok=True, version=version, restart_required=True)
    except LockBusy as e:
        return UpdateResult(ok=False, error=f"another update is in progress: {e}")


__all__ = [
    "UpdateResult",
    "first_install",
    "maybe_update",
    "reset_to_bundled",
    "rollback_to_previous",
    "run_update",
]
