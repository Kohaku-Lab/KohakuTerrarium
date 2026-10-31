"""``kt shims`` — expose ``kt`` / ``kt-cli`` / ``kt-tui`` on the terminal.

Two installation environments require different shim strategies:

- **Real-interpreter install** (``pip``/``pipx``/``uv``/dev): the console
  scripts already exist next to ``sys.executable``.  On POSIX we symlink
  them into ``~/.local/bin`` for users whose venv ``bin/`` isn't on PATH;
  on Windows we defer to ``pipx`` (its ``.exe`` launchers aren't freely
  relocatable).
- **Desktop bundle** (no external Python — the audience that needs this
  most): ``sys.executable`` *is* the Briefcase stub, which already routes
  ``<stub> cli …`` → ``__briefcase__`` → ``cli.main()``.  We write tiny
  *stub-forwarding* wrapper scripts (``exec "<stub>" cli "$@"``) into a
  user PATH dir.  This reuses the bundle's own ABI-matched runtime with no
  embedded-Python hunting.

The decision logic (:func:`build_plan`) is pure and fully injectable so it
unit-tests across (console|bundle)×(posix|windows) without touching the
filesystem; :func:`apply_plan` performs the writes.
"""

import argparse
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

from kohakuterrarium.utils.interpreter import is_briefcase_runtime
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Bundle wrappers map standalone commands back to the corresponding ``kt`` mode.
SHIM_SUBCOMMANDS: dict[str, str | None] = {
    "kt": None,
    "kt-cli": "cli",
    "kt-tui": "tui",
}
KT_SHIM_MARKER = "kohakuterrarium-shim"

MODE_CONSOLE = "console-symlink"
MODE_BUNDLE = "bundle-wrapper"
MODE_RUNTIME = "bundled-python"
MODE_DEFER_PIPX = "defer-pipx"

_FROM_ENVIRONMENT = object()


@dataclass
class ShimItem:
    """Describe one planned shim filesystem action."""

    name: str
    action: str  # "symlink" | "wrapper" | "skip"
    dest: Path
    source: Path | None = None
    content: str | None = None
    reason: str = ""


@dataclass
class ShimPlan:
    """Describe a platform-specific shim installation plan."""

    mode: str
    target_dir: Path
    system: str = ""
    items: list[ShimItem] = field(default_factory=list)
    on_path: bool = False
    note: str = ""


def in_bundle() -> bool:
    """True when running inside the Briefcase desktop bundle."""
    return is_briefcase_runtime()


def _default_target_dir(system: str) -> Path:
    """Return the default user-level shim directory for a platform."""
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "KohakuTerrarium" / "bin"
    return Path.home() / ".local" / "bin"


def _path_contains(target_dir: Path, path_env: str) -> bool:
    """Return whether PATH contains the target directory."""
    try:
        target = target_dir.resolve()
    except OSError:
        target = target_dir
    for entry in path_env.split(os.pathsep):
        if not entry:
            continue
        try:
            if Path(entry).resolve() == target:
                return True
        except OSError:
            continue
    return False


def _wrapper_content(system: str, executable: str, subcmd: str | None) -> str:
    """Build a platform wrapper that forwards to the bundled executable."""
    sub = f" {subcmd}" if subcmd else ""
    if system == "Windows":
        return f'@echo off\nrem {KT_SHIM_MARKER}\n"{executable}"{sub} %*\n'
    return f'#!/bin/sh\n# {KT_SHIM_MARKER}\nexec "{executable}"{sub} "$@"\n'


def bundled_runtime() -> tuple[str, str] | None:
    """The interpreter and launcher directory a native shell ships, if any."""
    python = os.environ.get("KT_PYTHON")
    bootstrap = os.environ.get("KT_BOOTSTRAP_DIR")
    return (python, bootstrap) if python and bootstrap else None


def _drop_verbatim_prefix(path: str) -> str:
    """``\\\\?\\C:\\x`` as ``C:\\x``: cmd.exe cannot run or search a verbatim path."""
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _runtime_wrapper_content(
    system: str, runtime: tuple[str, str], subcmd: str | None
) -> str:
    """Build a wrapper that runs the launcher on the shipped interpreter."""
    python, bootstrap = runtime
    sub = f" {subcmd}" if subcmd else ""
    if system == "Windows":
        python, bootstrap = _drop_verbatim_prefix(python), _drop_verbatim_prefix(
            bootstrap
        )
        return (
            f"@echo off\nrem {KT_SHIM_MARKER}\nsetlocal\n"
            f'set "PYTHONPATH={bootstrap}"\nset "PYTHONNOUSERSITE=1"\n'
            f'"{python}" -m kohakuterrarium.launcher{sub} %*\n'
            "exit /b %ERRORLEVEL%\n"
        )
    return (
        f"#!/bin/sh\n# {KT_SHIM_MARKER}\n"
        f'PYTHONPATH="{bootstrap}" PYTHONNOUSERSITE=1 '
        f'exec "{python}" -m kohakuterrarium.launcher{sub} "$@"\n'
    )


