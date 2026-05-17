"""``kt self-update`` — CLI parity for the wrapper's update flow.

Behaviour depends on install kind, probed in this order:

1. **Wrapper-managed venv** (sentinel ``runtime/venv/.kt-wrapper-marker``
   exists alongside this interpreter) → call
   :func:`launcher.update_runner.run_update` directly.
2. **pipx** (``PIPX_HOME`` env or ``sys.executable`` under pipx layout)
   → exec ``pipx upgrade kohakuterrarium``.
3. **Editable install** (``pip show kohakuterrarium`` reports
   ``Editable project location``) → refuse, point to ``git pull``.
4. **System pip** (``sys.executable`` under ``/usr/bin`` etc.) →
   refuse, point to the platform package manager.
5. **Other user venv** → run ``pip install -U kohakuterrarium`` in
   the current interpreter; warn that wrapper-style atomic rename
   and rollback are not available.

See ``plans/1.5.0-roadmap/06-app-update/design.md`` §10 for the
contract.
"""

import argparse
import importlib.metadata
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from kohakuterrarium.launcher import settings as _launcher_settings
from kohakuterrarium.launcher.paths import venv_python, wrapper_marker_path
from kohakuterrarium.launcher.sources import resolve_pip_args
from kohakuterrarium.launcher.update_runner import run_update

_PYPI_JSON = "https://pypi.org/pypi/kohakuterrarium/json"


def _detect_install_kind() -> str:
    """Return one of: ``wrapper``, ``pipx``, ``editable``, ``system``, ``user``.

    Wrapper detection wins because the wrapper drops its own sentinel
    inside its managed venv; the running interpreter must be that venv's
    python for the sentinel test to be meaningful.
    """
    marker = wrapper_marker_path()
    if marker.is_file():
        # Verify we're actually running under THAT venv's interpreter —
        # otherwise the sentinel just means "wrapper installed once",
        # not "this process is the wrapper-managed framework."
        expected_py = venv_python(marker.parent).resolve()
        try:
            this_py = Path(sys.executable).resolve()
        except OSError:
            this_py = Path(sys.executable)
        if expected_py == this_py:
            return "wrapper"
    if os.environ.get("PIPX_HOME") or "/pipx/venvs/" in sys.executable.replace(
        "\\", "/"
    ):
        return "pipx"
    # Editable install probe via pip show — short subprocess, only fires
    # for `kt self-update`, not on every CLI call.
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pip", "show", "kohakuterrarium"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        ).stdout
        for line in out.splitlines():
            if line.lower().startswith("editable project location"):
                return "editable"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        pass
    # Heuristic for system-managed pythons.
    exe = sys.executable.replace("\\", "/")
    for sysprefix in ("/usr/bin/", "/usr/local/bin/", "/opt/homebrew/bin/"):
        if exe.startswith(sysprefix):
            return "system"
    return "user"


def _latest_pypi_version() -> str | None:
    """Best-effort PyPI version probe.  Returns ``None`` on any error."""
    try:
        req = urllib.request.Request(
            _PYPI_JSON,
            headers={"User-Agent": "KohakuTerrarium-self-update/1.5"},
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("info", {}).get("version")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _current_version() -> str | None:
    try:
        return importlib.metadata.version("kohakuterrarium")
    except importlib.metadata.PackageNotFoundError:
        return None


def _print(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _refuse(kind: str, hint: str) -> int:
    _print(f"kt self-update: this install is managed by '{kind}'.")
    _print(hint)
    return 2


def self_update_cli(args: argparse.Namespace) -> int:
    kind = _detect_install_kind()
    cur = _current_version() or "unknown"

    if args.check_only:
        latest = _latest_pypi_version()
        if latest is None:
            _print(f"kt self-update: current={cur}; latest=unknown (probe failed)")
            return 0
        _print(f"kt self-update: current={cur}; latest={latest}")
        return 0 if latest != cur else 1

    if kind == "wrapper":
        # Use the same atomic-rename + flock + rollback flow the GUI uses.
        cfg = _launcher_settings.load()
        if args.source:
            cfg.source.kind = args.source
        if args.spec is not None:
            cfg.source.spec = args.spec
        if args.source or args.spec is not None:
            _launcher_settings.save(cfg)
        _print(
            f"kt self-update: wrapper-managed; running update (source={cfg.source.kind})"
        )
        if args.dry_run:
            pip_args = resolve_pip_args(cfg.source)
            _print(f"kt self-update: --dry-run; would invoke pip install {pip_args}")
            return 0
        result = run_update()
        if not result.ok:
            _print(f"kt self-update: failed — {result.error}")
            return 1
        _print(
            f"kt self-update: updated to {result.version}; restart the app to use it"
        )
        return 0

    if kind == "pipx":
        _print(
            "kt self-update: pipx install detected; running `pipx upgrade kohakuterrarium`"
        )
        if args.dry_run:
            _print(
                "kt self-update: --dry-run; would invoke pipx upgrade kohakuterrarium"
            )
            return 0
        try:
            subprocess.run(["pipx", "upgrade", "kohakuterrarium"], check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            _print(f"kt self-update: pipx upgrade failed — {e}")
            return 1
        return 0

    if kind == "editable":
        return _refuse(
            "editable",
            "Update via `git pull` in your checkout, then "
            "`pip install -e .` to refresh installed metadata.",
        )

    if kind == "system":
        return _refuse(
            "system package manager",
            "Use your platform's package manager (apt / brew / etc.) "
            "to update KohakuTerrarium, or install into a user venv "
            "to manage updates yourself.",
        )

    # kind == "user"
    _print(
        "kt self-update: user-managed install; running `pip install -U kohakuterrarium`.  "
        "Atomic rename + rollback are wrapper-only features and not available here."
    )
    if args.dry_run:
        _print("kt self-update: --dry-run; would invoke pip install -U kohakuterrarium")
        return 0
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "kohakuterrarium"],
            check=True,
        )
    except subprocess.CalledProcessError as e:
        _print(f"kt self-update: pip install failed — rc={e.returncode}")
        return 1
    return 0


def add_self_update_subparser(subparsers) -> None:
    parser = subparsers.add_parser(
        "self-update",
        help="Update KohakuTerrarium (wrapper-aware; falls back to pip)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the action that would run; do not modify anything.",
    )
    parser.add_argument(
        "--source",
        choices=("pypi", "git", "local", "bundled"),
        default=None,
        help="Override the configured source kind for this update only.",
    )
    parser.add_argument(
        "--spec",
        default=None,
        help="Override the configured source spec (e.g. `==1.5.0`, git URL, path).",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Print current and latest version; exit 0 if newer available, 1 if up-to-date.",
    )


__all__ = ["add_self_update_subparser", "self_update_cli"]
