"""Regression guard: no test ever writes to the operator's real config.

The whole suite runs with ``KT_CONFIG_DIR`` redirected to a per-test
``tmp_path`` via the autouse fixture in ``tests/conftest.py``.  This
file is a separate, last-resort check: snapshot the *content* of
``~/.kohakuterrarium/`` before this module runs and fail the test if any
file's content changed during the test run.

A failure here usually means a new test introduced a save / write
path that resolves through an absolute ``Path.home()`` lookup instead
of ``config_dir()`` — for example the deprecated
``monkeypatch.setattr(mod, "PROFILES_PATH", …)`` seam that stopped
working when the live read/write path moved to ``_profiles_path()``.

Existing session files are tracked by path only because this guard already
ignores their content changes. Other files use chunked SHA-256 hashing so
large files do not need to be loaded into memory. The xdist group keeps the
module's snapshot work in one worker.
"""

import ctypes
import hashlib
import os
import sys
from pathlib import Path

import pytest

from kohakuterrarium.llm.profiles.api_keys import PROVIDER_KEY_MAP, get_api_key

_REAL_CONFIG_DIR = Path.home() / ".kohakuterrarium"

# Subtrees populated by a *running* KohakuTerrarium daemon, not by
# any test path. The operator may have ``kt serve`` / a browser tab
# open while running pytest — those processes legitimately write
# under these subtrees concurrently and a content drift there is
# NOT a test leak. Tests never touch these paths.
# SQLite/lock sidecars appear whenever *any* process opens a store — the
# operator's daemon opening a pre-existing session included. A real leak mints
# the base file, which is still flagged.
_SIDECAR_SUFFIXES: tuple[str, ...] = ("-wal", "-shm", "-journal", ".migrate-lock")
_HASH_CHUNK_SIZE = 1024 * 1024

pytestmark = pytest.mark.xdist_group("real-config-pollution")

_RUNTIME_STATE_SUBDIRS: tuple[str, ...] = (
    "run",  # web daemon PID / state / log files
    "logs",  # framework log files
    "sessions/mirror",  # SessionMirrorWriter rewrites the .kohakutr
    # mirror as the live KT daemon receives events
)


def _is_runtime_state(p: Path) -> bool:
    try:
        rel = p.relative_to(_REAL_CONFIG_DIR)
    except ValueError:
        return False
    parts = rel.parts
    # Python's import system writes ``__pycache__/*.pyc`` next to any
    # imported module — including modules under
    # ``packages/<pkg>/<...>/`` from ``kt install``ed packages.  These
    # are interpreter-managed bytecode caches, not framework config
    # writes.  Skip them so simply importing an installed package
    # during the test run doesn't trip the leak guard.
    if "__pycache__" in parts or any(part.endswith(".pyc") for part in parts):
        return True
    if p.name.endswith(_SIDECAR_SUFFIXES):
        return True
    for sub in _RUNTIME_STATE_SUBDIRS:
        sub_parts = tuple(sub.split("/"))
        if parts[: len(sub_parts)] == sub_parts:
            return True
    return False


def _is_session_file(p: Path) -> bool:
    try:
        p.relative_to(_REAL_CONFIG_DIR / "sessions")
    except ValueError:
        return False
    return True


