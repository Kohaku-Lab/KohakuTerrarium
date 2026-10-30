"""Batched KVault reads and copies that skip unreadable rows (blocking)."""

from collections.abc import Callable, Iterator, Sequence
from typing import Any

from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

# Chunks grow from FIRST_READ_CHUNK to READ_CHUNK, so a scan that stops early
# decodes few rows and a full scan still batches.
FIRST_READ_CHUNK = 16
READ_CHUNK = 256
# Keys-only pages are small; at 1024 a 20k-key prefix scan costs 2x 8192's.
KEY_SCAN_BATCH = 8192
_MISSING = object()


def key_text(key: Any) -> str:
    """A KVault key as text."""
    return key.decode("utf-8", errors="replace") if isinstance(key, bytes) else key


def scan_keys(table: Any, prefix: str | bytes | None = None) -> list[Any]:
    """Every key of ``table`` under ``prefix``, in key order."""
    return list(table.keys(prefix=prefix, batch_size=KEY_SCAN_BATCH))


OnUnreadable = Callable[[Any, Exception], None]


def _read_one(table: Any, key: Any, on_unreadable: OnUnreadable) -> Any:
    try:
        return table[key]
    except KeyError:
        return _MISSING
    except Exception as e:
        on_unreadable(key, e)
        return _MISSING


def _log_unreadable(what: str) -> OnUnreadable:
    def log(key: Any, error: Exception) -> None:
        logger.warning(
            f"Failed to read {what}", key=key_text(key), error=str(error), exc_info=True
        )

    return log


def read_values(
    table: Any,
    keys: Sequence[Any],
    *,
    what: str = "session value",
    strict: bool = False,
    on_unreadable: OnUnreadable | None = None,
) -> Iterator[tuple[Any, Any]]:
    """Yield ``(key, value)`` for every present key, in input order.

    Keys are read in ``get_many`` chunks that double from ``FIRST_READ_CHUNK``
    up to ``READ_CHUNK``. A chunk that fails as a whole is re-read key by key,
    so only the unreadable rows are reported (logged by default) and skipped;
    with ``strict`` the failure propagates instead.
    """
    report = on_unreadable or _log_unreadable(what)
    start, size = 0, min(FIRST_READ_CHUNK, READ_CHUNK)
    while start < len(keys):
        chunk = list(keys[start : start + size])
        start += size
        size = min(size * 2, READ_CHUNK)
        try:
            values = table.get_many(chunk, _MISSING)
        except Exception:
            if strict:
                raise
            values = [_read_one(table, key, report) for key in chunk]
        for key, value in zip(chunk, values):
            if value is not _MISSING:
                yield key, value


def read_prefix(
    table: Any,
    prefix: str | bytes | None = None,
    *,
    what: str = "session value",
    strict: bool = False,
    on_unreadable: OnUnreadable | None = None,
) -> Iterator[tuple[Any, Any]]:
    """Yield ``(key, value)`` for every key under ``prefix``, in key order."""
    keys = scan_keys(table, prefix)
    return read_values(
        table, keys, what=what, strict=strict, on_unreadable=on_unreadable
    )


def write_rows(dst: Any, rows: list[tuple[Any, Any]], *, what: str = "row") -> int:
    """Commit ``rows`` as one ``put_many`` batch; return how many were written.

    A batch that fails as a whole is written row by row, so only the
    unwritable rows are logged and skipped.
    """
    if not rows:
        return 0
    try:
        dst.put_many(rows)
        return len(rows)
    except Exception:
        written = 0
        for key, value in rows:
            try:
                dst[key] = value
                written += 1
            except Exception as e:
                logger.warning(
                    f"Failed to write {what}", key=key_text(key), error=str(e)
                )
        return written


def copy_rows(
    src: Any, dst: Any, keys: Sequence[Any] | None = None, *, what: str = "row"
) -> int:
    """Copy present ``keys`` (default: all) from ``src`` to ``dst``; return the count."""
    rows = (
        read_prefix(src, what=what)
        if keys is None
        else read_values(src, keys, what=what)
    )
    written = 0
    batch: list[tuple[Any, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= READ_CHUNK:
            written += write_rows(dst, batch, what=what)
            batch = []
    return written + write_rows(dst, batch, what=what)