def _dest_for(target_dir: Path, name: str, system: str) -> Path:
    """Return the platform-specific destination for a shim name."""
    return target_dir / (f"{name}.cmd" if system == "Windows" else name)


def build_plan(
    *,
    system: str | None = None,
    bundle: bool | None = None,
    scripts_dir: Path | None = None,
    target_dir: Path | None = None,
    executable: str | None = None,
    path_env: str | None = None,
    runtime: tuple[str, str] | None | object = _FROM_ENVIRONMENT,
) -> ShimPlan:
    """Decide what shims to create. Pure: no filesystem writes.

    All inputs are injectable so the matrix (console|bundle|runtime)×
    (posix|windows) is unit-testable.  ``scripts_dir`` existence is *probed*
    (read-only) only to mark missing console scripts as skipped.
    """
    system = system or platform.system()
    runtime = bundled_runtime() if runtime is _FROM_ENVIRONMENT else runtime
    executable = executable or sys.executable
    bundle = in_bundle() if bundle is None else bundle
    scripts_dir = scripts_dir or Path(executable).resolve().parent
    target_dir = target_dir or _default_target_dir(system)
    path_env = os.environ.get("PATH", "") if path_env is None else path_env

    on_path = _path_contains(target_dir, path_env)

    if runtime:
        items = [
            ShimItem(
                name=name,
                action="wrapper",
                dest=_dest_for(target_dir, name, system),
                content=_runtime_wrapper_content(system, runtime, sub),
            )
            for name, sub in SHIM_SUBCOMMANDS.items()
        ]
        note = "Desktop app: wrappers run the app's bundled interpreter."
        return ShimPlan(MODE_RUNTIME, target_dir, system, items, on_path, note)

    if bundle:
        items = [
            ShimItem(
                name=name,
                action="wrapper",
                dest=_dest_for(target_dir, name, system),
                content=_wrapper_content(system, executable, sub),
            )
            for name, sub in SHIM_SUBCOMMANDS.items()
        ]
        note = "Desktop bundle: wrappers forward to the app's bundled runtime."
        return ShimPlan(MODE_BUNDLE, target_dir, system, items, on_path, note)

    if system == "Windows":
        return ShimPlan(
            MODE_DEFER_PIPX,
            target_dir,
            system,
            [],
            on_path,
            "On Windows, install for the terminal with: pipx install kohakuterrarium",
        )

    items = []
    for name in SHIM_SUBCOMMANDS:
        source = scripts_dir / name
        if source.exists():
            items.append(
                ShimItem(
                    name=name,
                    action="symlink",
                    dest=_dest_for(target_dir, name, system),
                    source=source,
                )
            )
        else:
            items.append(
                ShimItem(
                    name=name,
                    action="skip",
                    dest=_dest_for(target_dir, name, system),
                    reason=f"no console script at {source}",
                )
            )
    return ShimPlan(MODE_CONSOLE, target_dir, system, items, on_path, "")


def apply_plan(plan: ShimPlan) -> list[str]:
    """Create the planned symlinks / wrappers. Returns human-readable lines."""
    messages: list[str] = []
    if plan.mode == MODE_DEFER_PIPX:
        return [plan.note]

    plan.target_dir.mkdir(parents=True, exist_ok=True)
    for item in plan.items:
        if item.action == "skip":
            messages.append(f"skip {item.name}: {item.reason}")
            continue
        # Replacement is intentional only for shims we own, so reinstalling
        # repairs stale targets without deleting an unrelated user command.
        if item.dest.is_symlink() or item.dest.exists():
            if not is_ours(item.dest):
                messages.append(
                    f"skip {item.name}: {item.dest} is not a kohakuterrarium shim"
                )
                continue
            try:
                item.dest.unlink()
            except OSError as exc:
                messages.append(
                    f"FAILED {item.name}: cannot replace {item.dest} ({exc})"
                )
                continue
        if item.action == "symlink" and item.source is not None:
            os.symlink(item.source, item.dest)
            messages.append(f"linked {item.dest} -> {item.source}")
        elif item.action == "wrapper" and item.content is not None:
            item.dest.write_text(item.content, encoding="utf-8")
            os.chmod(item.dest, 0o755)
            messages.append(f"wrote {item.dest}")
    return messages


