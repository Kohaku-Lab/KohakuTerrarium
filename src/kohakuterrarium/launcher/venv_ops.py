"""Primitives for creating / installing into / smoke-testing / swapping venvs.

Higher-level orchestration (flock + retry + persistence) lives in
:mod:`launcher.update_runner`; this module just exposes the
deterministic primitives so unit tests can pin each one.
"""

import shutil
import subprocess
import sys
import venv
from pathlib import Path

from kohakuterrarium.launcher.log import get_logger
from kohakuterrarium.launcher.paths import venv_kt, venv_python

SMOKE_TIMEOUT_SECONDS = 30.0


class VenvOpError(RuntimeError):
    """Anything venv-lifecycle related that should surface to the UI."""


def create_venv(path: Path) -> None:
    """Create a fresh venv at ``path``.

    ``with_pip=True`` ensures the venv has ``pip`` available without a
    follow-up bootstrap step.  ``clear=True`` so an aborted prior call
    can't leave a half-built directory in the way.
    """
    log = get_logger()
    log.info("venv: creating %s", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    builder = venv.EnvBuilder(with_pip=True, clear=True, symlinks=False)
    try:
        builder.create(str(path))
    except (OSError, subprocess.CalledProcessError) as e:
        raise VenvOpError(f"venv create failed: {e}") from e


def install_into(venv_path: Path, pip_args: list[str]) -> None:
    """Run ``<venv>/bin/python -m pip install <pip_args>`` synchronously.

    Stdout / stderr are surfaced via subprocess.run + check=True so the
    caller sees pip's output for debugging.  Progress streaming for the
    splash UI is wrapped at the :mod:`update_runner` layer (it spawns
    pip via :class:`subprocess.Popen` and tails the pipe).
    """
    log = get_logger()
    py = venv_python(venv_path)
    if not py.is_file():
        raise VenvOpError(f"venv python missing at {py}")
    cmd = [str(py), "-m", "pip", "install", *pip_args]
    log.info("venv: pip install %s", pip_args)
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        raise VenvOpError(f"pip install failed (rc={e.returncode})") from e


def smoke_test(venv_path: Path) -> str:
    """Verify the venv's framework is importable + ``kt --help`` runs.

    Returns the framework version string on success.  Raises
    :class:`VenvOpError` on any failure.  Catches a stuck ``kt --help``
    via the timeout — neither the GUI nor CI should ever wait longer.
    """
    py = venv_python(venv_path)
    if not py.is_file():
        raise VenvOpError(f"venv python missing at {py}")

    # 1. Framework importable + reports a version.
    try:
        out = subprocess.run(
            [
                str(py),
                "-c",
                "import kohakuterrarium, sys; "
                "v = getattr(kohakuterrarium, '__version__', ''); "
                "sys.stdout.write(v or '<no-version>')",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=SMOKE_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise VenvOpError(f"framework import failed: {e}") from e
    version = (out.stdout or "").strip() or "<no-version>"

    # 2. ``kt --help`` exits 0.
    kt = venv_kt(venv_path)
    if not kt.is_file():
        raise VenvOpError(f"kt console script missing at {kt}")
    try:
        subprocess.run(
            [str(kt), "--help"],
            check=True,
            capture_output=True,
            timeout=SMOKE_TIMEOUT_SECONDS,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise VenvOpError(f"`kt --help` failed: {e}") from e

    return version


def atomic_swap(current: Path, new: Path, backup: Path) -> None:
    """Make ``new/`` become ``current/`` and stash the prior ``current/``
    at ``backup/`` for one-shot rollback.

    POSIX guarantees both renames are atomic.  Windows ``os.replace``
    works on directories ONLY if the target doesn't exist, so we
    first remove ``backup`` if it's lying around, then rename.
    """
    log = get_logger()
    if backup.exists():
        log.info("venv: removing stale backup at %s", backup)
        shutil.rmtree(backup, ignore_errors=True)
    if current.exists():
        log.info("venv: %s -> %s (stash)", current, backup)
        try:
            current.replace(backup)
        except OSError as e:
            raise VenvOpError(f"failed to stash current venv: {e}") from e
    log.info("venv: %s -> %s (promote)", new, current)
    try:
        new.replace(current)
    except OSError as e:
        # Try to undo the stash so the user isn't left without any venv.
        if backup.exists() and not current.exists():
            try:
                backup.replace(current)
            except OSError:
                pass
        raise VenvOpError(f"failed to promote venv.new: {e}") from e


def rollback(current: Path, backup: Path, broken: Path) -> None:
    """Inverse of :func:`atomic_swap`.

    Moves ``current/`` aside to ``broken/`` and promotes ``backup/`` back.
    Raises :class:`VenvOpError` if ``backup/`` doesn't exist — caller
    should fall to the C2 recovery path then.
    """
    log = get_logger()
    if not backup.exists():
        raise VenvOpError(
            f"no rollback target at {backup}; recovery requires C2 bundled wheels"
        )
    if broken.exists():
        shutil.rmtree(broken, ignore_errors=True)
    if current.exists():
        log.info("venv: rollback — %s -> %s (broken)", current, broken)
        current.replace(broken)
    log.info("venv: rollback — %s -> %s (restore)", backup, current)
    backup.replace(current)


def write_wrapper_marker(venv_path: Path) -> None:
    """Drop the wrapper sentinel inside the venv.

    ``kt self-update`` keys on this to decide whether the install is
    wrapper-managed (and therefore should use the atomic-rename path).
    """
    marker = venv_path / ".kt-wrapper-marker"
    marker.write_text(f"kt-wrapper {sys.version_info[0]}.{sys.version_info[1]}\n")


__all__ = [
    "SMOKE_TIMEOUT_SECONDS",
    "VenvOpError",
    "create_venv",
    "install_into",
    "smoke_test",
    "atomic_swap",
    "rollback",
    "write_wrapper_marker",
]
