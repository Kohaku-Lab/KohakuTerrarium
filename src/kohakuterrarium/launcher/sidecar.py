"""Run the desktop server for a native shell inside a bundled interpreter.

The shell ships an interpreter and this launcher package only. The sidecar
prepares the versioned release tree (first install from the bundled archive, or
an update), reports progress as JSON lines on stdout, switches imports to that
tree, and runs the desktop server until the shell asks it to stop.
"""

import argparse
import importlib
import json
import os
import sys
from pathlib import Path

from kohakuterrarium.launcher import interpreter_mode
from kohakuterrarium.launcher.bootloader import prepare
from kohakuterrarium.launcher.paths import site_packages_dir


def emit_progress(phase: str, percent: float, message: str) -> None:
    """Print one progress update for the shell to render."""
    print(
        json.dumps({"phase": phase, "percent": percent, "message": message}),
        flush=True,
    )


def publish_error(state_path: Path, message: str) -> None:
    state_path.write_text(
        json.dumps({"status": "error", "error": message}), encoding="utf-8"
    )


def export_release_path(site_packages: Path) -> None:
    """Let child processes import the active release as this process does."""
    existing = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(site_packages), existing) if part
    )
    os.environ["PYTHONNOUSERSITE"] = "1"
    os.environ["KT_LAUNCHER_EXEC"] = "1"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="kt-sidecar")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )
    parser.add_argument("--state-path", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, default=0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = prepare([], progress=emit_progress)
    if result.done or result.site_packages is None:
        message = result.error or "The runtime could not be prepared."
        publish_error(args.state_path, message)
        return result.exit_code or 1

    site = site_packages_dir(result.version_root)
    interpreter_mode.activate_release(str(site))
    export_release_path(site)
    emit_progress("Starting", 100.0, result.version or "")

    server = importlib.import_module("kohakuterrarium.serving.desktop.server")
    return server.run_server_child(
        args.port, args.log_level, args.state_path, parent_pid=args.parent_pid
    )


if __name__ == "__main__":
    sys.exit(main())
