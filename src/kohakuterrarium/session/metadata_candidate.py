"""Admission-controlled metadata reads with explicit, default-off fast admission."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kohakuterrarium.session.readonly import (
    MetaDecodeError,
    MetaTableRead,
    read_session_meta,
    validate_metadata_keys,
)
from kohakuterrarium.session.resource_gate import try_fast
from kohakuterrarium.utils.fs_path import coerce_fs_path


class MetadataReadError(Exception):
    """A failed candidate read, including primary and independent cleanup evidence."""

    def __init__(
        self,
        stage: str,
        cause: BaseException,
        cleanup_errors: tuple[BaseException, ...] = (),
    ) -> None:
        self.stage = stage
        self.cleanup_errors = cleanup_errors
        super().__init__(f"Session metadata candidate failed during {stage}")
        self.__cause__ = cause


@dataclass(frozen=True, slots=True)
class MetadataRead:
    """Materialized metadata and the admission reason selecting its read path."""

    meta: dict[str, Any]
    reason: str


def read_metadata_candidate(
    path: str | Path, *, eligible: bool = False, keys: tuple[str, ...] | None = None
) -> MetadataRead:
    """Read under a sole-occupant admission, or enrolled alongside other occupants.

    Both paths read through a read-only handle; admission only decides which
    occupancy the read holds while that handle is open.
    """
    validate_metadata_keys(keys)
    source = coerce_fs_path(path).expanduser().resolve(strict=True)
    admission = try_fast(source, eligible=eligible)
    if admission.lease is None:
        meta = (
            read_session_meta(source)
            if keys is None
            else read_session_meta(source, keys=keys)
        )
        return MetadataRead(meta, admission.reason)
    read = MetaTableRead(source, missing_ok=False, keys=keys)
    primary: BaseException | None = None
    meta: dict[str, Any] = {}
    try:
        meta = read.run()
    except BaseException as exc:
        primary = exc
    finally:
        admission.lease.finish(source_released=read.released)
    cleanup = () if read.close_error is None else (read.close_error,)
    if primary is not None:
        if not isinstance(primary, Exception):
            primary.metadata_cleanup_errors = cleanup
            raise primary
        if not read.released:
            stage = "resource"
        elif isinstance(primary, MetaDecodeError):
            stage = "decode"
        else:
            stage = "sql"
        raise MetadataReadError(stage, primary, cleanup) from primary
    if read.close_error is not None:
        cause = read.close_error
        if not isinstance(cause, Exception):
            raise cause
        raise MetadataReadError("resource", cause, cleanup) from cause
    return MetadataRead(meta, admission.reason)
