"""Fork drive-table purge occupancy around the destination SQLite file.

The helper is exercised against an offline SQLite fixture so these tests never
open KohakuVault / SessionStore on the same file.
"""

import sqlite3
import threading
import time

import pytest

import kohakuterrarium.session.store.fork as store_fork
from kohakuterrarium.session.occupancy.gate import (
    GATE,
    REASON_LEGACY_ENROLLED,
    REASON_LEGACY_PENDING,
    REASON_LEGACY_RELEASE_UNCERTAIN as UNCERTAIN,
    REASON_OK,
    ResourceReleaseError,
    acquire_legacy,
    try_fast,
)
from kohakuterrarium.session.store.fork import _purge_drive_tables

_REAL_CONNECT = sqlite3.connect


def _offline_db(tmp_path, name: str = "child.sqlite") -> str:
    path = tmp_path / name
    conn = _REAL_CONNECT(path)
    conn.execute("CREATE TABLE drives(drive_id TEXT)")
    conn.execute("CREATE TABLE drive_audit(seq INTEGER)")
    conn.execute("CREATE TABLE meta(k TEXT)")
    conn.execute("INSERT INTO drives VALUES('d1')")
    conn.commit()
    conn.close()
    return str(path)


def _table_names(path: str) -> set[str]:
    conn = _REAL_CONNECT(path)
    try:
        return {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()


def _wait_pending(path, count: int = 1, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if GATE.snapshot(path).legacy_pending == count:
            return
        time.sleep(0.01)
    raise AssertionError(f"pending occupancy did not reach {count}")


def _assert_idle(path) -> None:
    admitted = try_fast(path, eligible=True)
    assert admitted.reason == REASON_OK
    assert admitted.lease is not None
    admitted.lease.finish(source_released=True)


def _connect_with_factory(monkeypatch, factory):
    def connect(target, *args, **kwargs):
        kwargs["factory"] = factory
        return _REAL_CONNECT(target, *args, **kwargs)

    monkeypatch.setattr(store_fork.sqlite3, "connect", connect)


class _BoomCursorList(list):
    def append(self, item):
        raise RuntimeError("cannot track cursor")


def _fail_cursor_append(monkeypatch) -> None:
    monkeypatch.setattr(store_fork, "_new_purge_cursors", _BoomCursorList)


def _force_close_failing() -> None:
    for conn in list(_FailingCloseConnection.instances):
        try:
            conn.force_close()
        except Exception:
            pass
    _FailingCloseConnection.instances.clear()


class _FailingCloseConnection(sqlite3.Connection):
    instances: list[sqlite3.Connection] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type(self).instances.append(self)

    def close(self):
        raise RuntimeError("db close failed")

    def force_close(self):
        super().close()


class _TrackingConnection(sqlite3.Connection):
    events: list = []
    path: str | None = None

    def execute(self, *args, **kwargs):
        type(self).events.append(("execute", args[0] if args else None))
        return super().cursor(_TrackingCursor).execute(*args, **kwargs)

    def commit(self):
        type(self).events.append("commit")
        if self.path is not None:
            snap = GATE.snapshot(self.path)
            assert snap.legacy_active >= 1
            assert snap.legacy_pending == 0
        return super().commit()

    def close(self):
        type(self).events.append("db.close")
        return super().close()


class _TrackingCursor(sqlite3.Cursor):
    def close(self):
        _TrackingConnection.events.append("cursor.close")
        return super().close()


class _FailExecuteConnection(sqlite3.Connection):
    def execute(self, *args, **kwargs):
        raise sqlite3.OperationalError("query failed")


class _FailCursorClose(sqlite3.Cursor):
    def close(self):
        raise RuntimeError("cursor close failed")


class _CursorCloseFailsConnection(sqlite3.Connection):
    events: list = []

    def execute(self, *args, **kwargs):
        return super().cursor(_FailCursorClose).execute(*args, **kwargs)

    def close(self):
        type(self).events.append("db.close")
        return super().close()


class _CursorAndDbCloseFailsConnection(sqlite3.Connection):
    instances: list[sqlite3.Connection] = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type(self).instances.append(self)

    def execute(self, *args, **kwargs):
        return super().cursor(_FailCursorClose).execute(*args, **kwargs)

    def close(self):
        raise RuntimeError("db close failed")

    def force_close(self):
        super().close()


class _OrphanProgrammingClose(sqlite3.Cursor):
    def close(self):
        raise sqlite3.ProgrammingError("orphan close failed")


class _OrphanSqlClose(sqlite3.Cursor):
    def close(self):
        raise sqlite3.OperationalError("orphan close failed")


class TestForkDrivePurgeBarriers:
    def test_enrolls_legacy_before_connect_and_releases_after_close(
        self, tmp_path, monkeypatch
    ):
        path = _offline_db(tmp_path)
        order: list[object] = []
        real_acquire = store_fork.acquire_legacy

        def spy_acquire(target, operation):
            order.append(("acquire", operation))
            lease = real_acquire(target, operation)
            snap = GATE.snapshot(path)
            assert snap.legacy_active == 1
            assert snap.legacy_pending == 0
            denied = try_fast(path, eligible=True)
            assert denied.lease is None
            assert denied.reason == REASON_LEGACY_ENROLLED
            return lease

        def spy_connect(target, *args, **kwargs):
            order.append("connect")
            assert GATE.snapshot(path).legacy_active == 1
            return _REAL_CONNECT(target, *args, **kwargs)

        monkeypatch.setattr(store_fork, "acquire_legacy", spy_acquire)
        monkeypatch.setattr(store_fork.sqlite3, "connect", spy_connect)

        _purge_drive_tables(path)

        assert order[0] == ("acquire", "fork_drive_purge")
        assert "connect" in order
        assert order.index(("acquire", "fork_drive_purge")) < order.index("connect")
        names = _table_names(path)
        assert "drives" not in names
        assert "drive_audit" not in names
        assert "meta" in names
        _assert_idle(path)

    def test_new_occupant_blocks_connect_until_released(self, tmp_path, monkeypatch):
        path = _offline_db(tmp_path, "wait.sqlite")
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None
        opened: list[object] = []
        errors: list[BaseException] = []

        def spy_connect(target, *args, **kwargs):
            opened.append(target)
            return _REAL_CONNECT(target, *args, **kwargs)

        monkeypatch.setattr(store_fork.sqlite3, "connect", spy_connect)
        done = threading.Event()

        def run_purge():
            try:
                _purge_drive_tables(path)
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_purge)
        worker.start()
        try:
            _wait_pending(path, 1)
            assert opened == []
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_PENDING
            occupying.lease.finish(source_released=True)
            assert done.wait(5) and not errors
            assert opened
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(5)
        assert not worker.is_alive() and not errors
        _assert_idle(path)

    def test_new_fault_blocks_before_connect(self, tmp_path, monkeypatch):
        path = _offline_db(tmp_path, "new-fault.sqlite")
        admitted = try_fast(path, eligible=True)
        assert admitted.lease is not None
        admitted.lease.finish(source_released=False)
        opened: list[object] = []

        def boom_connect(*args, **kwargs):
            opened.append(args)
            raise AssertionError("must not open SQL after NEW fault")

        monkeypatch.setattr(store_fork.sqlite3, "connect", boom_connect)
        with pytest.raises(ResourceReleaseError):
            _purge_drive_tables(path)
        assert opened == []
        with pytest.raises(ResourceReleaseError):
            try_fast(path, eligible=True)

    def test_helper_enrolls_its_own_scope_when_legacy_already_active(self, tmp_path):
        path = _offline_db(tmp_path, "nested.sqlite")
        outer = acquire_legacy(path, "outer")
        try:
            assert GATE.snapshot(path).legacy_active == 1
            _purge_drive_tables(path)
            snap = GATE.snapshot(path)
            assert snap.legacy_active == 1
            assert snap.legacy_uncertain is False
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_ENROLLED
        finally:
            outer.finish(source_released=True)
        _assert_idle(path)


class TestForkDrivePurgeCloseOrder:
    def test_cursor_close_precedes_db_close(self, tmp_path, monkeypatch):
        path = _offline_db(tmp_path, "order.sqlite")
        _TrackingConnection.events = []
        _TrackingConnection.path = path
        _connect_with_factory(monkeypatch, _TrackingConnection)

        _purge_drive_tables(path)

        kinds = [
            item[0] if isinstance(item, tuple) else item
            for item in _TrackingConnection.events
        ]
        assert kinds.index("cursor.close") < kinds.index("db.close")
        close_events = [
            item
            for item in _TrackingConnection.events
            if item in ("cursor.close", "db.close")
        ]
        assert close_events[-1] == "db.close"
        assert "cursor.close" in close_events
        assert "commit" in _TrackingConnection.events
        assert _TrackingConnection.events.index(
            "commit"
        ) < _TrackingConnection.events.index("db.close")
        _assert_idle(path)

    def test_commit_does_not_release_occupancy(self, tmp_path, monkeypatch):
        path = _offline_db(tmp_path, "commit.sqlite")
        _TrackingConnection.events = []
        _TrackingConnection.path = path
        _connect_with_factory(monkeypatch, _TrackingConnection)
        _purge_drive_tables(path)
        assert "commit" in _TrackingConnection.events
        assert "db.close" in _TrackingConnection.events
        _assert_idle(path)


class TestForkDrivePurgeFaults:
    def test_connect_failure_is_conservatively_uncertain(self, tmp_path, monkeypatch):
        path = _offline_db(tmp_path, "connect-fail.sqlite")
        monkeypatch.setattr(
            store_fork.sqlite3,
            "connect",
            lambda *a, **k: (_ for _ in ()).throw(
                sqlite3.OperationalError("connect failed without a handle")
            ),
        )
        with pytest.raises(sqlite3.OperationalError, match="connect failed"):
            _purge_drive_tables(path)
        assert try_fast(path, eligible=True).reason == UNCERTAIN

    def test_execute_error_releases_when_close_succeeds(self, tmp_path, monkeypatch):
        path = _offline_db(tmp_path, "execute-fail.sqlite")
        enrolled: list[int] = []

        class Recording(_FailExecuteConnection):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                enrolled.append(GATE.snapshot(path).legacy_active)

        _connect_with_factory(monkeypatch, Recording)
        _purge_drive_tables(path)
        assert enrolled == [1]
        assert "drives" in _table_names(path)
        _assert_idle(path)

    def test_db_close_failure_disables_new(self, tmp_path, monkeypatch):
        path = _offline_db(tmp_path, "db-close.sqlite")
        _FailingCloseConnection.instances = []
        _connect_with_factory(monkeypatch, _FailingCloseConnection)
        try:
            with pytest.raises(RuntimeError, match="db close failed"):
                _purge_drive_tables(path)
            assert try_fast(path, eligible=True).reason == UNCERTAIN
        finally:
            _force_close_failing()

    def test_cursor_close_failure_still_closes_db_and_disables_new(
        self, tmp_path, monkeypatch
    ):
        path = _offline_db(tmp_path, "cursor-close.sqlite")
        _CursorCloseFailsConnection.events = []
        _connect_with_factory(monkeypatch, _CursorCloseFailsConnection)
        with pytest.raises(RuntimeError, match="cursor close failed"):
            _purge_drive_tables(path)
        assert "db.close" in _CursorCloseFailsConnection.events
        assert try_fast(path, eligible=True).reason == UNCERTAIN

    def test_cursor_and_db_close_failure_preserves_primary(self, tmp_path, monkeypatch):
        path = _offline_db(tmp_path, "both-close.sqlite")
        _CursorAndDbCloseFailsConnection.instances = []
        _connect_with_factory(monkeypatch, _CursorAndDbCloseFailsConnection)
        try:
            with pytest.raises(RuntimeError, match="cursor close failed") as caught:
                _purge_drive_tables(path)
            assert "db close failed" in str(caught.value.__cause__)
            assert try_fast(path, eligible=True).reason == UNCERTAIN
        finally:
            for conn in list(_CursorAndDbCloseFailsConnection.instances):
                try:
                    conn.force_close()
                except Exception:
                    pass
            _CursorAndDbCloseFailsConnection.instances.clear()

    def test_execute_error_then_close_failure_propagates_close(
        self, tmp_path, monkeypatch
    ):
        path = _offline_db(tmp_path, "query-then-close.sqlite")
        _FailingCloseConnection.instances = []

        class FailQueryThenClose(_FailingCloseConnection):
            def execute(self, *args, **kwargs):
                raise sqlite3.OperationalError("query failed")

        _connect_with_factory(monkeypatch, FailQueryThenClose)
        try:
            with pytest.raises(RuntimeError, match="db close failed"):
                _purge_drive_tables(path)
            assert try_fast(path, eligible=True).reason == UNCERTAIN
        finally:
            _force_close_failing()

    def test_tracking_failure_after_execute_keeps_source_uncertain(
        self, tmp_path, monkeypatch
    ):
        path = _offline_db(tmp_path, "track-fail.sqlite")

        class Factory(sqlite3.Connection):
            def execute(self, *args, **kwargs):
                return super().cursor(_OrphanProgrammingClose).execute(*args, **kwargs)

        _connect_with_factory(monkeypatch, Factory)
        _fail_cursor_append(monkeypatch)
        with pytest.raises(RuntimeError, match="cannot track cursor"):
            _purge_drive_tables(path)
        assert try_fast(path, eligible=True).reason == UNCERTAIN

    def test_tracking_failure_then_close_failure_preserves_primary(
        self, tmp_path, monkeypatch
    ):
        path = _offline_db(tmp_path, "track-then-close.sqlite")
        _FailingCloseConnection.instances = []
        _connect_with_factory(monkeypatch, _FailingCloseConnection)
        _fail_cursor_append(monkeypatch)
        try:
            with pytest.raises(RuntimeError, match="cannot track cursor") as caught:
                _purge_drive_tables(path)
            assert "db close failed" in str(caught.value.__cause__)
            assert try_fast(path, eligible=True).reason == UNCERTAIN
        finally:
            _force_close_failing()

    def test_keyboard_interrupt_then_close_failure_preserves_primary(
        self, tmp_path, monkeypatch
    ):
        path = _offline_db(tmp_path, "interrupt-close.sqlite")
        _FailingCloseConnection.instances = []

        class InterruptThenClose(_FailingCloseConnection):
            def execute(self, *args, **kwargs):
                raise KeyboardInterrupt()

        _connect_with_factory(monkeypatch, InterruptThenClose)
        try:
            with pytest.raises(KeyboardInterrupt) as caught:
                _purge_drive_tables(path)
            assert caught.value.__cause__ is not None
            assert "db close failed" in str(caught.value.__cause__)
            assert try_fast(path, eligible=True).reason == UNCERTAIN
        finally:
            _force_close_failing()

    def test_orphan_close_sql_error_not_swallowed_as_query_error(
        self, tmp_path, monkeypatch
    ):
        path = _offline_db(tmp_path, "orphan-sql.sqlite")

        class Factory(sqlite3.Connection):
            def execute(self, *args, **kwargs):
                return super().cursor(_OrphanSqlClose).execute(*args, **kwargs)

        _connect_with_factory(monkeypatch, Factory)
        _fail_cursor_append(monkeypatch)
        with pytest.raises(RuntimeError, match="cannot track cursor") as caught:
            _purge_drive_tables(path)
        assert caught.value.__cause__ is not None
        assert "orphan close failed" in str(caught.value.__cause__)
        assert try_fast(path, eligible=True).reason == UNCERTAIN
