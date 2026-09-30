"""Blocking JSON record I/O and serialized lifecycle commands."""

import json
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from kohakuterrarium.utils.file_lock import FileLock, FileLockBusy


def workspace_identity(path: Path) -> str:
    return os.path.normcase(str(path.expanduser().resolve()))


def https_origin(value: str) -> str:
    parsed = urlsplit(value)
    parsed.port  # Reject malformed or out-of-range ports before saving.
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("public origin must be HTTPS without path or credentials")
    return "https://" + parsed.netloc.lower()


def write_json(path: Path, data: dict) -> None:
    """Atomic replacement; a failed write never truncates an existing record."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + 1
        while True:
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                # Windows readers/scanners can briefly deny FILE_SHARE_DELETE.
                # Keep the old complete record while waiting for their handle.
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path: Path) -> Any:
    """Read a JSON record, retrying brief access denials."""
    deadline = time.monotonic() + 1
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


@contextmanager
def lifecycle_command(lock: FileLock):
    deadline = time.monotonic() + 40
    while True:
        try:
            lock.acquire()
            break
        except FileLockBusy:
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "Another MCP lifecycle command is still running; retry"
                ) from None
            time.sleep(0.1)
    try:
        yield
    finally:
        lock.release()
