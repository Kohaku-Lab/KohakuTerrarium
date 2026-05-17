"""Launcher entry point.

Flow:

1. Parse minimal launcher flags (``--reset-settings``, ``--no-exec``,
   ``--splash-demo``).
2. Load (or create) ``app-settings.json`` via :mod:`launcher.settings`.
3. Detect the managed venv; if missing, run :func:`update_runner.first_install`
   to build it per the configured source (see :mod:`launcher.sources`).
4. If present, call :func:`update_runner.maybe_update` to honour
   ``update.mode`` (``manual`` / ``notify-on-launch`` / ``auto-on-launch``).
5. ``os.execv`` into the venv's ``kt`` to launch the framework process,
   forwarding the original argv.

The version-check probe that powers the ``notify-on-launch`` banner
runs *inside the framework process* via ``POST /api/app/check-now``
(see :mod:`kohakuterrarium.api.routes.app_update`) — the launcher itself
does not reach the network on launch.
"""

import argparse
import os
import sys
import time

from kohakuterrarium.launcher import settings as _settings
from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.paths import (
    runtime_dir,
    settings_path,
    venv_dir,
    venv_kt,
)
from kohakuterrarium.launcher.sources import resolve_pip_args
from kohakuterrarium.launcher.splash_window import open_splash
from kohakuterrarium.launcher.update_runner import first_install, maybe_update


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="kt-launcher",
        description="KohakuTerrarium launcher — manages the framework venv.",
        add_help=False,  # let the framework's own argparse handle --help post-exec
    )
    parser.add_argument(
        "--reset-settings",
        action="store_true",
        help="Overwrite app-settings.json with defaults and exit.",
    )
    parser.add_argument(
        "--no-exec",
        action="store_true",
        help="Resolve launcher state and exit (don't exec into the venv).",
    )
    parser.add_argument(
        "--splash-demo",
        action="store_true",
        help="Open the splash window with a scripted progress sequence (Phase D smoke).",
    )
    # Unknown flags get forwarded to the framework's ``kt`` after exec.
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

    if args.splash_demo:
        log.info("launcher: --splash-demo running scripted sequence")
        srv = open_splash()
        try:
            srv.publish("Starting…", percent=5, message="")
            time.sleep(0.8)
            srv.publish("Creating venv", percent=20, message="python -m venv …")
            time.sleep(0.8)
            srv.publish(
                "Installing framework",
                percent=55,
                message="pip install kohakuterrarium",
            )
            time.sleep(0.8)
            srv.publish("Smoke-testing", percent=85, message="kt --help")
            time.sleep(0.4)
            srv.publish("Ready", percent=100, message="", status="ok")
            time.sleep(1.0)
        finally:
            srv.stop()
        return 0

    # Load settings on every non-reset call — defaults are written on
    # first access so the rest of the wrapper can rely on a populated
    # ``runtime.venv_path``.
    cfg = _settings.load()
    try:
        pip_args = resolve_pip_args(cfg.source)
    except (ValueError, FileNotFoundError) as e:
        log.error("launcher: source resolution failed (%s); aborting", e)
        return 4

    if args.no_exec:
        log.info(
            "launcher: --no-exec — runtime_dir=%s venv_dir=%s venv_exists=%s "
            "source=%s update_mode=%s pip_args=%s",
            runtime_dir(),
            venv_dir(),
            venv_dir().exists(),
            cfg.source.kind,
            cfg.update.mode,
            pip_args,
        )
        return 0

    venv = venv_dir()
    if not venv.exists():
        # First launch — build the managed venv from configured source.
        log.info("launcher: no venv at %s — running first_install", venv)
        result = first_install()
        if not result.ok:
            log.error("launcher: first_install failed: %s", result.error)
            return 5
        log.info(
            "launcher: first_install succeeded at version %s",
            result.version,
        )
    else:
        # Honour the update.mode on launch.
        result = maybe_update()
        if not result.ok:
            log.warning(
                "launcher: maybe_update reported failure (%s); "
                "exec-ing the existing venv anyway",
                result.error,
            )

    kt = venv_kt(venv)
    if not kt.is_file():
        log.error("launcher: managed venv is missing the 'kt' entry at %s", kt)
        return 3

    # Replace this process with the framework's entry, forwarding argv.
    forward = sys.argv[1:] if argv is None else list(argv)
    log.info("launcher: exec %s %s", kt, forward)
    os.execv(str(kt), [str(kt), *forward])
    return 0  # unreachable; execv replaces the process


__all__ = ["main"]


if __name__ == "__main__":
    sys.exit(main())
