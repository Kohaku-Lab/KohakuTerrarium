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


def _candidate_wheel_dirs() -> list[Path]:
    """Compute the ordered list of ``wheels-bundle/`` candidate paths.

    Factored out for testability — ``bundled_wheels_dir()`` is the
    "search + validate" entry point; this helper is the "where would
    we look?" piece.  Candidate order (first-match-wins in the caller):

    1. Sibling of the ``kohakuterrarium/`` source as Briefcase lays it
       out (three ``parent`` calls from this file lands at the ``app/``
       root, where ``wheels-bundle/`` sits as a sibling of
       ``kohakuterrarium/``).
    2. Sibling of ``sys.executable`` (Briefcase Windows layout).
    3. Parent of ``sys.executable``'s directory (Briefcase macOS legacy).
    4. Repo root (four ``parent`` calls from this file lands at the
       repo root for dev installs that ran
       ``scripts/build_wrapper_wheels.py`` locally).
    """
    here = Path(__file__).resolve()
    exe = Path(sys.executable)
    return [
        here.parent.parent.parent / "wheels-bundle",
        exe.parent / "wheels-bundle",
        exe.parent.parent / "wheels-bundle",
        here.parents[3] / "wheels-bundle",
    ]


def _bundle_has_framework_wheel(path: Path) -> bool:
    """Treat ``path`` as a valid bundle only when it carries the
    framework wheel — guards against leftover scaffolding directories
    that happen to be named ``wheels-bundle/``."""
    return path.is_dir() and any(path.glob("kohakuterrarium-*.whl"))


def bundled_wheels_dir() -> Path | None:
    """Directory inside the Briefcase bundle containing offline wheels.

    Briefcase copies ``wheels-bundle/`` as a sibling of the
    ``kohakuterrarium/`` source tree when it builds the artifact (see
    the ``sources = ["src/kohakuterrarium", "wheels-bundle"]`` entry in
    ``pyproject.toml`` and ``scripts/build_wrapper_wheels.py``).  The
    on-disk layout varies per backend; this function probes the
    candidate paths from :func:`_candidate_wheel_dirs` and returns the
    first that passes :func:`_bundle_has_framework_wheel`.

    Returns ``None`` when running outside a bundled context (dev
    install with no local wheels-bundle, or a packaged install where
    the bundle is broken / missing).
    """
    for candidate in _candidate_wheel_dirs():
        if _bundle_has_framework_wheel(candidate):
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
    "_candidate_wheel_dirs",
    "_bundle_has_framework_wheel",
    "venv_python",
    "venv_kt",
]
