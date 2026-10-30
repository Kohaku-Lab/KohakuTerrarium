"""Conventional session-sidecar identities for process-local mutation admission."""

import os
import re
from collections.abc import Iterable
from pathlib import Path

from kohakuterrarium.session.occupancy.gate import ResourceLease, acquire_legacy
from kohakuterrarium.utils.fs_path import coerce_fs_path

_OWNER = re.compile(
    r"(?P<owner>.+\.kohakutr(?:\.v[0-9]+)?)"
    r"(?:-(?:wal|shm|journal)|\.drives(?:-(?:wal|shm|journal)|"
    r"\.split-intent\.json(?:\..+)?)?)$"
)


def family_identities(paths: Iterable[str | Path]) -> tuple[str, ...]:
    """Return exact identities plus conventional lexical and resolved sidecar owners."""
    identities = set()
    for value in paths:
        lexical = coerce_fs_path(value).expanduser().absolute()
        resolved = lexical.resolve()
        identities.add(os.path.normcase(str(resolved)))
        for path in (lexical, resolved):
            match = _OWNER.fullmatch(os.path.normcase(path.name))
            if match is not None:
                owner = path.with_name(match["owner"]).resolve()
                identities.add(os.path.normcase(str(owner)))
    return tuple(sorted(identities))


class FamilyLease:
    """A group of legacy enrollments released together after a file operation."""

    def __init__(self) -> None:
        self._leases: list[ResourceLease] = []
        self._finished = False

    def finish(self, *, source_released: bool) -> None:
        """Release the group's occupancy or retain source-release uncertainty."""
        if self._finished:
            return
        self._finished = True
        for lease in reversed(self._leases):
            lease.finish(source_released=source_released)
        self._leases.clear()


def acquire_family(paths: Iterable[str | Path], operation: str) -> FamilyLease:
    """Enroll exact files and inferred owners before any source mutation."""
    identities = family_identities(paths)
    family = FamilyLease()
    try:
        for path in identities:
            lease = acquire_legacy(path, operation)
            try:
                family._leases.append(lease)
            except BaseException:
                lease.finish(source_released=True)
                raise
    except BaseException:
        family.finish(source_released=True)
        raise
    return family