def _file_digest(p: Path) -> str:
    digest = hashlib.sha256()
    with p.open("rb") as stream:
        while chunk := stream.read(_HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot() -> dict[str, str | None]:
    """Map every test-touchable file under the real config dir to its
    content SHA-256.

    Files under runtime-state subtrees (``run/``, ``logs/``,
    ``sessions/mirror/``) are excluded — those belong to whatever
    KT process the operator may have running concurrently.

    Files that disappear or become unreadable between snapshot and
    test are silently dropped — only new content / changed content
    in surviving files is treated as a leak.
    """
    if not _REAL_CONFIG_DIR.exists():
        return {}
    out: dict[str, str | None] = {}
    for p in _REAL_CONFIG_DIR.rglob("*"):
        if not p.is_file():
            continue
        if _is_runtime_state(p):
            continue
        try:
            out[str(p)] = None if _is_session_file(p) else _file_digest(p)
        except OSError:
            # File may have been deleted / locked / become inaccessible
            # between iglob and read — that's an external race, not a
            # test leak.  Skip it.
            continue
    return out


def _pid_alive(pid: int) -> bool:
    """Report whether a pid is running, without signalling it."""
    if sys.platform == "win32":
        # os.kill on Windows maps every signal other than CTRL_C_EVENT /
        # CTRL_BREAK_EVENT to TerminateProcess, so a "probe" would kill the
        # daemon it is asking about.
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Owned by another user: running, just not ours to signal.
        return True
    except OSError:
        return False
    return True


def _running_daemon_pid(config_dir: Path | None = None) -> int | None:
    """Return the pid of a live KT web daemon, or None if none is running."""
    root = _REAL_CONFIG_DIR if config_dir is None else config_dir
    try:
        pid = int((root / "run" / "web.pid").read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


@pytest.fixture(scope="module", autouse=True)
def _real_config_snapshot():
    """Check the real config after all module tests finish."""
    before = _snapshot()
    yield
    _assert_no_real_config_leaks(before, _snapshot())


def _assert_no_real_config_leaks(
    before: dict[str, str | None], after: dict[str, str | None]
) -> None:
    leaks: list[str] = []
    for path, digest in after.items():
        if path not in before:
            leaks.append(f"NEW: {path}")
        elif before[path] != digest:
            file_path = Path(path)
            if _is_session_file(file_path) or file_path.name.endswith(
                ("-wal", "-shm", "-journal")
            ):
                continue
            leaks.append(f"CONTENT-CHANGED: {path}")
    daemon = _running_daemon_pid()
    if leaks and daemon is not None:
        pytest.skip(
            f"a KohakuTerrarium daemon is running (pid {daemon}); its writes to "
            "the real config dir cannot be distinguished from a test leak. "
            "Stop it and re-run to make this guard meaningful. Saw:\n"
            + "\n".join(leaks)
        )
    assert not leaks, (
        "tests wrote to the operator's real ~/.kohakuterrarium/ — every "
        "save path must resolve through KT_CONFIG_DIR (the conftest "
        "autouse fixture redirects to tmp_path).  Leaks:\n" + "\n".join(leaks)
    )


def test_snapshot_hashes_regular_files_and_tracks_sessions_by_path(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "config"
    session_dir = config_dir / "sessions"
    session_dir.mkdir(parents=True)
    regular = config_dir / "settings.json"
    session = session_dir / "large.kohakutr"
    regular.write_bytes(b"settings")
    session.write_bytes(b"session contents")
    monkeypatch.setattr(sys.modules[__name__], "_REAL_CONFIG_DIR", config_dir)
    monkeypatch.setattr(sys.modules[__name__], "_HASH_CHUNK_SIZE", 3)

    snapshot = _snapshot()

    assert snapshot[str(regular)] == hashlib.sha256(b"settings").hexdigest()
    assert snapshot[str(session)] is None


def test_session_content_changes_are_ignored_but_new_files_are_detected(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "config"
    session_dir = config_dir / "sessions"
    session_dir.mkdir(parents=True)
    existing = session_dir / "existing.kohakutr"
    regular = config_dir / "settings.json"
    existing.write_bytes(b"before")
    regular.write_bytes(b"before")
    monkeypatch.setattr(sys.modules[__name__], "_REAL_CONFIG_DIR", config_dir)

    before = _snapshot()
    existing.write_bytes(b"after")
    regular.write_bytes(b"after")
    with pytest.raises(AssertionError, match="CONTENT-CHANGED:"):
        _assert_no_real_config_leaks(before, _snapshot())

    regular.write_bytes(b"before")
    assert _assert_no_real_config_leaks(before, _snapshot()) is None

    new_session = session_dir / "new.kohakutr"
    new_session.write_bytes(b"new")
    with pytest.raises(AssertionError, match="NEW:"):
        _assert_no_real_config_leaks(before, _snapshot())


def test_sidecars_are_runtime_state_but_the_store_itself_is_not():
    base = _REAL_CONFIG_DIR / "sessions" / "s.kohakutr"
    assert not _is_runtime_state(base)
    for suffix in _SIDECAR_SUFFIXES:
        assert _is_runtime_state(Path(str(base) + suffix)), suffix


def test_daemon_probe_ignores_a_missing_or_dead_pid(tmp_path, monkeypatch):
    assert _running_daemon_pid(tmp_path) is None

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "web.pid").write_text("not-a-pid")
    assert _running_daemon_pid(tmp_path) is None

    # Our own pid is alive on every platform.
    (run_dir / "web.pid").write_text(str(os.getpid()))
    assert _running_daemon_pid(tmp_path) == os.getpid()

    monkeypatch.setattr(sys.modules[__name__], "_pid_alive", lambda pid: False)
    (run_dir / "web.pid").write_text("999999")
    assert _running_daemon_pid(tmp_path) is None


async def test_a_test_cannot_start_a_real_provider_login():
    from kohakuterrarium.llm.codex import auth as codex_auth
    from kohakuterrarium.llm.codex import provider as codex_provider

    for module in (codex_auth, codex_provider):
        with pytest.raises(AssertionError, match="real provider login"):
            await module.oauth_login()


def test_operator_provider_keys_are_not_visible_to_tests():
    inherited = [name for name in PROVIDER_KEY_MAP.values() if name in os.environ]
    assert inherited == []
    for provider in PROVIDER_KEY_MAP:
        assert get_api_key(provider) == ""
