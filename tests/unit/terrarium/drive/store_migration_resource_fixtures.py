"""Shared offline SQLite fixtures for Drive source occupancy tests.

Offline SQLite fixtures only: these tests never open SessionStore / KohakuVault
on the same file as CPython sqlite3.
"""

from __future__ import annotations

import sqlite3
import time

from kohakuterrarium.session.occupancy.gate import (
    GATE,
    REASON_OK,
    REASON_LEGACY_ENROLLED,
    try_fast,
)
from kohakuterrarium.terrarium.drive.store.migration import (
    DRIVE_SCHEMA_VERSION,
    _SCHEMA,
)

_REAL_CONNECT = sqlite3.connect


def legacy_db(
    tmp_path,
    name: str = "legacy.kohakutr",
    *,
    drive_count: int = 2,
    audit_count: int = 1,
) -> str:
    path = tmp_path / name
    conn = _REAL_CONNECT(path)
    conn.execute("CREATE TABLE drives (drive_id TEXT PRIMARY KEY, blob TEXT NOT NULL)")
    conn.execute(
        "CREATE TABLE drive_audit ("
        "seq INTEGER PRIMARY KEY AUTOINCREMENT, "
        "drive_id TEXT NOT NULL, blob TEXT NOT NULL)"
    )
    conn.execute("CREATE TABLE meta (k TEXT)")
    for index in range(drive_count):
        conn.execute(
            "INSERT INTO drives VALUES (?, ?)",
            (f"d{index}", f'{{"title":"t{index}"}}'),
        )
    for index in range(audit_count):
        conn.execute(
            "INSERT INTO drive_audit(drive_id, blob) VALUES (?, ?)",
            ("d0", f'{{"seq":{index}}}'),
        )
    conn.commit()
    conn.close()
    return str(path)


def empty_session(tmp_path, name: str = "empty.kohakutr") -> str:
    path = tmp_path / name
    conn = _REAL_CONNECT(path)
    conn.execute("CREATE TABLE meta (k TEXT)")
    conn.commit()
    conn.close()
    return str(path)


def complete_sidecar(session_path: str) -> str:
    sidecar = session_path + ".drives"
    conn = _REAL_CONNECT(sidecar)
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO drive_meta(key, value) VALUES('schema_version', ?)",
        (str(DRIVE_SCHEMA_VERSION),),
    )
    conn.commit()
    conn.close()
    return sidecar


def sidecar(session_path: str) -> str:
    return session_path + ".drives"


def counts(path: str, table: str) -> list[tuple]:
    conn = _REAL_CONNECT(path)
    try:
        return list(conn.execute(f"SELECT * FROM {table} ORDER BY 1"))
    finally:
        conn.close()


def wait_pending(path, count: int = 1, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if GATE.snapshot(path).legacy_pending == count:
            return
        time.sleep(0.01)
    raise AssertionError(f"pending occupancy did not reach {count}")


def assert_idle(path) -> None:
    admitted = try_fast(path, eligible=True)
    assert admitted.reason == REASON_OK
    assert admitted.lease is not None
    admitted.lease.finish(source_released=True)


def connect_with_factory(monkeypatch, factory, module) -> None:
    def connect(target, *args, **kwargs):
        kwargs["factory"] = factory
        return _REAL_CONNECT(target, *args, **kwargs)

    monkeypatch.setattr(module.sqlite3, "connect", connect)


def source_connect(target, source: str) -> bool:
    text = str(target)
    return "immutable=1" in text and source.replace("\\", "/") in text.replace(
        "\\", "/"
    )


def force_close(instances) -> None:
    for conn in list(instances):
        try:
            conn.force_close()
        except Exception:
            pass
    instances.clear()


class BoomCursorList(list):
    def append(self, item):
        raise RuntimeError("cannot track cursor")


class FailingCloseConnection(sqlite3.Connection):
    instances: list[sqlite3.Connection] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type(self).instances.append(self)

    def close(self):
        raise RuntimeError("db close failed")

    def force_close(self):
        super().close()


class TrackingConnection(sqlite3.Connection):
    events: list = []
    source: str | None = None

    def execute(self, *args, **kwargs):
        sql = args[0] if args else None
        type(self).events.append(("execute", sql, args[1] if len(args) > 1 else ()))
        if isinstance(sql, str) and "ATTACH" in sql.upper() and self.source:
            snap = GATE.snapshot(self.source)
            assert snap.legacy_active >= 1
            assert snap.legacy_pending == 0
            denied = try_fast(self.source, eligible=True)
            assert denied.reason == REASON_LEGACY_ENROLLED
        return super().cursor(TrackingCursor).execute(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        type(self).events.append(("executescript", args[0] if args else None))
        return super().cursor(TrackingCursor).executescript(*args, **kwargs)

    def close(self):
        type(self).events.append("db.close")
        return super().close()


class TrackingCursor(sqlite3.Cursor):
    def close(self):
        TrackingConnection.events.append("cursor.close")
        return super().close()


class FailExecuteConnection(sqlite3.Connection):
    def execute(self, *args, **kwargs):
        raise sqlite3.OperationalError("query failed")


class FailCursorClose(sqlite3.Cursor):
    def close(self):
        raise RuntimeError("cursor close failed")


class CursorCloseFailsConnection(sqlite3.Connection):
    events: list = []

    def execute(self, *args, **kwargs):
        return super().cursor(FailCursorClose).execute(*args, **kwargs)

    def close(self):
        type(self).events.append("db.close")
        return super().close()


class CursorAndDbCloseFailsConnection(sqlite3.Connection):
    instances: list[sqlite3.Connection] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type(self).instances.append(self)

    def execute(self, *args, **kwargs):
        return super().cursor(FailCursorClose).execute(*args, **kwargs)

    def close(self):
        raise RuntimeError("db close failed")

    def force_close(self):
        super().close()


class SchemaThenCloseFails(FailingCloseConnection):
    def executescript(self, *args, **kwargs):
        raise RuntimeError("schema failed")


class CommitThenCloseFails(FailingCloseConnection):
    def execute(self, *args, **kwargs):
        sql = args[0] if args else ""
        if isinstance(sql, str) and sql.strip().upper() == "COMMIT":
            raise sqlite3.OperationalError("commit failed")
        return super().execute(*args, **kwargs)
