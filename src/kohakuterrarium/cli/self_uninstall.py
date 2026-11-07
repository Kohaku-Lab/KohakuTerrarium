"""``kt self-uninstall`` — remove what the one-line installer put on this machine.

Removes the ``kt`` / ``kt-ui`` / ``kt-cli`` / ``kt-tui`` wrappers and the launcher runtime
(``~/.kohakuterrarium/runtime``: the terminal install's interpreter and launcher, and the
release trees). Config, sessions and packages stay unless ``--purge`` removes the whole
``~/.kohakuterrarium``. A desktop app, if any, reinstalls its bundled release on its next start.

A background KT server running from the runtime is stopped first. The running interpreter is
part of what goes: on Windows, where a running program (and the ``.cmd`` wrapper cmd.exe is
still reading) cannot be deleted, the removal finishes in a detached PowerShell once this
command and the launcher that started it have exited.
"""

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path

from kohakuterrarium.cli.serve import STATE_PATH, serve_stop_cli
from kohakuterrarium.cli.shims import SHIM_SUBCOMMANDS, _dest_for, build_plan, is_ours
from kohakuterrarium.cli.shims import shims_uninstall
from kohakuterrarium.launcher.paths import config_home, runtime_dir


def _targets(purge: bool) -> list[Path]:
    return [config_home()] if purge else [runtime_dir()]


def _server_from(targets: list[Path]) -> bool:
    """Whether the background server runs an interpreter inside ``targets``."""
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        python = Path(str(state.get("python", ""))).resolve()
    except (OSError, ValueError):
        return False
    return any(t.resolve() in python.parents for t in targets)


def _our_shims() -> list[Path]:
    plan = build_plan()
    found = []
    for name in SHIM_SUBCOMMANDS:
        dest = _dest_for(plan.target_dir, name, plan.system)
        if dest.exists() and is_ours(dest):
            found.append(dest)
    return found


def _ps_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _remove_later(paths: list[Path]) -> None:
    """Windows: delete ``paths`` once this process and its parent (the launcher) are gone."""
    pids = ",".join(str(p) for p in {os.getpid(), os.getppid()})
    items = ",".join(_ps_quote(p) for p in paths)
    script = (
        f"Wait-Process -Id {pids} -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2; "
        f"foreach ($p in @({items})) "
        "{ Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue }"
    )
    # no window (a DETACHED_PROCESS PowerShell never starts); out of the terminal's job when
    # allowed, so closing the terminal does not end it
    base = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    breakaway = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
    for flags in (base | breakaway, base):
        try:
            subprocess.Popen(  # noqa: S603 - a fixed script over our own paths
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                creationflags=flags,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except OSError:
            continue


def _confirm(question: str) -> bool:
    try:
        return input(question).strip().lower() in ("y", "yes")
    except EOFError:  # no terminal to answer from
        return False


def self_uninstall_cli(args: argparse.Namespace) -> int:
    """Remove the wrappers and the runtime (``--purge``: all KohakuTerrarium data)."""
    targets = [p for p in _targets(args.purge) if p.exists()]
    if not args.yes:
        what = ", ".join(str(p) for p in targets) or "nothing else"
        if not _confirm(f"This removes the kt commands and {what}. Continue? [y/N] "):
            print("Cancelled (pass -y to skip the question).")
            return 1
    if _server_from(targets):
        print("Stopping the background KT server running from this install...")
        serve_stop_cli(argparse.Namespace(timeout=15))
    if os.name == "nt":
        shims = _our_shims()
        _remove_later([*shims, *targets])
        for path in [*shims, *targets]:
            print(f"removing {path} (finishes once this command has exited)")
    else:
        shims_uninstall()
        for path in targets:
            shutil.rmtree(path, ignore_errors=True)
            print(f"removed {path}")
    print("KohakuTerrarium is uninstalled.")
    if not args.purge:
        print(f"Your config and sessions are kept in {config_home()}.")
    return 0


def add_self_uninstall_subparser(subparsers) -> None:
    parser = subparsers.add_parser(
        "self-uninstall",
        help="Remove the kt commands and the launcher runtime the installer put here",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="Also remove config, sessions and packages (all of ~/.kohakuterrarium).",
    )
    parser.add_argument("-y", "--yes", action="store_true", help="Do not ask.")


__all__ = ["add_self_uninstall_subparser", "self_uninstall_cli"]
