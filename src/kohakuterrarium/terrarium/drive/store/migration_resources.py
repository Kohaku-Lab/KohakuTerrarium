"""Source occupancy and SQL cleanup for legacy Drive migration."""

import sqlite3

from kohakuterrarium.session.occupancy.gate import ResourceLease


def new_cursors() -> list[sqlite3.Cursor]:
    """Return a fresh cursor ledger."""
    return []


def append_cleanup(primary: BaseException, cleanup: BaseException) -> None:
    """Append cleanup evidence without replacing an existing exception cause."""
    chain = []
    current = primary
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__
    incoming = []
    current = cleanup
    while current is not None and current not in incoming:
        if current in chain:
            return
        incoming.append(current)
        current = current.__cause__
    chain[-1].__cause__ = cleanup


class MigrationSqlResources:
    """Retain source occupancy until tracked source handles are disposed."""

    def __init__(self, lease: ResourceLease, *, cursors=None) -> None:
        self._lease = lease
        self._conn: sqlite3.Connection | None = None
        self._cursors = new_cursors() if cursors is None else cursors
        self._source_handle = False
        self._source_uncertain = False
        self._attached = False
        self._detached = False
        self._cursors_closed = False
        self._dest_closed = False
        self._finished = False
        self.tracking_failed = False

    def mark_source_handle(self) -> None:
        """Record an attempted connection to the source file itself."""
        self._source_handle = True

    def mark_source_uncertain(self) -> None:
        """Retain source uncertainty even after later successful cleanup."""
        self._source_uncertain = True

    def adopt(self, conn: sqlite3.Connection) -> None:
        """Retain a returned destination or source connection."""
        self._conn = conn

    def retain_cursor(self, cursor: sqlite3.Cursor) -> sqlite3.Cursor:
        """Track a cursor and attempt orphan cleanup if tracking fails."""
        try:
            self._cursors.append(cursor)
        except BaseException as primary:
            self.tracking_failed = True
            if self._source_handle or self._attached:
                self._source_uncertain = True
            try:
                cursor.close()
            except BaseException as cleanup:
                append_cleanup(primary, cleanup)
            raise
        return cursor

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute SQL and retain its cursor."""
        return self.retain_cursor(self._conn.execute(sql, params))

    def executescript(self, sql: str) -> sqlite3.Cursor:
        """Execute a schema script and retain its cursor."""
        return self.retain_cursor(self._conn.executescript(sql))

    def attach_legacy(self, source_uri: str) -> None:
        """Register the source attachment attempt before invoking SQLite."""
        self._attached = True
        self.execute("ATTACH DATABASE ? AS legacy", (source_uri,))

    def detach_legacy(self) -> None:
        """Record successful source detachment after executing DETACH."""
        self.execute("DETACH DATABASE legacy")
        self._detached = True

    def close_sql(self) -> None:
        """Attempt every cursor close and connection close, retaining failures."""
        if self._conn is None:
            return
        primary = None
        self._cursors_closed = True
        try:
            for cursor in self._cursors:
                try:
                    cursor.close()
                except BaseException as exc:
                    self._cursors_closed = False
                    if primary is None:
                        primary = exc
                    else:
                        append_cleanup(primary, exc)
            self._cursors.clear()
        except BaseException as exc:
            self._cursors_closed = False
            if primary is None:
                primary = exc
            else:
                append_cleanup(primary, exc)
        finally:
            try:
                self._conn.close()
                self._dest_closed = True
            except BaseException as exc:
                if primary is None:
                    primary = exc
                else:
                    append_cleanup(primary, exc)
            finally:
                self._conn = None
        if primary is not None:
            raise primary

    def source_released(self) -> bool:
        """Report proof of source disposal independently of destination-only faults."""
        if self._source_uncertain:
            return False
        if not self._source_handle and not self._attached:
            return True
        return self._cursors_closed and (
            self._dest_closed or (self._attached and self._detached)
        )

    def finish(self, primary: BaseException | None = None) -> None:
        """Close SQL and release or quarantine the source lease exactly once."""
        if not self._finished:
            try:
                self.close_sql()
            except BaseException as cleanup:
                if primary is None:
                    primary = cleanup
                else:
                    append_cleanup(primary, cleanup)
            finally:
                self._finished = True
                self._lease.finish(source_released=self.source_released())
        if primary is not None:
            raise primary
