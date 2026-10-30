"""Drive source occupancy around sidecar rebuild."""

from __future__ import annotations

import sqlite3
import threading

import pytest

import kohakuterrarium.terrarium.drive.store_migration as store_migration
from kohakuterrarium.session.occupancy.gate import (
    GATE,
    REASON_LEGACY_RELEASE_UNCERTAIN as UNCERTAIN,
    try_fast,
)
from kohakuterrarium.terrarium.drive.store_migration import (
    _rebuild_sidecar_from_legacy,
)
from tests.unit.terrarium.drive.store_migration_resource_fixtures import (
    BoomCursorList,
    CommitThenCloseFails,
    FailingCloseConnection,
    SchemaThenCloseFails,
    TrackingConnection,
    _REAL_CONNECT,
    assert_idle,
    connect_with_factory,
    force_close,
    legacy_db,
    sidecar,
    wait_pending,
)


class TestRebuildEnrollment:
    def test_enrolls_before_attach_and_closes_cursors_before_db(
        self, tmp_path, monkeypatch
    ):
        path = legacy_db(tmp_path, "rebuild.kohakutr")
        dest = sidecar(path)
        order: list[object] = []
        TrackingConnection.events = []
        TrackingConnection.source = path
        real_acquire = store_migration.acquire_legacy

        def spy_acquire(target, operation):
            order.append(("acquire", operation))
            lease = real_acquire(target, operation)
            assert GATE.snapshot(path).legacy_active == 1
            return lease

        def spy_connect(target, *args, **kwargs):
            order.append("connect")
            kwargs["factory"] = TrackingConnection
            return _REAL_CONNECT(target, *args, **kwargs)

        monkeypatch.setattr(store_migration, "acquire_legacy", spy_acquire)
        monkeypatch.setattr(store_migration.sqlite3, "connect", spy_connect)

        _rebuild_sidecar_from_legacy(path, dest)

        assert order[0] == ("acquire", "drive_rebuild")
        assert "connect" in order
        sqls = [
            item[1]
            for item in TrackingConnection.events
            if isinstance(item, tuple) and isinstance(item[1], str)
        ]
        attach = next(sql for sql in sqls if "ATTACH" in sql)
        detach = next(sql for sql in sqls if "DETACH" in sql)
        attach_event = next(
            item
            for item in TrackingConnection.events
            if isinstance(item, tuple) and item[1] == attach
        )
        assert "immutable=1" in attach_event[2][0]
        kinds = [
            item[0] if isinstance(item, tuple) else item
            for item in TrackingConnection.events
        ]
        assert kinds.index("executescript") < kinds.index("execute")
        close_events = [
            item
            for item in TrackingConnection.events
            if item in ("cursor.close", "db.close")
        ]
        assert close_events[-1] == "db.close"
        assert "cursor.close" in close_events
        assert kinds.index("cursor.close") < kinds.index("db.close")
        detach_at = next(
            i
            for i, item in enumerate(TrackingConnection.events)
            if isinstance(item, tuple)
            and isinstance(item[1], str)
            and "DETACH" in item[1]
        )
        assert detach_at < TrackingConnection.events.index("db.close")
        assert detach in sqls
        assert GATE.snapshot(dest).registered is False
        assert_idle(path)

    def test_new_occupant_blocks_attach_until_released(self, tmp_path, monkeypatch):
        path = legacy_db(tmp_path, "rebuild-wait.kohakutr")
        dest = sidecar(path)
        occupying = try_fast(path, eligible=True)
        assert occupying.lease is not None
        attached: list[object] = []
        opened: list[object] = []
        errors: list[BaseException] = []

        class Watch(sqlite3.Connection):
            def execute(self, *args, **kwargs):
                sql = args[0] if args else ""
                if isinstance(sql, str) and "ATTACH" in sql.upper():
                    attached.append(sql)
                return super().execute(*args, **kwargs)

        def spy_connect(target, *args, **kwargs):
            opened.append(target)
            kwargs["factory"] = Watch
            return _REAL_CONNECT(target, *args, **kwargs)

        monkeypatch.setattr(store_migration.sqlite3, "connect", spy_connect)
        done = threading.Event()

        def run_rebuild():
            try:
                _rebuild_sidecar_from_legacy(path, dest)
            except BaseException as cop:
                errors.append(cop)
            finally:
                done.set()

        worker = threading.Thread(target=run_rebuild)
        worker.start()
        try:
            wait_pending(path, 1)
            assert attached == []
            assert opened == []
            occupying.lease.finish(source_released=True)
            assert done.wait(5) and not errors
            assert attached
        finally:
            occupying.lease.finish(source_released=True)
            worker.join(5)
        assert not worker.is_alive() and not errors
        assert_idle(path)


