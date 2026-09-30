"""Selective, read-only session queries without writable source vault handles.

A file no live store holds open is read through a SQLite read transaction that
includes committed WAL rows, and KohakuVault only decodes selected values in an
isolated in-memory vault. A file that a live store in this process holds open is
read through that store, because a second SQLite library copy on the same file
crashes the process.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

from kohakuvault import KVault

from kohakuterrarium.session.errors import SessionNotReadyError
from kohakuterrarium.session.open_registry import live_store_for
from kohakuterrarium.session.store import iter_kv_keys
from kohakuterrarium.utils.fs_path import coerce_fs_path
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_TABLES = frozenset({"meta", "events", "state"})


def _text(key: Any) -> str:
    return key.decode("utf-8", errors="replace") if isinstance(key, bytes) else key


class SessionReadView:
    """One selectively decoded view of an existing session."""

    def __init__(self, path: str | Path) -> None:
        source = coerce_fs_path(path).expanduser().resolve(strict=True)
        self._live = live_store_for(source)
        self._db = None
        self._decoder = None
        if self._live is not None:
            if not self._live.is_ready:
                self._live = None
                raise SessionNotReadyError(f"Session is still opening: {source}")
            return
        self._db = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        try:
            self._db.execute("BEGIN")
            self._tables = {
                row[0]
                for row in self._db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self._decoder = KVault(":memory:", enable_wal=False, cache_kb=1024)
            self._decoder.enable_auto_pack()
        except BaseException:
            self.close()
            raise

    def _decode(self, value: bytes) -> Any:
        # Bytes are stored verbatim by auto-pack; reading invokes the native
        # header/encoding decoder. No source-file handle reaches KohakuVault.
        self._decoder[b"value"] = value
        return self._decoder[b"value"]

    def get(self, table: str, key: str, default: Any = None) -> Any:
        if table not in _TABLES:
            raise ValueError(f"unsupported session table: {table}")
        if self._live is not None:
            return self._live_get(table, key, default)
        if table not in self._tables:
            return default
        row = self._db.execute(
            f'SELECT value FROM "{table}" WHERE key = ?', (key.encode(),)
        ).fetchone()
        if row is not None:
            try:
                return self._decode(row[0])
            except Exception as exc:
                logger.warning(
                    "Unreadable session value", table=table, key=key, error=str(exc)
                )
        return default

    def items(self, table: str, *, prefix: str = "") -> Iterator[tuple[str, Any]]:
        if table not in _TABLES:
            raise ValueError(f"unsupported session table: {table}")
        if self._live is not None:
            yield from self._live_items(table, prefix)
            return
        if table not in self._tables:
            return
        lower = prefix.encode()
        with closing(
            self._db.execute(
                f'SELECT key, value FROM "{table}" WHERE key >= ? AND key < ? ORDER BY key',
                (lower, lower + b"\xff"),
            )
        ) as rows:
            for key, value in rows:
                name = key.decode("utf-8", errors="replace")
                try:
                    decoded = self._decode(value)
                except Exception as exc:
                    logger.warning(
                        "Unreadable session value",
                        table=table,
                        key=name,
                        error=str(exc),
                    )
                    continue
                yield name, decoded

    def load_meta(self, *, discover_agents: bool = True) -> dict[str, Any]:
        meta = dict(self.items("meta"))
        known = list(meta.get("agents") or [])
        if discover_agents:
            for raw_key in self._event_keys():
                parts = raw_key.rsplit(":e", 1)
                if len(parts) != 2:
                    continue
                agent = parts[0]
                if (
                    agent != "terrarium"
                    and ":attached:" not in agent
                    and agent not in known
                ):
                    known.append(agent)
        meta["agents"] = known
        return meta

    def _event_keys(self) -> Iterator[str]:
        """Event keys in order; namespace discovery reads keys, never payloads."""
        if self._live is not None:
            yield from self._live_keys("events", "")
            return
        if "events" not in self._tables:
            return
        with closing(self._db.execute("SELECT key FROM events ORDER BY key")) as rows:
            for (raw_key,) in rows:
                yield raw_key.decode("utf-8", errors="replace")

    def _live_keys(self, table: str, prefix: str) -> list[str]:
        vault = getattr(self._live, table)
        vault.flush_cache()
        return sorted(_text(key) for key in iter_kv_keys(vault, prefix=prefix))

    def _live_get(self, table: str, key: str, default: Any) -> Any:
        try:
            return getattr(self._live, table)[key]
        except KeyError:
            return default
        except Exception as exc:
            logger.warning(
                "Unreadable session value", table=table, key=key, error=str(exc)
            )
            return default

    def _live_items(self, table: str, prefix: str) -> Iterator[tuple[str, Any]]:
        for name in self._live_keys(table, prefix):
            try:
                yield name, getattr(self._live, table)[name]
            except Exception as exc:
                logger.warning(
                    "Unreadable session value", table=table, key=name, error=str(exc)
                )

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None
        if self._decoder is not None:
            self._decoder.close()
            self._decoder = None
        self._live = None

    def __enter__(self) -> "SessionReadView":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
