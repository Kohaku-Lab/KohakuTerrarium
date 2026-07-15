"""Prepare versioned launcher state and hand off to the active release.

The standard entry point executes the framework in a fresh process using the
active release's site-packages. Briefcase reuses :func:`prepare` but performs
an in-process handoff because its stub executable and isolated path file do
not support the standard ``PYTHONPATH`` execution model.
"""

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from kohakuterrarium.launcher import migration as _migration
from kohakuterrarium.launcher import settings as _settings
from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.paths import (
    bundled_release_dir,
    runtime_dir,
    settings_path,
    site_packages_dir,
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


@dataclass
class PrepareResult:
    """Describe whether setup finished or produced an active release.

    ``done`` directs the caller to exit with ``exit_code``. Otherwise,
    ``site_packages`` and the version paths identify the release to launch.
    """

    exit_code: int = 0
    done: bool = False
    error: str | None = None
    version: str | None = None
    version_root: Path | None = None
    site_packages: Path | None = None


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="kt-launcher",
        description="KohakuTerrarium launcher — manages versioned releases.",
        add_help=False,  # Preserve --help for the framework after handoff.
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


def prepare(argv: list[str] | None = None) -> "PrepareResult":
    """Prepare the active release without executing it or changing import paths.

    Setup handles one-shot launcher modes, migration, first installation, and
    optional updates. The result either directs the caller to exit or provides
    the active release paths for a process or in-process handoff. This function
    also owns first-install splash lifecycle for both entry points.
    """
    args = _parse_args(argv)
    log = get_logger()

    runtime_dir().mkdir(parents=True, exist_ok=True)

    if args.reset_settings:
        _settings.reset()
        log.info("launcher: reset settings (wrote defaults to %s)", settings_path())
        return PrepareResult(exit_code=0, done=True)

    if args.reset_runtime:
        log.info("launcher: --reset-runtime running first_install fresh")
        result = runner_reset()
        if not result.ok:
            log.error("launcher: reset failed: %s", result.error)
            return PrepareResult(exit_code=6, error=result.error, done=True)
        log.info("launcher: reset succeeded at version %s", result.version)
        return PrepareResult(exit_code=0, done=True)

    if args.splash_demo:
        return PrepareResult(exit_code=_run_splash_demo(log), done=True)

    # Legacy cleanup is idempotent and must precede settings-driven setup.
    _migration.wipe_legacy_venv()

    cfg = _settings.load()

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
        return PrepareResult(exit_code=0, done=True)

    pointer = read_active_pointer()
    if pointer is None:
        # First installation can block on extraction, download, and smoke
        # checks, so keep the shell visibly responsive with progress.
        srv = open_splash()
        try:
            srv.publish("Setting up", percent=5, message="")

            def _progress(phase: str, percent: float, msg: str) -> None:
                try:
                    srv.publish(phase, percent=percent, message=msg)
                except Exception:
                    pass

            log.info("launcher: no active pointer — first_install")
            install_result = first_install(_progress)
            if not install_result.ok:
                srv.publish(
                    "Failed",
                    percent=100,
                    message=install_result.error or "",
                    status="failed",
                )
                log.error("launcher: first_install failed: %s", install_result.error)
                time.sleep(2.0)
                return PrepareResult(exit_code=5, error=install_result.error)
            log.info("launcher: first_install succeeded at %s", install_result.version)
            srv.publish(
                "Ready",
                percent=100,
                message=str(install_result.version),
                status="ok",
            )
            time.sleep(0.4)
        finally:
            try:
                srv.stop()
            except Exception:
                pass
        pointer = read_active_pointer()
        if pointer is None:
            log.error("launcher: first_install reported ok but pointer absent")
            return PrepareResult(exit_code=7, error="post-install pointer absent")
    else:
        update_result = maybe_update()
        if not update_result.ok:
            log.warning(
                "launcher: maybe_update reported failure (%s); "
                "starting the existing version anyway",
                update_result.error,
            )
        elif update_result.restart_required and update_result.version is not None:
            log.info(
                "launcher: maybe_update installed %s (auto mode)",
                update_result.version,
            )
            pointer = read_active_pointer()
            if pointer is None:
                log.error("launcher: post-update pointer absent")
                return PrepareResult(exit_code=7, error="post-update pointer absent")

    target = version_dir(pointer.version)
    site = site_packages_dir(target)
    if not site.is_dir():
        log.error("launcher: site-packages missing at %s", site)
        return PrepareResult(exit_code=3, error=f"site-packages missing at {site}")

    return PrepareResult(
        exit_code=0,
        version_root=target,
        site_packages=site,
        version=pointer.version,
    )


def main(argv: list[str] | None = None) -> int:
    """Prepare the active release and replace the process with its CLI.

    One-shot modes return their exit code directly. Briefcase uses its separate
    in-process entry point instead of this execution path.
    """
    result = prepare(argv)
    if result.done or result.site_packages is None:
        return result.exit_code

    log = get_logger()
    forward = sys.argv[1:] if argv is None else list(argv)
    os.environ["PYTHONPATH"] = _build_pythonpath(result.version_root)
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.environ["KT_LAUNCHER_EXEC"] = "1"
    py = str(sys.executable)
    log.info("launcher: exec %s -m kohakuterrarium.cli %s", py, forward)
    os.execv(py, [py, "-m", "kohakuterrarium.cli", *forward])
    return 0  # Successful execv replaces the process before this return.


def _build_pythonpath(version_root) -> str:
    """Prepend the version's ``site-packages/`` to any existing PYTHONPATH."""
    site = str(site_packages_dir(version_root))
    existing = os.environ.get("PYTHONPATH", "")
    if not existing:
        return site
    return os.pathsep.join([site, existing])


def _run_splash_demo(log) -> int:
    """Run a scripted splash sequence and return a successful exit code."""
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


__all__ = ["PrepareResult", "UpdateResult", "main", "prepare"]


if __name__ == "__main__":
    sys.exit(main())
