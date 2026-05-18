"""Launcher entry point.

Flow:

1. Parse minimal launcher flags (``--reset-settings``, ``--reset-runtime``,
   ``--no-exec``, ``--splash-demo``).
2. Wipe legacy 06 venv if present (one-shot migration).
3. Load (or create) ``app-settings.json``.
4. Read ``runtime/active``:
   - absent → :func:`update_runner.first_install` (bundled-release tarball or feed)
   - present → :func:`update_runner.maybe_update` per ``update.mode``
5. ``os.execv`` into ``versions/<active>/scripts/kt`` with the original argv.
"""

import argparse
import os
import sys
import time

from kohakuterrarium.launcher import migration as _migration
from kohakuterrarium.launcher import settings as _settings
from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.paths import (
    bundled_release_dir,
    kt_script,
    runtime_dir,
    settings_path,
    version_dir,
)
from kohakuterrarium.launcher.splash_window import open_splash
from kohakuterrarium.launcher.tree_ops import read_active_pointer
from kohakuterrarium.launcher.update_runner import (
    UpdateResult,
    first_install,
    maybe_update,
    reset as runner_reset,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="kt-launcher",
        description="KohakuTerrarium launcher — manages versioned releases.",
        add_help=False,  # let the framework's own argparse handle --help post-exec
    )
    parser.add_argument(
        "--reset-settings",
        action="store_true",
        help="Overwrite app-settings.json with defaults and exit.",
    )
    parser.add_argument(
        "--reset-runtime",
        action="store_true",
        help="Wipe runtime/versions/ and re-run first_install.",
    )
    parser.add_argument(
        "--no-exec",
        action="store_true",
        help="Resolve launcher state and exit (don't exec into the version tree).",
    )
    parser.add_argument(
        "--splash-demo",
        action="store_true",
        help="Open the splash window with a scripted progress sequence.",
    )
    known, _ = parser.parse_known_args(argv)
    return known


def main(argv: list[str] | None = None) -> int:
    """Bootstrap entry called by ``__briefcase__.py`` and ``python -m kohakuterrarium.launcher``."""
    args = _parse_args(argv)
    log = get_logger()

    runtime_dir().mkdir(parents=True, exist_ok=True)

    if args.reset_settings:
        _settings.reset()
        log.info("launcher: reset settings (wrote defaults to %s)", settings_path())
        return 0

    if args.reset_runtime:
        log.info("launcher: --reset-runtime running first_install fresh")
        result = runner_reset()
        if not result.ok:
            log.error("launcher: reset failed: %s", result.error)
            return 6
        log.info("launcher: reset succeeded at version %s", result.version)
        return 0

    if args.splash_demo:
        return _run_splash_demo(log)

    # One-shot 06 cleanup. Idempotent; no-op when nothing is there.
    _migration.wipe_legacy_venv()

    cfg = _settings.load()  # noqa: F841  - settings autoload also creates defaults

    if args.no_exec:
        ptr = read_active_pointer()
        log.info(
            "launcher: --no-exec — runtime_dir=%s active=%s bundled_release=%s "
            "feed.kind=%s channel=%s pinned=%s update_mode=%s",
            runtime_dir(),
            ptr.version if ptr else None,
            bundled_release_dir(),
            cfg.feed.kind,
            cfg.channel,
            cfg.pinned_version,
            cfg.update.mode,
        )
        return 0

    pointer = read_active_pointer()
    if pointer is None:
        log.info("launcher: no active pointer — first_install")
        result = first_install()
        if not result.ok:
            log.error("launcher: first_install failed: %s", result.error)
            return 5
        log.info("launcher: first_install succeeded at %s", result.version)
        pointer = read_active_pointer()
        if pointer is None:
            log.error("launcher: first_install reported ok but pointer absent")
            return 7
    else:
        result = maybe_update()
        if not result.ok:
            log.warning(
                "launcher: maybe_update reported failure (%s); "
                "exec-ing the existing version anyway",
                result.error,
            )
        elif result.restart_required and result.version is not None:
            log.info("launcher: maybe_update installed %s (auto mode)", result.version)
            pointer = read_active_pointer()
            if pointer is None:
                log.error("launcher: post-update pointer absent")
                return 7

    target = version_dir(pointer.version)
    kt = kt_script(target)
    if not kt.is_file():
        log.error("launcher: kt shim missing at %s", kt)
        return 3

    # Replace this process with the framework's entry, forwarding argv.
    forward = sys.argv[1:] if argv is None else list(argv)
    # PYTHONPATH so the briefcase python finds the version's site-packages.
    os.environ["PYTHONPATH"] = _build_pythonpath(target)
    os.environ["PYTHONNOUSERSITE"] = "1"
    log.info("launcher: exec %s %s", kt, forward)
    os.execv(str(kt), [str(kt), *forward])
    return 0  # unreachable; execv replaces the process


def _build_pythonpath(version_root) -> str:
    """Prepend the version's ``site-packages/`` to any existing PYTHONPATH."""
    site = str(version_root / "site-packages")
    existing = os.environ.get("PYTHONPATH", "")
    if not existing:
        return site
    return os.pathsep.join([site, existing])


def _run_splash_demo(log) -> int:
    """Demo the splash UI without doing any real install."""
    log.info("launcher: --splash-demo running scripted sequence")
    srv = open_splash()
    try:
        srv.publish("Starting…", percent=5, message="")
        time.sleep(0.8)
        srv.publish("Resolving feed", percent=20, message="stable.json")
        time.sleep(0.8)
        srv.publish(
            "Downloading",
            percent=55,
            message="kohakuterrarium-1.5.1-linux-x64-py3.13.tar.zst",
        )
        time.sleep(0.8)
        srv.publish("Smoke testing", percent=85, message="kt --version")
        time.sleep(0.4)
        srv.publish("Ready", percent=100, message="", status="ok")
        time.sleep(1.0)
    finally:
        srv.stop()
    return 0


__all__ = ["main", "UpdateResult"]


if __name__ == "__main__":
    sys.exit(main())
