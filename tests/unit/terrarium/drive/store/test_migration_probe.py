"""Drive source occupancy around the immutable legacy probe."""

from __future__ import annotations

import sqlite3
import threading

import pytest

import kohakuterrarium.terrarium.drive.store.migration as store_migration
from kohakuterrarium.session.occupancy.gate import (
    GATE,
    REASON_LEGACY_ENROLLED,
    REASON_LEGACY_PENDING,
    REASON_LEGACY_RELEASE_UNCERTAIN as UNCERTAIN,
    ResourceReleaseError,
    acquire_legacy,
    try_fast,
)
from kohakuterrarium.terrarium.drive.store.migration import _legacy_same_file_drives
from tests.unit.terrarium.drive.store_migration_resource_fixtures import (
    BoomCursorList,
    CursorAndDbCloseFailsConnection,
    CursorCloseFailsConnection,
    FailExecuteConnection,
    FailingCloseConnection,
    _REAL_CONNECT,
    assert_idle,
    connect_with_factory,
    force_close,
    legacy_db,
    source_connect,
    wait_pending,
)


class TestProbeEnrollment:
    def test_enrolls_legacy_before_connect_and_releases(self, tmp_path, monkeypatch):
        path = legacy_db(tmp_path)
        order: list[object] = []
        real_acquire = store_migration.acquire_legacy

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
            assert source_connect(target, path)
            return _REAL_CONNECT(target, *args, **kwargs)

        monkeypatch.setattr(store_migration, "acquire_legacy", spy_acquire)
        monkeypatch.setattr(store_migration.sqlite3, "connect", spy_connect)

        assert _legacy_same_file_drives(path) is True
        assert order[0] == ("acquire", "drive_probe")
        assert order.index(("acquire", "drive_probe")) < order.index("connect")
        assert_idle(path)

    def test_missing_file_does_not_enroll(self, tmp_path):
        path = str(tmp_path / "absent.kohakutr")
        assert _legacy_same_file_drives(path) is False
        assert GATE.snapshot(path).registered is False

    def test_query_error_returns_false_and_releases(self, tmp_path, monkeypatch):
        path = legacy_db(tmp_path, "query.kohakutr")
        connect_with_factory(monkeypatch, FailExecuteConnection, store_migration)
        assert _legacy_same_file_drives(path) is False
        assert_idle(path)

    def test_new_occupant_blocks_connect_until_released(self, tmp_path, monkeypatch):
        path = legacy_db(tmp_path, "wait.kohakutr")
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None
        opened: list[object] = []
        errors: list[BaseException] = []

        def spy_connect(target, *args, **kwargs):
            opened.append(target)
            return _REAL_CONNECT(target, *args, **kwargs)

        monkeypatch.setattr(store_migration.sqlite3, "connect", spy_connect)
        done = threading.Event()

        def run_probe():
            try:
                assert _legacy_same_file_drives(path) is True
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run_probe)
        worker.start()
        try:
            wait_pending(path, 1)
            assert opened == []
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_PENDING
            occupying.lease.finish(source_released=True)
            assert done.wait(5) and not errors
            assert opened
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(5)
        assert not worker.is_alive() and not errors
        assert_idle(path)

    def test_new_fault_blocks_before_connect(self, tmp_path, monkeypatch):
        path = legacy_db(tmp_path, "new-fault.kohakutr")
        admitted = try_fast(path, eligible=True)
        assert admitted.lease is not None
        admitted.lease.finish(source_released=False)
        opened: list[object] = []

        def boom_connect(*args, **kwargs):
            opened.append(args)
            raise AssertionError("must not open SQL after NEW fault")

        monkeypatch.setattr(store_migration.sqlite3, "connect", boom_connect)
        with pytest.raises(ResourceReleaseError):
            _legacy_same_file_drives(path)
        assert opened == []
        with pytest.raises(ResourceReleaseError):
            try_fast(path, eligible=True)

    def test_direct_call_enrolls_when_legacy_already_active(self, tmp_path):
        path = legacy_db(tmp_path, "nested.kohakutr")
        outer = acquire_legacy(path, "outer")
        try:
            assert GATE.snapshot(path).legacy_active == 1
            assert _legacy_same_file_drives(path) is True
            snap = GATE.snapshot(path)
            assert snap.legacy_active == 1
            assert snap.legacy_uncertain is False
            assert try_fast(path, eligible=True).reason == REASON_LEGACY_ENROLLED
        finally:
            outer.finish(source_released=True)
        assert_idle(path)


