"""Selective, read-only session queries through independent read-only handles.

Each table is opened read-only on first use (``vault_read.open_read_table``),
so a view never writes, never creates sidecars on a checkpointed file, and
never shares a live store's connections. A live store in this process has its
buffered events flushed first so the view observes them. Occupancy is
enrolled before any handle opens.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from kohakuvault import KVault

from kohakuterrarium.session.errors import SessionNotReadyError, SessionViewClosedError
from kohakuterrarium.session.occupancy.registry import live_store_for
from kohakuterrarium.session.occupancy.gate import ResourceLease, acquire_legacy
from kohakuterrarium.session.vault.handles import dispose
from kohakuterrarium.session.vault.io import key_text, read_prefix
from kohakuterrarium.session.vault.read import open_read_table
from kohakuterrarium.utils.file.path import coerce_fs_path
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

_TABLES = frozenset({"meta", "events", "state", "conversation"})
_MISSING = object()


class SessionReadView:
    """One selectively decoded view of an existing session."""

    def __init__(self, path: str | Path) -> None:
        self._source = coerce_fs_path(path).expanduser().resolve(strict=True)
        self._handles: dict[str, KVault | None] = {}
        self._closed = False
        self._resource_lease: ResourceLease | None = acquire_legacy(
            self._source, "read_view"
        )
        try:
            live = live_store_for(self._source)
            if live is not None:
                if not live.is_ready:
                    raise SessionNotReadyError(
                        f"Session is still opening: {self._source}"
                    )
                live.flush()
        except BaseException as primary:
            try:
                self.close()
            except BaseException as cleanup:
                raise primary from cleanup
            raise

    def _check_open(self) -> None:
        if self._closed:
            raise SessionViewClosedError(f"Session view is closed: {self._source}")

    def _table(self, table: str) -> KVault | None:
        if table not in _TABLES:
            raise ValueError(f"unsupported session table: {table}")
        self._check_open()
        if table not in self._handles:
            self._handles[table] = open_read_table(self._source, table, snapshot=True)
        return self._handles[table]

    def get(self, table: str, key: str, default: Any = None) -> Any:
        vault = self._table(table)
        if vault is None:
            return default
        try:
            value = vault.get(key, _MISSING)
        except Exception as exc:
            logger.warning(
                "Unreadable session value", table=table, key=key, error=str(exc)
            )
            return default
        return default if value is _MISSING else value

    def items(self, table: str, *, prefix: str = "") -> Iterator[tuple[str, Any]]:
        """Yield decoded rows in key order, skipping unreadable ones.

        A generator outliving ``close`` stops instead of reading further.
        """
        vault = self._table(table)
        if vault is None:
            return

        def unreadable(key: Any, exc: Exception) -> None:
            logger.warning(
                "Unreadable session value",
                table=table,
                key=key_text(key),
                error=str(exc),
            )

        rows = read_prefix(vault, prefix or None, on_unreadable=unreadable)
        while not self._closed:
            try:
                key, value = next(rows)
            except StopIteration:
                return
            except Exception:
                if self._closed:
                    return
                raise
            if self._closed:
                return
            yield key_text(key), value

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
        vault = self._table("events")
        if vault is None:
            return
        keys = vault.keys()
        while not self._closed:
            try:
                key = next(keys)
            except StopIteration:
                return
            except Exception:
                if self._closed:
                    return
                raise
            if self._closed:
                return
            yield key_text(key)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        released = True
        primary: BaseException | None = None
        for vault in self._handles.values():
            if vault is None:
                continue
            try:
                if not dispose(vault):
                    released = False
            except BaseException as exc:
                released = False
                if primary is None:
                    primary = exc
        self._handles.clear()
        lease, self._resource_lease = self._resource_lease, None
        if lease is not None:
            lease.finish(source_released=released)
        if primary is not None:
            raise primary

    def __enter__(self) -> "SessionReadView":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self.close()
        except BaseException as cleanup:
            if exc is not None:
                raise exc.with_traceback(tb) from cleanup
            raise
