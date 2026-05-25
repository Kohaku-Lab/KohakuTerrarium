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


def clone_repo(url: str, target: Path, ref: str | None = None) -> None:
    """Clone ``url`` into ``target`` directory, optionally at ``ref``.

    ``ref`` may be a branch name, tag name, or commit SHA.  When
    omitted, clones the default branch at HEAD (existing behaviour).

    Selects the native git binary when available, otherwise falls
    back to dulwich.  Both code paths raise :class:`RuntimeError` on
    failure with a one-line diagnostic message.
    """
    if _has_native_git():
        _clone_native(url, target, ref)
    else:
        _clone_dulwich(url, target, ref)


def pull_repo(target: Path) -> None:
    """Pull (fast-forward only) the existing clone at ``target``.

    Same backend-selection rule as :func:`clone_repo`.
    """
    if _has_native_git():
        _pull_native(target)
    else:
        _pull_dulwich(target)


# ─── native git binary ────────────────────────────────────────────


def _clone_native(url: str, target: Path, ref: str | None = None) -> None:
    if ref:
        # ``git clone -b <ref>`` accepts branches AND tags directly —
        # the common path for marketplace-pinned installs.  SHAs are
        # NOT accepted by ``-b`` (git refuses); fall through to a
        # second-attempt plain clone + ``git checkout <ref>`` for
        # those.
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "-b", ref, url, str(target)],
                check=True,
                capture_output=True,
            )
            return
        except subprocess.CalledProcessError:
            # Clean up the partial dir before falling back.
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
    # Plain clone (no ref pin, or ref was rejected by ``-b``).
    try:
        subprocess.run(
            ["git", "clone", url, str(target)],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace").strip() if exc.stderr else str(exc)
        raise RuntimeError(f"Git clone failed: {stderr}") from exc
    if ref:
        try:
            subprocess.run(
                ["git", "-C", str(target), "checkout", ref],
                check=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (
                exc.stderr.decode(errors="replace").strip() if exc.stderr else str(exc)
            )
            # Tear down the partial checkout so a later
            # ``install_package(..., ref=...)`` doesn't see
            # ``target.exists()`` and silently fall through to a
            # pull-in-place (which would skip the requested ref
            # entirely).  Mirrors the same cleanup in the
            # ``-b <ref>`` retry above.
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            raise RuntimeError(
                f"Git checkout {ref!r} failed after clone: {stderr}"
            ) from exc


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


def _clone_dulwich(url: str, target: Path, ref: str | None = None) -> None:
    porcelain = _import_dulwich()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not ref:
        try:
            # ``porcelain.clone`` returns a live ``Repo`` whose open
            # file handles into ``.git`` block ``shutil.rmtree`` /
            # ``os.replace`` on Windows.  Always close before we hand
            # control back — the caller may need to move the dir
            # (transactional install does ``os.replace`` shortly after).
            _close_returned_repo(porcelain.clone(url, str(target), depth=1))
            return
        except Exception as exc:
            raise RuntimeError(f"Git clone failed (dulwich): {exc}") from exc

    # ``ref`` set — try branch, then tag.  Dulwich's ``porcelain.clone``
    # accepts ``branch=`` for both heads and tags (when given as a
    # full ``refs/...`` path), so we try the common cases in order.
    last_exc: Exception | None = None
    for ref_path in (ref.encode(), b"refs/tags/" + ref.encode()):
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        try:
            _close_returned_repo(
                porcelain.clone(url, str(target), depth=1, branch=ref_path)
            )
            return
        except Exception as exc:
            last_exc = exc
            continue

    # Last-ditch: full (no-depth) clone + post-clone reset.  Covers
    # the SHA case which dulwich's ``branch=`` doesn't accept.
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    try:
        _close_returned_repo(porcelain.clone(url, str(target)))
    except Exception as exc:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError(
            f"Git clone failed (dulwich): could not resolve ref {ref!r}: "
            f"{last_exc or exc}"
        ) from exc

    try:
        from dulwich.repo import Repo  # noqa: PLC0415

        repo = Repo(str(target))
        try:
            sha: bytes | None = None
            # Try the same prefix order as the clone retries, then raw SHA.
            for prefix in (
                b"refs/tags/",
                b"refs/heads/",
                b"refs/remotes/origin/",
                b"",
            ):
                try:
                    sha = repo.refs[prefix + ref.encode()]
                    break
                except KeyError:
                    continue
            if sha is None:
                # Raw SHA — look up directly in the object store.
                try:
                    obj = repo[ref.encode()]  # noqa: F841 — KeyError trigger
                    sha = obj.id
                except KeyError:
                    raise RuntimeError(
                        f"Git checkout {ref!r} failed (dulwich): ref not found"
                    )
            porcelain.reset(repo, "hard", sha)
        finally:
            # Release dulwich's open file handles BEFORE any cleanup
            # path runs (same Windows-handle hazard as above).
            repo.close()
    except RuntimeError:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise
    except Exception as exc:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        raise RuntimeError(f"Git checkout {ref!r} failed (dulwich): {exc}") from exc


def _close_returned_repo(maybe_repo) -> None:
    """Best-effort close of a ``Repo`` returned by dulwich porcelain.

    On Windows, leaving the cloned ``Repo`` open prevents subsequent
    ``shutil.rmtree`` / ``os.replace`` operations against the same
    target dir.  The transactional install path needs both, so every
    clone-success path explicitly closes here.
    """
    if maybe_repo is None:
        return
    close = getattr(maybe_repo, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # Closing is best-effort — don't mask a successful clone.
            pass


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
