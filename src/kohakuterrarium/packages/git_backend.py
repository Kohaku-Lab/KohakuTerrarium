"""Git clone / pull backend abstraction.

Two implementations, picked at call time:

    1. **Native ``git`` binary** — fast, full feature set, the default
       when ``git`` is discoverable on ``$PATH``.  Used on desktop /
       CI / Linux server installs.

    2. **Pure-Python ``dulwich``** — pulled in as a hard dep so it's
       always available on Chaquopy Android (no system ``git``
       binary ships in the APK and one can't be shelled out to).
       Slower than native ``git`` for large repos but covers the
       creature / terrarium / plugin package sizes ``kt install``
       deals with (a few MB at most).

The public API is just two functions:

    ``clone_repo(url, target)``
    ``pull_repo(target)``

Both raise :class:`RuntimeError` on any failure.  Callers can rely on
the abstraction to handle backend selection — they don't need to know
which one ran.
"""

import shutil
import subprocess
from pathlib import Path

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def _has_native_git() -> bool:
    """True iff the system ``git`` binary is on ``$PATH`` and callable.

    We probe once and cache.  Cache invalidates on a process restart;
    a long-running daemon that gets ``git`` installed mid-session
    would still pick up the new binary on next restart, which is fine
    for an install path.
    """
    global _NATIVE_GIT_CACHE
    if _NATIVE_GIT_CACHE is not None:
        return _NATIVE_GIT_CACHE
    found = shutil.which("git") is not None
    _NATIVE_GIT_CACHE = found
    if not found:
        logger.info("Native git not found on PATH; using dulwich pure-Python backend")
    return found


_NATIVE_GIT_CACHE: bool | None = None


def clone_repo(url: str, target: Path) -> None:
    """Clone ``url`` into ``target`` directory.

    Selects the native git binary when available, otherwise falls
    back to dulwich.  Both code paths raise :class:`RuntimeError` on
    failure with a one-line diagnostic message.
    """
    if _has_native_git():
        _clone_native(url, target)
    else:
        _clone_dulwich(url, target)


def pull_repo(target: Path) -> None:
    """Pull (fast-forward only) the existing clone at ``target``.

    Same backend-selection rule as :func:`clone_repo`.
    """
    if _has_native_git():
        _pull_native(target)
    else:
        _pull_dulwich(target)


# ─── native git binary ────────────────────────────────────────────


def _clone_native(url: str, target: Path) -> None:
    try:
        subprocess.run(
            ["git", "clone", url, str(target)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace").strip() if exc.stderr else str(exc)
        raise RuntimeError(f"Git clone failed: {stderr}") from exc


def _pull_native(target: Path) -> None:
    try:
        subprocess.run(
            ["git", "-C", str(target), "pull", "--ff-only"],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace").strip() if exc.stderr else str(exc)
        raise RuntimeError(f"Git pull failed: {stderr}") from exc


# ─── pure-Python dulwich ──────────────────────────────────────────
#
# Dulwich is imported lazily so that:
#   * the module loads on systems where dulwich isn't installed yet
#     (only fails when actually invoked without native git);
#   * import cost is paid once per process, on first call.


def _import_dulwich():
    try:
        from dulwich import porcelain  # noqa: PLC0415  — lazy by design

        return porcelain
    except ImportError as exc:
        raise RuntimeError(
            "No git available — install ``git`` on PATH or "
            "``pip install dulwich`` (dulwich is a hard dep so this "
            "should never trigger in production builds)."
        ) from exc


def _clone_dulwich(url: str, target: Path) -> None:
    porcelain = _import_dulwich()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        porcelain.clone(url, str(target), depth=1)
    except Exception as exc:
        raise RuntimeError(f"Git clone failed (dulwich): {exc}") from exc


def _pull_dulwich(target: Path) -> None:
    porcelain = _import_dulwich()
    if not (target / ".git").exists():
        raise RuntimeError(f"Not a git clone: {target}")
    try:
        porcelain.pull(str(target))
    except Exception as exc:
        raise RuntimeError(f"Git pull failed (dulwich): {exc}") from exc


def _reset_backend_cache_for_tests() -> None:
    """Force re-probe of the native git binary on the next call."""
    global _NATIVE_GIT_CACHE
    _NATIVE_GIT_CACHE = None
