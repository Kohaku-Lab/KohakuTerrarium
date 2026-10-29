"""Strictly read session metadata without writing to the session file family."""

from pathlib import Path
from typing import Any

from kohakuvault import KVault
from kohakuvault.errors import KohakuVaultError

from kohakuterrarium.session.legacy_memory import copy_legacy_memory_only
from kohakuterrarium.session.resource_gate import ResourceReleaseError, acquire_legacy
from kohakuterrarium.session.vault_handles import dispose
from kohakuterrarium.session.vault_io import read_prefix
from kohakuterrarium.session.vault_read import open_read_table
from kohakuterrarium.utils.fs_path import coerce_fs_path
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


class MetaDecodeError(ValueError):
    """A strict metadata read met a key or value it cannot decode."""


def validate_metadata_keys(keys: tuple[str, ...] | None) -> None:
    """Validate an optional selection of metadata keys."""
    if keys is not None and (
        not isinstance(keys, tuple) or any(not isinstance(key, str) for key in keys)
    ):
        raise TypeError("metadata keys must be a tuple of strings or None")


def _strict_key(key: Any) -> str:
    if isinstance(key, bytes):
        try:
            return key.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MetaDecodeError("Metadata key is not UTF-8 text") from exc
    if isinstance(key, str):
        return key
    raise MetaDecodeError("Metadata key is not UTF-8 text")


class MetaTableRead:
    """One read of a session's ``meta`` table that records handle release.

    ``released`` is False only while a native handle may still be open, which
    is what an occupancy lease must report when it finishes. Any undecodable
    key or value fails the read. A missing table reads as ``{}`` when
    ``missing_ok`` or when strict admission recognizes legacy memory.
    """

    def __init__(
        self,
        source: Path,
        *,
        missing_ok: bool,
        strict_open: bool = False,
        keys: tuple[str, ...] | None = None,
    ) -> None:
        validate_metadata_keys(keys)
        self.keys = keys
        self.source = source
        self.missing_ok = missing_ok
        self.strict_open = strict_open
        self.released = True
        self.close_error: BaseException | None = None

    def run(self) -> dict[str, Any]:
        """Return decoded rows; open and read failures propagate.

        A close failure is kept in ``close_error`` instead of being raised.
        """
        if self.strict_open:
            self.released = False
            try:
                vault = KVault(
                    self.source,
                    table="meta",
                    read_only=True,
                    enable_wal=False,
                    mmap_size=0,
                    cache_kb=1024,
                    reader_cache_kb=1024,
                    reader_idle_limit=1,
                )
            except Exception as exc:
                if (
                    type(exc) is KohakuVaultError
                    and str(exc) == "SQLite error: no such table: meta"
                ):
                    self.released = True
                    if copy_legacy_memory_only(self.source):
                        return {}
                    raise
                raise ResourceReleaseError(
                    f"Session metadata open failed: {exc}"
                ) from exc
        else:
            vault = open_read_table(self.source, "meta", snapshot=True)
        if vault is None:
            if self.missing_ok:
                return {}
            raise FileNotFoundError(f"Session has no meta table: {self.source}")
        self.released = False
        try:
            if self.strict_open:
                vault.enable_auto_pack()
            if self.keys is not None:
                selected = {}
                for key in dict.fromkeys(self.keys):
                    try:
                        selected[key] = vault[key]
                    except KeyError:
                        pass
                return selected
            rows = read_prefix(vault, what="session metadata", strict=True)
            return {_strict_key(key): value for key, value in rows}
        finally:
            try:
                self.released = dispose(vault)
            except BaseException as exc:
                self.close_error = exc
                self.released = False


def read_strict_session_meta(
    path: str | Path, *, keys: tuple[str, ...] | None = None
) -> dict[str, Any]:
    """Read metadata for writable admission with strict resource cleanup."""
    validate_metadata_keys(keys)
    source = coerce_fs_path(path).expanduser().resolve(strict=True)
    lease = acquire_legacy(source, "read_format")
    read = MetaTableRead(source, missing_ok=False, strict_open=True, keys=keys)
    primary = None
    try:
        try:
            meta = read.run()
        except BaseException as exc:
            primary = exc
        if primary is not None and not isinstance(primary, Exception):
            primary.metadata_cleanup_errors = (
                () if read.close_error is None else (read.close_error,)
            )
            raise primary
        if read.close_error is not None and not isinstance(read.close_error, Exception):
            raise read.close_error from primary
        if isinstance(primary, ResourceReleaseError) and read.close_error is None:
            raise primary
        if read.close_error is not None or not read.released:
            error = ResourceReleaseError("Session metadata release failed")
            error.cleanup_errors = (
                () if read.close_error is None else (read.close_error,)
            )
            raise error from (primary if primary is not None else read.close_error)
        if primary is not None:
            raise primary
        return meta
    finally:
        lease.finish(source_released=read.released)


def read_session_meta(
    path: str | Path, *, keys: tuple[str, ...] | None = None
) -> dict[str, Any]:
    """Return metadata without creating WAL, SHM, schema, or status writes.

    A failed handle close is logged and leaves the file's occupancy uncertain.
    """
    validate_metadata_keys(keys)
    source = coerce_fs_path(path).expanduser().resolve(strict=True)
    lease = acquire_legacy(source, "read_meta")
    read = MetaTableRead(source, missing_ok=True, keys=keys)
    try:
        meta = read.run()
        if read.close_error is not None:
            logger.warning(
                "Failed to close read-only session metadata",
                error=str(read.close_error),
            )
        return meta
    finally:
        lease.finish(source_released=read.released)
