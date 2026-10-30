"""Open session tables read-only without touching the session file family."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kohakuvault import KVault, TextVault

_MISSING_TABLE = "no such table"
# A read-only open of an absent FTS table fails when TextVault tries to create it.
_READONLY_CREATE = "attempt to write a readonly database"


class AbsentTable:
    """Read-only stand-in for a table the session file does not contain."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._closed = False

    def get(self, key: Any, default: Any = None) -> Any:
        return default

    def __getitem__(self, key: Any) -> Any:
        raise KeyError(key)

    def __contains__(self, key: Any) -> bool:
        return False

    def __len__(self) -> int:
        return 0

    def keys(self, prefix: Any = None, limit: int | None = None, **_: Any) -> Iterator:
        return iter(())

    def items(self, prefix: Any = None, limit: int | None = None, **_: Any) -> Iterator:
        return iter(())

    def values(
        self, prefix: Any = None, limit: int | None = None, **_: Any
    ) -> Iterator:
        return iter(())

    def get_many(self, keys: list | tuple, default: Any = None) -> list[Any]:
        return [default] * len(keys)

    def flush_cache(self) -> int:
        return 0

    def checkpoint(self) -> None:
        return None

    def enable_auto_pack(self, use_pickle: bool = True) -> None:
        return None

    def close(self) -> None:
        self._closed = True

    def _reject(self, *args: Any, **kwargs: Any) -> None:
        raise PermissionError(f"session table {self.name!r} is read-only and absent")

    put = __setitem__ = __delitem__ = put_many = delete = pop = _reject
    setdefault = update = clear = enable_cache = _reject


def wal_path(path: Path) -> Path:
    """The ``-wal`` sidecar of a session file."""
    return Path(f"{path}-wal")


def is_snapshot_target(path: Path, snapshot: bool) -> bool:
    """Whether a read of ``path`` opens immutable rather than WAL-shared."""
    return snapshot and not wal_path(path).exists()


def open_read_table(
    path: Path, table: str, *, snapshot: bool, **options: Any
) -> KVault | None:
    """Open ``table`` read-only with auto-pack; ``None`` when the table is absent.

    ``snapshot`` reads of a file without ``-wal`` open immutable: no sidecars,
    but a writer that starts afterwards is not observed, so they suit
    request-scoped reads only. Otherwise the open shares the WAL and sees every
    committed row; on a checkpointed file it leaves ``-wal``/``-shm`` behind.
    An ``Exception`` raised here leaves no handle open.
    """
    if is_snapshot_target(path, snapshot):
        target = path.resolve().as_uri() + "?immutable=1"
    else:
        target = str(path)
    try:
        vault = KVault(target, table=table, read_only=True, **options)
    except Exception as exc:
        if _MISSING_TABLE in str(exc):
            return None
        raise
    try:
        vault.enable_auto_pack()
    except Exception:
        vault.close()
        raise
    return vault


def open_read_text(path: Path, table: str, *, snapshot: bool) -> TextVault | None:
    """Open an FTS ``table`` read-only; ``None`` when the file has no such table.

    The target follows ``open_read_table``; the WAL-shared form is a ``mode=ro``
    URI, since TextVault has no read-only flag of its own.
    """
    immutable = is_snapshot_target(path, snapshot)
    target = path.resolve().as_uri() + ("?immutable=1" if immutable else "?mode=ro")
    try:
        fts = TextVault(target, table=table)
    except Exception as exc:
        if _READONLY_CREATE in str(exc):
            return None
        raise
    fts.enable_auto_pack()
    return fts


def open_read_table_or_absent(
    path: Path, table: str, *, snapshot: bool, **options: Any
) -> Any:
    """``open_read_table``, with an :class:`AbsentTable` for a missing table."""
    vault = open_read_table(path, table, snapshot=snapshot, **options)
    return AbsentTable(table) if vault is None else vault