class TestRebuildFaults:
    def test_dest_connect_failure_does_not_quarantine_source(
        self, tmp_path, monkeypatch
    ):
        path = legacy_db(tmp_path, "dest-connect.kohakutr")
        dest = sidecar(path)
        monkeypatch.setattr(
            store_migration.sqlite3,
            "connect",
            lambda *a, **k: (_ for _ in ()).throw(
                sqlite3.OperationalError("dest connect failed")
            ),
        )
        with pytest.raises(sqlite3.OperationalError, match="dest connect failed"):
            _rebuild_sidecar_from_legacy(path, dest)
        assert_idle(path)

    def test_dest_cleanup_before_attach_does_not_quarantine_source(
        self, tmp_path, monkeypatch
    ):
        path = legacy_db(tmp_path, "dest-schema.kohakutr")
        dest = sidecar(path)
        FailingCloseConnection.instances = []
        connect_with_factory(monkeypatch, SchemaThenCloseFails, store_migration)
        try:
            with pytest.raises(RuntimeError, match="schema failed") as caught:
                _rebuild_sidecar_from_legacy(path, dest)
            assert "db close failed" in str(caught.value.__cause__)
            assert_idle(path)
        finally:
            force_close(FailingCloseConnection.instances)

    def test_attach_then_dest_close_failure_is_uncertain(self, tmp_path, monkeypatch):
        path = legacy_db(tmp_path, "attach-close.kohakutr")
        dest = sidecar(path)
        FailingCloseConnection.instances = []
        connect_with_factory(monkeypatch, CommitThenCloseFails, store_migration)
        try:
            with pytest.raises(sqlite3.OperationalError) as caught:
                _rebuild_sidecar_from_legacy(path, dest)
            assert "db close failed" in str(caught.value.__cause__)
            assert try_fast(path, eligible=True).reason == UNCERTAIN
        finally:
            force_close(FailingCloseConnection.instances)

    def test_detach_proof_allows_source_release_if_dest_close_fails(
        self, tmp_path, monkeypatch
    ):
        path = legacy_db(tmp_path, "detach-proof.kohakutr")
        dest = sidecar(path)
        FailingCloseConnection.instances = []
        connect_with_factory(monkeypatch, FailingCloseConnection, store_migration)
        try:
            with pytest.raises(RuntimeError, match="db close failed"):
                _rebuild_sidecar_from_legacy(path, dest)
            assert_idle(path)
        finally:
            force_close(FailingCloseConnection.instances)

    def test_dest_close_after_attach_without_detach_proof_is_uncertain(
        self, tmp_path, monkeypatch
    ):
        path = legacy_db(tmp_path, "no-detach-proof.kohakutr")
        dest = sidecar(path)

        class AttachThenCloseFails(FailingCloseConnection):
            def execute(self, *args, **kwargs):
                sql = args[0] if args else ""
                if isinstance(sql, str) and sql.strip().upper().startswith("DETACH"):
                    raise sqlite3.OperationalError("detach failed")
                return super().execute(*args, **kwargs)

        FailingCloseConnection.instances = []
        connect_with_factory(monkeypatch, AttachThenCloseFails, store_migration)
        try:
            with pytest.raises(sqlite3.OperationalError) as caught:
                _rebuild_sidecar_from_legacy(path, dest)
            assert "db close failed" in str(caught.value.__cause__)
            assert try_fast(path, eligible=True).reason == UNCERTAIN
        finally:
            force_close(FailingCloseConnection.instances)

    def test_tracking_failure_before_attach_does_not_quarantine_source(
        self, tmp_path, monkeypatch
    ):
        path = legacy_db(tmp_path, "track-before.kohakutr")
        dest = sidecar(path)
        monkeypatch.setattr(store_migration, "new_cursors", BoomCursorList)
        with pytest.raises(RuntimeError, match="cannot track cursor"):
            _rebuild_sidecar_from_legacy(path, dest)
        assert_idle(path)

    def test_attach_attempt_without_disposal_proof_stays_uncertain(
        self, tmp_path, monkeypatch
    ):
        path = legacy_db(tmp_path, "attach-flag.kohakutr")
        dest = sidecar(path)

        class AttachFails(FailingCloseConnection):
            def execute(self, *args, **kwargs):
                sql = args[0] if args else ""
                if isinstance(sql, str) and "ATTACH" in sql.upper():
                    raise sqlite3.OperationalError("attach failed")
                return super().execute(*args, **kwargs)

        FailingCloseConnection.instances = []
        connect_with_factory(monkeypatch, AttachFails, store_migration)
        try:
            with pytest.raises(sqlite3.OperationalError, match="attach failed"):
                _rebuild_sidecar_from_legacy(path, dest)
            assert try_fast(path, eligible=True).reason == UNCERTAIN
        finally:
            force_close(FailingCloseConnection.instances)

    def test_temp_unlink_error_preserves_primary(self, tmp_path, monkeypatch):
        path = legacy_db(tmp_path, "unlink.kohakutr")
        dest = sidecar(path)
        FailingCloseConnection.instances = []
        connect_with_factory(monkeypatch, SchemaThenCloseFails, store_migration)

        def boom_unlink(self, missing_ok=False):
            raise OSError("temp unlink failed")

        monkeypatch.setattr(store_migration.Path, "unlink", boom_unlink)
        try:
            with pytest.raises(RuntimeError, match="schema failed") as caught:
                _rebuild_sidecar_from_legacy(path, dest)
            assert "db close failed" in str(caught.value.__cause__)
            assert "temp unlink failed" in str(caught.value.__cause__.__cause__)
            assert_idle(path)
        finally:
            force_close(FailingCloseConnection.instances)
