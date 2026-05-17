"""Path constants the launcher reads / writes.

Centralised so test fixtures can monkey-patch ``CONFIG_HOME`` and the
rest of the launcher consults the patched value.  Every helper here
is pure — no side effects on import.
"""

import os
import sys
from pathlib import Path


def config_home() -> Path:
    """Resolve the user's KohakuTerrarium config dir.

    Honours ``KT_CONFIG_DIR`` (the same env var the framework's
    :func:`kohakuterrarium.utils.config_dir.config_dir` uses) so the
    launcher and the framework agree on where settings + runtime
    state live.  Defaults to ``~/.kohakuterrarium``.
    """
    env = os.environ.get("KT_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".kohakuterrarium"


def runtime_dir() -> Path:
    """Where the launcher keeps its managed venv + lockfile."""
    return config_home() / "runtime"


def venv_dir() -> Path:
    """Path of the currently-active managed venv."""
    return runtime_dir() / "venv"


def venv_new_dir() -> Path:
    """Path of the next-version venv being prepared by an update."""
    return runtime_dir() / "venv.new"


def venv_old_dir() -> Path:
    """Path of the previous venv kept for one-shot rollback."""
    return runtime_dir() / "venv.old"


def settings_path() -> Path:
    """Path to the ``app-settings.json`` file."""
    return config_home() / "app-settings.json"


def lock_path() -> Path:
    """Path to the update-flock file."""
    return runtime_dir() / ".update.lock"


def wrapper_marker_path() -> Path:
    """Sentinel file the wrapper drops next to its managed venv.

    Presence == "this install is wrapper-managed", which
    :func:`kohakuterrarium.cli.self_update` keys on to decide whether
    ``kt self-update`` should use the wrapper's atomic-rename protocol
    or fall back to a plain ``pip install -U``.
    """
    return venv_dir() / ".kt-wrapper-marker"


def bundled_wheels_dir() -> Path | None:
    """Directory inside the Briefcase bundle containing offline wheels.

    Briefcase copies ``wheels-bundle/`` next to the launcher entry
    when the bundle is built (see ``scripts/build_wrapper_wheels.py``).
    Returns ``None`` when running outside a bundled context (dev install).
    """
    candidate = Path(sys.executable).parent.parent / "wheels-bundle"
    if candidate.is_dir():
        return candidate
    candidate = Path(__file__).resolve().parents[3] / "wheels-bundle"
    if candidate.is_dir():
        return candidate
    return None


def venv_python(venv: Path) -> Path:
    """Return the Python interpreter path inside ``venv``."""
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def venv_kt(venv: Path) -> Path:
    """Return the ``kt`` console script path inside ``venv``."""
    if sys.platform == "win32":
        return venv / "Scripts" / "kt.exe"
    return venv / "bin" / "kt"


__all__ = [
    "config_home",
    "runtime_dir",
    "venv_dir",
    "venv_new_dir",
    "venv_old_dir",
    "settings_path",
    "lock_path",
    "wrapper_marker_path",
    "bundled_wheels_dir",
    "venv_python",
    "venv_kt",
]