def shim_path(plan: ShimPlan, name: str) -> Path:
    """Where ``plan`` puts the shim called ``name``."""
    return _dest_for(plan.target_dir, name, plan.system)


def is_ours(dest: Path) -> bool:
    """True if ``dest`` is a shim we created (symlink to a kt script, or a
    wrapper carrying our marker)."""
    if dest.is_symlink():
        try:
            target = os.readlink(dest)
        except OSError:
            return False
        return Path(target).name in SHIM_SUBCOMMANDS
    if dest.is_file():
        try:
            return KT_SHIM_MARKER in dest.read_text(encoding="utf-8")
        except OSError:
            return False
    return False


def install_shims() -> int:
    """Install terminal shims for the current runtime."""
    plan = build_plan()
    for line in apply_plan(plan):
        print(line)
    if plan.mode != MODE_DEFER_PIPX and not plan.on_path:
        print()
        print(f"Note: {plan.target_dir} is not on your PATH. Add it, e.g.:")
        print(f'  export PATH="{plan.target_dir}:$PATH"')
    return 0


def shims_status() -> int:
    """Print shim installation and PATH status."""
    plan = build_plan()
    print(f"mode:       {plan.mode}")
    print(
        f"target dir: {plan.target_dir}  ({'on PATH' if plan.on_path else 'NOT on PATH'})"
    )
    if plan.note:
        print(f"note:       {plan.note}")
    for name in SHIM_SUBCOMMANDS:
        dest = _dest_for(plan.target_dir, name, plan.system)
        state = "installed" if (dest.exists() and is_ours(dest)) else "missing"
        print(f"  {name:<7} {state}  ({dest})")
    return 0


def refresh_installed_shims() -> list[str]:
    """Rewrite installed wrapper shims whose contents differ from what this install
    would write, so a reinstall or a move does not leave them pointing at nothing.

    Only shims we own are touched, and none are created.
    """
    plan = build_plan()
    if plan.mode == MODE_DEFER_PIPX:
        return []
    stale = []
    for item in plan.items:
        if item.action != "wrapper" or item.content is None:
            continue
        if not (item.dest.is_file() and is_ours(item.dest)):
            continue
        try:
            current = item.dest.read_text(encoding="utf-8")
        except OSError:
            continue
        if current != item.content:
            stale.append(item)
    if not stale:
        return []
    plan.items = stale
    return apply_plan(plan)


def shims_uninstall() -> int:
    """Remove only shims recognized as owned by KohakuTerrarium."""
    plan = build_plan()
    removed = 0
    for name in SHIM_SUBCOMMANDS:
        dest = _dest_for(plan.target_dir, name, plan.system)
        if dest.exists() or dest.is_symlink():
            if is_ours(dest):
                try:
                    dest.unlink()
                    print(f"removed {dest}")
                    removed += 1
                except OSError as exc:
                    print(f"FAILED to remove {dest}: {exc}")
            else:
                print(f"skip {dest}: not a kohakuterrarium shim")
    if removed == 0:
        print("No shims to remove.")
    return 0


def shims_summary() -> str:
    """One-line status for ``kt doctor`` (never raises)."""
    try:
        plan = build_plan()
    except Exception as exc:  # pragma: no cover - defensive
        return f"unavailable ({exc})"
    installed = [
        name
        for name in SHIM_SUBCOMMANDS
        if (lambda d: d.exists() and is_ours(d))(
            _dest_for(plan.target_dir, name, plan.system)
        )
    ]
    if not installed:
        return "not installed (run `kt shims install`)"
    where = f"{plan.target_dir} ({'on PATH' if plan.on_path else 'NOT on PATH'})"
    return f"{', '.join(installed)} -> {where}"


def add_shims_subparser(subparsers) -> None:
    """Register the ``shims`` subcommand group on the main parser."""
    p = subparsers.add_parser(
        "shims",
        help="Install/inspect terminal shims (kt, kt-cli, kt-tui) on PATH",
    )
    sub = p.add_subparsers(dest="shims_command")
    sub.add_parser("install", help="Create kt/kt-cli/kt-tui shims in a PATH dir")
    sub.add_parser("status", help="Show shim install + PATH status")
    sub.add_parser("uninstall", help="Remove shims we created")


def shims_cli(args: argparse.Namespace) -> int:
    """Dispatch shim management commands."""
    command = getattr(args, "shims_command", None)
    if command == "install":
        return install_shims()
    if command == "uninstall":
        return shims_uninstall()
    return shims_status()
