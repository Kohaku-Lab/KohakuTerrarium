"""Worker-owned occupancy for exact-path Laboratory file mutations."""

import os
from collections.abc import Callable
from pathlib import Path

from kohakuterrarium.session.occupancy.family import acquire_family
from kohakuterrarium.session.occupancy.mutation import run_mutation_worker


def write_bytes_enrolled(
    path: str | Path,
    data: bytes,
    validate: Callable[[], None] | None = None,
) -> None:
    """Overwrite ``path`` with a native handle enrolled before open.

    ``validate`` runs after enrollment and before the file is opened, so a
    caller can re-check state that may have changed while admission was
    pending; a rejection aborts the write before any byte is touched.
    """
    target = Path(path)
    lease = acquire_family([target], "files_write")
    if validate is not None:
        try:
            validate()
        except BaseException:
            lease.finish(source_released=True)
            raise
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


def replace_enrolled(
    src: str | Path,
    dst: str | Path,
    validate: Callable[[], None] | None = None,
) -> None:
    """Publish a file while occupying both paths and conventional session owners.

    ``validate`` runs after enrollment and before the replace, so a caller
    can re-check state that may have changed while admission was pending.
    """
    source = Path(src)
    target = Path(dst)
    lease = acquire_family([source, target], "files_replace")
    try:
        if validate is not None:
            validate()
        os.replace(source, target)
    finally:
        lease.finish(source_released=True)


def unlink_enrolled(
    path: str | Path,
    validate: Callable[[], None] | None = None,
) -> None:
    """Unlink one exact file after occupying its original identity.

    Handle-free: occupancy is released after ``os.unlink`` returns, even
    when the syscall fails. Directory trees are out of scope here.

    ``validate`` runs after enrollment and before the unlink, so a caller
    can re-check state that may have changed while admission was pending.
    """
    target = Path(path)
    lease = acquire_family([target], "files_delete")
    try:
        if validate is not None:
            validate()
        os.unlink(target)
    finally:
        lease.finish(source_released=True)


__all__ = [
    "replace_enrolled",
    "run_mutation_worker",
    "unlink_enrolled",
    "write_bytes_enrolled",
]