class TestProbeFaults:
    def test_connect_failure_is_conservatively_uncertain(self, tmp_path, monkeypatch):
        path = legacy_db(tmp_path, "connect-fail.kohakutr")
        monkeypatch.setattr(
            store_migration.sqlite3,
            "connect",
            lambda *a, **k: (_ for _ in ()).throw(
                sqlite3.OperationalError("connect failed without a handle")
            ),
        )
        assert _legacy_same_file_drives(path) is False
        assert try_fast(path, eligible=True).reason == UNCERTAIN

    def test_db_close_failure_does_not_vanish_into_false(self, tmp_path, monkeypatch):
        path = legacy_db(tmp_path, "db-close.kohakutr")
        FailingCloseConnection.instances = []
        connect_with_factory(monkeypatch, FailingCloseConnection, store_migration)
        try:
            with pytest.raises(RuntimeError, match="db close failed"):
                _legacy_same_file_drives(path)
            assert try_fast(path, eligible=True).reason == UNCERTAIN
        finally:
            force_close(FailingCloseConnection.instances)

    def test_cursor_close_failure_still_closes_db_and_disables_new(
        self, tmp_path, monkeypatch
    ):
        path = legacy_db(tmp_path, "cursor-close.kohakutr")
        CursorCloseFailsConnection.events = []
        connect_with_factory(monkeypatch, CursorCloseFailsConnection, store_migration)
        with pytest.raises(RuntimeError, match="cursor close failed"):
            _legacy_same_file_drives(path)
        assert "db.close" in CursorCloseFailsConnection.events
        assert try_fast(path, eligible=True).reason == UNCERTAIN

    def test_cursor_and_db_close_failure_preserves_primary(self, tmp_path, monkeypatch):
        path = legacy_db(tmp_path, "both-close.kohakutr")
        CursorAndDbCloseFailsConnection.instances = []
        connect_with_factory(
            monkeypatch, CursorAndDbCloseFailsConnection, store_migration
        )
        try:
            with pytest.raises(RuntimeError, match="cursor close failed") as caught:
                _legacy_same_file_drives(path)
            assert "cursor close failed" in str(caught.value.__cause__)
            assert "db close failed" in str(caught.value.__cause__.__cause__)
            assert try_fast(path, eligible=True).reason == UNCERTAIN
        finally:
            force_close(CursorAndDbCloseFailsConnection.instances)

    def test_tracking_failure_after_execute_keeps_source_uncertain(
        self, tmp_path, monkeypatch
    ):
        path = legacy_db(tmp_path, "track-fail.kohakutr")
        monkeypatch.setattr(store_migration, "new_cursors", BoomCursorList)
        with pytest.raises(RuntimeError, match="cannot track cursor"):
            _legacy_same_file_drives(path)
        assert try_fast(path, eligible=True).reason == UNCERTAIN

    def test_query_error_then_close_failure_propagates_close(
        self, tmp_path, monkeypatch
    ):
        path = legacy_db(tmp_path, "query-then-close.kohakutr")
        FailingCloseConnection.instances = []

        class FailQueryThenClose(FailingCloseConnection):
            def execute(self, *args, **kwargs):
                raise sqlite3.OperationalError("query failed")

        connect_with_factory(monkeypatch, FailQueryThenClose, store_migration)
        try:
            with pytest.raises(RuntimeError, match="db close failed"):
                _legacy_same_file_drives(path)
            assert try_fast(path, eligible=True).reason == UNCERTAIN
        finally:
            force_close(FailingCloseConnection.instances)
