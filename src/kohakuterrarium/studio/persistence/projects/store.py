"""Saved projects file: ``projects.json`` in the config dir, or per L4 user.

Shape: ``{"version": 1, "projects": [{"id", "path", "node", "name", "pinned",
"hidden", "added_at"}, ...]}``. Fields this module does not know are kept on
every write. A file that is not this shape raises :class:`ProjectsFileError`
and is never overwritten. Writes are locked (thread + file lock) and atomic.
"""

import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from kohakuterrarium.errors import ConflictError, ProjectsFileError
from kohakuterrarium.studio.persistence.projects.paths import (
    normalize_node,
    normalize_path,
    project_id,
)
from kohakuterrarium.utils.config_dir import config_dir
from kohakuterrarium.utils.file.lock import FileLock, FileLockBusy

FILE_VERSION = 1

_T = TypeVar("_T")
_THREAD_LOCK = threading.Lock()
_LOCK_TIMEOUT_S = 10.0
_LOCK_POLL_S = 0.02


def projects_path(user_id: int | None = None) -> Path:
    """Return the shared file, or ``users/<id>/projects.json`` for an L4 user."""
    if user_id is None:
        return config_dir() / "projects.json"
    return config_dir() / "users" / str(int(user_id)) / "projects.json"


def _empty_document() -> dict[str, Any]:
    return {"version": FILE_VERSION, "projects": []}


def _validate(document: Any, path: Path) -> dict[str, Any]:
    """Return ``document`` with every record's ``id`` set from its key."""
    if not isinstance(document, dict):
        raise ProjectsFileError(
            f"Projects file is not a JSON object: {path}", path=str(path)
        )
    records = document.setdefault("projects", [])
    if not isinstance(records, list):
        raise ProjectsFileError(
            f"Projects file has no 'projects' list: {path}", path=str(path)
        )
    for position, record in enumerate(records):
        raw_path = record.get("path") if isinstance(record, dict) else None
        raw_node = record.get("node") if isinstance(record, dict) else None
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ProjectsFileError(
                f"Project #{position} in {path} has no folder path", path=str(path)
            )
        if raw_node is not None and not isinstance(raw_node, str):
            raise ProjectsFileError(
                f"Project #{position} in {path} has a non-text node", path=str(path)
            )
        node = normalize_node(raw_node)
        _, key = normalize_path(raw_path, node)
        record["id"] = project_id(node, key)
    return document


def load_document(user_id: int | None = None) -> dict[str, Any]:
    """Read the projects document; a missing file is an empty document."""
    path = projects_path(user_id)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty_document()
    except OSError as exc:
        raise ProjectsFileError(
            f"Cannot read projects file {path}: {exc}", path=str(path)
        ) from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise ProjectsFileError(
            f"Projects file is not valid JSON: {path}: {exc}", path=str(path)
        ) from exc
    return _validate(document, path)


def load_records(user_id: int | None = None) -> list[dict[str, Any]]:
    """Every saved project record, in file order."""
    return list(load_document(user_id)["projects"])


def _atomic_write(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` through a flushed same-directory temp file."""
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".kt-tmp"
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _acquire(lock: FileLock) -> None:
    """Take the cross-process lock, polling up to a bounded deadline."""
    deadline = time.monotonic() + _LOCK_TIMEOUT_S
    while True:
        try:
            lock.acquire()
            return
        except FileLockBusy as exc:
            if time.monotonic() >= deadline:
                raise ConflictError(
                    f"Projects file is locked by another writer: {lock.path}"
                ) from exc
            time.sleep(_LOCK_POLL_S)


def update_document(
    mutate: Callable[[dict[str, Any]], _T], *, user_id: int | None = None
) -> _T:
    """Load, ``mutate`` in place, and atomically save the projects document.

    The read-modify-write runs under one thread lock and one file lock, so
    concurrent writers never lose each other's changes. ``mutate``'s return
    value is returned; when it raises, nothing is written.
    """
    path = projects_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(path.with_name(path.name + ".lock"))
    with _THREAD_LOCK:
        _acquire(lock)
        try:
            document = load_document(user_id)
            result = mutate(document)
            _validate(document, path)
            document["version"] = document.get("version") or FILE_VERSION
            payload = json.dumps(document, ensure_ascii=False, indent=2)
            _atomic_write(path, payload.encode("utf-8"))
            return result
        finally:
            lock.release()


__all__ = [
    "FILE_VERSION",
    "load_document",
    "load_records",
    "projects_path",
    "update_document",
]
