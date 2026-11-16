"""Bounded snapshots for the suite-level real-config pollution guard."""

import ctypes
import hashlib
import os
from pathlib import Path

REAL_CONFIG_DIR = (
    Path(os.environ.get("KT_TEST_REAL_HOME", Path.home())) / ".kohakuterrarium"
)
SIDECAR_SUFFIXES: tuple[str, ...] = ("-wal", "-shm", "-journal", ".migrate-lock")
HASH_CHUNK_SIZE = 1024 * 1024
RUNTIME_STATE_SUBDIRS: tuple[str, ...] = (
    "run",
    "logs",
    "sessions/mirror",
)


def is_runtime_state(path: Path) -> bool:
    try:
        relative = path.relative_to(REAL_CONFIG_DIR)
    except ValueError:
        return False
    parts = relative.parts
    if "__pycache__" in parts or any(part.endswith(".pyc") for part in parts):
        return True
    if path.name.endswith(SIDECAR_SUFFIXES):
        return True
    return any(
        parts[: len(sub.split("/"))] == tuple(sub.split("/"))
        for sub in RUNTIME_STATE_SUBDIRS
    )


def is_session_file(path: Path) -> bool:
    try:
        path.relative_to(REAL_CONFIG_DIR / "sessions")
    except ValueError:
        return False
    return True


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot() -> dict[str, str | None]:
    """Capture bounded content metadata for the real configuration directory."""
    if not REAL_CONFIG_DIR.exists():
        return {}
    result: dict[str, str | None] = {}
    for path in REAL_CONFIG_DIR.rglob("*"):
        if not path.is_file() or is_runtime_state(path):
            continue
        try:
            result[str(path)] = None if is_session_file(path) else file_digest(path)
        except OSError:
            continue
    return result


def find_leaks(
    before: dict[str, str | None], after: dict[str, str | None]
) -> list[str]:
    """Return new or changed non-session files since the baseline snapshot."""
    leaks: list[str] = []
    for path, digest in after.items():
        if path not in before:
            leaks.append(f"NEW: {path}")
        elif before[path] != digest:
            file_path = Path(path)
            if is_session_file(file_path) or file_path.name.endswith(
                ("-wal", "-shm", "-journal")
            ):
                continue
            leaks.append(f"CONTENT-CHANGED: {path}")
    return leaks


def pid_alive(pid: int) -> bool:
    """Report whether a pid is running, without signalling it."""
    if os.name == "nt":
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def running_daemon_pid(config_dir: Path | None = None) -> int | None:
    """Return the pid of a live KT web daemon, or None if none is running."""
    root = REAL_CONFIG_DIR if config_dir is None else config_dir
    try:
        pid = int((root / "run" / "web.pid").read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if pid_alive(pid) else None
