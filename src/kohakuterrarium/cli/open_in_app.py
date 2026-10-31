"""``kt open`` — hand folders and session files to the desktop app.

The desktop shell records where it is installed; a second launch with paths
forwards them to the running app (or starts it) and exits.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from kohakuterrarium.launcher.paths import config_home

SHELL_RECORD = "desktop-shell.json"
SESSION_SUFFIX = ".kohakutr"


def shell_executable() -> Path | None:
    """The desktop app the shell recorded, if that file still exists."""
    try:
        record = json.loads((config_home() / SHELL_RECORD).read_text(encoding="utf-8"))
        exe = Path(str(record["exe"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return exe if exe.is_file() else None


def openable(path: Path) -> bool:
    """Whether the app opens ``path``: an existing folder or a session file."""
    return path.is_dir() or (path.is_file() and path.suffix == SESSION_SUFFIX)


def open_cli(paths: list[str]) -> int:
    """Open each path in the desktop app; the exit code for the terminal."""
    exe = shell_executable()
    if exe is None:
        print(
            "The desktop app is not installed or has never been started.",
            file=sys.stderr,
        )
        return 1
    targets = [Path(raw).expanduser().resolve() for raw in paths]
    missing = [str(path) for path in targets if not openable(path)]
    if missing:
        print(
            "Not a folder or a .kohakutr session file: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 2
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [str(exe), *(str(path) for path in targets)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
        close_fds=True,
    )
    return 0


def add_open_subparser(subparsers) -> None:
    """Register the ``open`` subcommand on the main parser."""
    parser = subparsers.add_parser(
        "open", help="Open folders or session files in the desktop app"
    )
    parser.add_argument("paths", nargs="+", help="Folders or .kohakutr files")


def open_dispatch(args: argparse.Namespace) -> int:
    """Run ``kt open``."""
    return open_cli(args.paths)
