"""Selective, read-only session queries without writable source vault handles.

SQLite owns the read transaction, including committed WAL rows. KohakuVault
only decodes selected values in an isolated in-memory vault, keeping its
binary-format handling upstream and its initialization writes off the source.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

from kohakuvault import KVault

from kohakuterrarium.utils.fs_path import coerce_fs_path
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_TABLES = frozenset({"meta", "events", "state"})


class SessionReadView:
    """One consistent, selectively decoded snapshot of an existing session."""

    def __init__(self, path: str | Path) -> None:
        source = coerce_fs_path(path).expanduser().resolve(strict=True)
        self._db = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
        self._decoder = None
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
        if discover_agents and "events" in self._tables:
            # Namespace discovery reads keys only, never the event payloads.
            with closing(
                self._db.execute("SELECT key FROM events ORDER BY key")
            ) as rows:
                for (raw_key,) in rows:
                    parts = raw_key.decode("utf-8", errors="replace").rsplit(":e", 1)
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

    def close(self) -> None:
        self._db.close()
        if self._decoder is not None:
            self._decoder.close()
            self._decoder = None

    def __enter__(self) -> "SessionReadView":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
