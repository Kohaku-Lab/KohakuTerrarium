"""Worker-owned occupancy for exact-path Laboratory file mutations."""

import os
from pathlib import Path

from kohakuterrarium.session.occupancy.family import acquire_family
from kohakuterrarium.session.occupancy.mutation import run_mutation_worker


def write_bytes_enrolled(path: str | Path, data: bytes) -> None:
    """Overwrite ``path`` with a native handle enrolled before open."""
    target = Path(path)
    lease = acquire_family([target], "files_write")
    write_error: BaseException | None = None
    close_error: BaseException | None = None
    released = False
    fh = None
    try:
        fh = open(target, "wb")
        released = False
        fh.write(data)
    except BaseException as exc:
        write_error = exc
    finally:
        if fh is not None:
            try:
                fh.close()
                released = True
            except BaseException as exc:
                close_error = exc
                released = False
        lease.finish(source_released=released)
    if write_error is not None:
        if close_error is not None:
            raise write_error from close_error
        raise write_error
    if close_error is not None:
        raise close_error


def replace_enrolled(src: str | Path, dst: str | Path) -> None:
    """Publish a file while occupying both paths and conventional session owners."""
    source = Path(src)
    target = Path(dst)
    lease = acquire_family([source, target], "files_replace")
    try:
        os.replace(source, target)
    finally:
        lease.finish(source_released=True)


def unlink_enrolled(path: str | Path) -> None:
    """Unlink one exact file after occupying its original identity.

    Handle-free: occupancy is released after ``os.unlink`` returns, even
    when the syscall fails. Directory trees are out of scope here.
    """
    target = Path(path)
    lease = acquire_family([target], "files_delete")
    try:
        os.unlink(target)
    finally:
        lease.finish(source_released=True)


__all__ = [
    "replace_enrolled",
    "run_mutation_worker",
    "unlink_enrolled",
    "write_bytes_enrolled",
]
