"""Drive source occupancy around outer migrate lock order."""

import asyncio
import sqlite3
import threading

import pytest

import kohakuterrarium.terrarium.drive.store.migration as store_migration
from kohakuterrarium.session.occupancy.gate import (
    GATE,
    REASON_LEGACY_PENDING,
    REASON_LEGACY_RELEASE_UNCERTAIN as UNCERTAIN,
    try_fast,
)
from kohakuterrarium.terrarium.drive.store.migration import (
    DRIVE_SCHEMA_VERSION,
    _legacy_same_file_drives,
    _migrate_same_file_drives,
)
from kohakuterrarium.utils.file.lock import FileLock
from tests.unit.terrarium.drive.store_migration_resource_fixtures import (
    CommitThenCloseFails,
    FailingCloseConnection,
    _REAL_CONNECT,
    assert_idle,
    complete_sidecar,
    counts,
    empty_session,
    force_close,
    legacy_db,
    sidecar,
    source_connect,
    wait_pending,
)


class TestMigrateLockOrder:
    def test_enrolls_before_migration_lock(self, tmp_path, monkeypatch):
        path = legacy_db(tmp_path, "lock-order.kohakutr")
        dest = sidecar(path)
        order: list[str] = []
        real_acquire = store_migration.acquire_legacy
        real_lock_acquire = FileLock.acquire

        def spy_acquire(target, operation):
            order.append(f"enroll:{operation}")
            return real_acquire(target, operation)

        def spy_lock(self):
            order.append("lock")
            assert GATE.snapshot(path).legacy_active >= 1
            return real_lock_acquire(self)

        monkeypatch.setattr(store_migration, "acquire_legacy", spy_acquire)
        monkeypatch.setattr(store_migration.FileLock, "acquire", spy_lock)

        _migrate_same_file_drives(path, dest)

        enroll_at = next(
            i for i, item in enumerate(order) if item.startswith("enroll:")
        )
        assert order[enroll_at] == "enroll:drive_migrate"
        assert "lock" in order
        assert enroll_at < order.index("lock")
        assert_idle(path)

    def test_new_first_waits_before_source_touch_and_lock(self, tmp_path, monkeypatch):
        path = legacy_db(tmp_path, "migrate-wait.kohakutr")
        dest = sidecar(path)
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None
        opened: list[object] = []
        locks: list[object] = []
        errors: list[BaseException] = []
        real_lock_acquire = FileLock.acquire

        def spy_connect(target, *args, **kwargs):
            opened.append(target)
            return _REAL_CONNECT(target, *args, **kwargs)

        def spy_lock(self):
            locks.append(self.path)
            return real_lock_acquire(self)

        monkeypatch.setattr(store_migration.sqlite3, "connect", spy_connect)
        monkeypatch.setattr(store_migration.FileLock, "acquire", spy_lock)
        done = threading.Event()

        def run_migrate():
            try:
                _migrate_same_file_drives(path, dest)
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_migrate)
        worker.start()
        try:
            wait_pending(path, 1)
            assert opened == []
            assert locks == []
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_PENDING
            occupying.lease.finish(source_released=True)
            assert done.wait(5) and not errors
            assert any(source_connect(target, path) for target in opened)
            assert locks
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(5)
        assert not worker.is_alive() and not errors
        assert_idle(path)

    def test_complete_sidecar_does_not_wait_on_new(self, tmp_path):
        path = empty_session(tmp_path, "complete.kohakutr")
        dest = complete_sidecar(path)
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None
        done = threading.Event()
        errors: list[BaseException] = []

        def run_migrate():
            try:
                _migrate_same_file_drives(path, dest)
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_migrate)
        worker.start()
        try:
            assert done.wait(2)
            assert not errors
            assert GATE.snapshot(path).new_active == 1
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(5)
        assert_idle(path)

    def test_outer_coordination_finish_does_not_clear_inner_uncertainty(
        self, tmp_path, monkeypatch
    ):
        path = legacy_db(tmp_path, "sticky.kohakutr")
        dest = sidecar(path)
        FailingCloseConnection.instances = []
        attached = []

        class FailingRebuild(CommitThenCloseFails):
            def execute(self, sql, params=()):
                if sql.startswith("ATTACH"):
                    attached.append(sql)
                return super().execute(sql, params)

        def connect(target, *args, **kwargs):
            if ".migrating." in str(target):
                kwargs["factory"] = FailingRebuild
            return _REAL_CONNECT(target, *args, **kwargs)

        monkeypatch.setattr(store_migration.sqlite3, "connect", connect)
        try:
            with pytest.raises(sqlite3.OperationalError, match="commit failed"):
                _migrate_same_file_drives(path, dest)
            assert attached
            snap = GATE.snapshot(path)
            assert snap.legacy_active == 0
            assert snap.legacy_uncertain is True
            assert try_fast(path, eligible=True).reason == UNCERTAIN
        finally:
            force_close(FailingCloseConnection.instances)

    async def test_canceled_worker_retains_occupancy(self, tmp_path, monkeypatch):
        path = legacy_db(tmp_path, "cancel.kohakutr")
        dest = sidecar(path)
        hold, entered, finished = (threading.Event() for _ in range(3))
        errors = []

        class PausedCopy(sqlite3.Connection):
            def execute(self, sql, params=()):
                cursor = super().execute(sql, params)
                if sql.startswith("ATTACH"):
                    entered.set()
                    assert hold.wait(10)
                return cursor

        def connect(target, *args, **kwargs):
            if ".migrating." in str(target):
                kwargs["factory"] = PausedCopy
            return _REAL_CONNECT(target, *args, **kwargs)

        def migrate():
            try:
                _migrate_same_file_drives(path, dest)
            except BaseException as exc:
                errors.append(exc)
            finally:
                finished.set()

        monkeypatch.setattr(store_migration.sqlite3, "connect", connect)
        waiter = asyncio.create_task(asyncio.to_thread(migrate))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not finished.is_set()
            assert try_fast(path, eligible=True).reason == "legacy_enrolled"
            assert GATE.snapshot(path).legacy_active == 2
        finally:
            hold.set()
            assert await asyncio.to_thread(finished.wait, 5)
        assert not errors
        assert counts(dest, "drives") == counts(path, "drives")
        assert_idle(path)


class TestRowParity:
    def test_successful_migration_copies_legacy_rows(self, tmp_path):
        path = legacy_db(tmp_path, "parity.kohakutr", drive_count=3, audit_count=2)
        dest = sidecar(path)
        source_drives = counts(path, "drives")
        source_audit = counts(path, "drive_audit")

        _migrate_same_file_drives(path, dest)

        assert counts(path, "drives") == source_drives
        assert counts(path, "drive_audit") == source_audit
        assert counts(dest, "drives") == source_drives
        assert counts(dest, "drive_audit") == source_audit
        conn = _REAL_CONNECT(dest)
        try:
            version = conn.execute(
                "SELECT value FROM drive_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert int(version) == DRIVE_SCHEMA_VERSION
        assert_idle(path)
        assert GATE.snapshot(dest).registered is False

    def test_probe_false_when_no_drive_rows(self, tmp_path):
        path = empty_session(tmp_path, "no-drives.kohakutr")
        assert _legacy_same_file_drives(path) is False
        assert_idle(path)
