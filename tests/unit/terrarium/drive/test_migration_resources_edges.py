"""Offline SQLite edge faults for Drive migration resource cleanup."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest

import kohakuterrarium.terrarium.drive.store_migration as migration
from kohakuterrarium.session.occupancy.gate import GATE, try_fast
from kohakuterrarium.terrarium.drive.errors import DriveSchemaVersionError
from kohakuterrarium.terrarium.drive.store_migration import DRIVE_SCHEMA_VERSION
from kohakuterrarium.terrarium.drive.store_migration_resources import (
    MigrationSqlResources,
    append_cleanup,
)
from kohakuterrarium.utils.file.lock import FileLock
from tests.unit.terrarium.drive.store_migration_resource_fixtures import (
    _REAL_CONNECT,
    assert_idle,
    complete_sidecar,
    connect_with_factory,
    empty_session,
    legacy_db,
    sidecar,
)


class _SqliteTrackingBoom(list):
    def append(self, item):
        raise sqlite3.OperationalError("malicious tracking")


class _OrphanCloseFails(sqlite3.Cursor):
    def close(self):
        raise RuntimeError("orphan close failed")


class _OrphanCloseConnection(sqlite3.Connection):
    def execute(self, *args, **kwargs):
        return super().cursor(_OrphanCloseFails).execute(*args, **kwargs)


def _meta_file(path: str, value: str | None = None, *, table: bool = True) -> str:
    conn = _REAL_CONNECT(path)
    if table:
        conn.execute("CREATE TABLE drive_meta (key TEXT PRIMARY KEY, value TEXT)")
        if value is not None:
            conn.execute(
                "INSERT INTO drive_meta(key, value) VALUES('schema_version', ?)",
                (value,),
            )
    else:
        conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    return path


class TestAppendCleanupOverlap:
    def test_overlapping_cause_is_not_rewired(self):
        shared = ValueError("shared")
        primary = RuntimeError("primary")
        primary.__cause__ = shared
        cleanup = OSError("cleanup")
        cleanup.__cause__ = shared
        append_cleanup(primary, cleanup)
        assert primary.__cause__ is shared
        assert shared.__cause__ is None
        assert cleanup.__cause__ is shared

    def test_cleanup_already_on_chain_is_ignored(self):
        root = ValueError("root")
        primary = RuntimeError("primary")
        primary.__cause__ = root
        append_cleanup(primary, root)
        assert primary.__cause__ is root
        assert root.__cause__ is None


class TestRetainCursorCloseChain:
    def test_sqlite_tracking_error_is_not_swallowed_as_false(
        self, tmp_path, monkeypatch
    ):
        path = legacy_db(tmp_path, "track-sql.kohakutr")
        monkeypatch.setattr(migration, "new_cursors", _SqliteTrackingBoom)
        connect_with_factory(monkeypatch, _OrphanCloseConnection, migration)
        with pytest.raises(
            sqlite3.OperationalError, match="malicious tracking"
        ) as caught:
            migration._legacy_same_file_drives(path)
        assert "orphan close failed" in str(caught.value.__cause__)
        assert try_fast(path, eligible=True).reason == "legacy_release_uncertain"


class TestSourceResourcesInitFailure:
    @pytest.mark.parametrize("rebuild", [False, True])
    def test_initializer_failure_releases_lease(self, tmp_path, monkeypatch, rebuild):
        path = legacy_db(tmp_path, "init-fail.kohakutr")

        def boom_init(self, lease, *, cursors=None):
            raise MemoryError("resources init failed")

        monkeypatch.setattr(MigrationSqlResources, "__init__", boom_init)
        with pytest.raises(MemoryError, match="resources init failed"):
            if rebuild:
                migration._rebuild_sidecar_from_legacy(path, sidecar(path))
            else:
                migration._legacy_same_file_drives(path)
        assert GATE.snapshot(path).legacy_active == 0
        assert_idle(path)


class TestSidecarProbeIo:
    def test_timeout_budget_is_honored_on_complete_sidecar(self, tmp_path):
        dest = complete_sidecar(empty_session(tmp_path, "budget.kohakutr"))
        assert migration._sidecar_is_complete(dest, timeout_s=5.0) is True
        assert migration._sidecar_is_complete(dest, timeout_s=0) is False

    def test_connect_error_returns_false(self, tmp_path, monkeypatch):
        dest = complete_sidecar(empty_session(tmp_path, "connect.kohakutr"))
        monkeypatch.setattr(
            migration.sqlite3,
            "connect",
            lambda *a, **k: (_ for _ in ()).throw(
                sqlite3.OperationalError("sidecar connect failed")
            ),
        )
        assert migration._sidecar_is_complete(dest) is False

    def test_missing_marker_or_version_returns_false(self, tmp_path):
        none = _meta_file(str(tmp_path / "none.drives"), table=False)
        missing = _meta_file(str(tmp_path / "missing.drives"))
        assert migration._sidecar_is_complete(none) is False
        assert migration._sidecar_is_complete(missing) is False

    def test_invalid_version_probe_returns_false(self, tmp_path):
        dest = _meta_file(str(tmp_path / "zero.drives"), "0")
        assert migration._sidecar_is_complete(dest) is False
        with pytest.raises(ValueError, match="schema version must be >= 1"):
            migration._parse_schema_version("0")

    def test_corrupt_database_returns_false(self, tmp_path):
        dest = tmp_path / "corrupt.drives"
        dest.write_bytes(b"SQLite format 3\x00" + b"\xff" * 80)
        assert migration._sidecar_is_complete(str(dest)) is False


class TestRejectInvalidMarker:
    def test_connect_error_is_ignored(self, tmp_path, monkeypatch):
        dest = str(tmp_path / "reject-connect.drives")
        Path(dest).write_bytes(b"data")
        monkeypatch.setattr(
            migration.sqlite3,
            "connect",
            lambda *a, **k: (_ for _ in ()).throw(
                sqlite3.OperationalError("reject connect failed")
            ),
        )
        migration._reject_invalid_sidecar_marker(dest)

    def test_missing_meta_or_row_is_left_for_rebuild(self, tmp_path):
        none = _meta_file(str(tmp_path / "reject-none.drives"), table=False)
        missing = _meta_file(str(tmp_path / "reject-missing.drives"))
        migration._reject_invalid_sidecar_marker(none)
        migration._reject_invalid_sidecar_marker(missing)

    def test_database_error_is_ignored(self, tmp_path):
        dest = tmp_path / "reject-corrupt.drives"
        dest.write_bytes(b"SQLite format 3\x00" + b"\xff" * 80)
        migration._reject_invalid_sidecar_marker(str(dest))

    @pytest.mark.parametrize(
        "value",
        ["not-a-number", "0", str(DRIVE_SCHEMA_VERSION + 4)],
    )
    def test_present_invalid_marker_raises(self, tmp_path, value):
        dest = _meta_file(str(tmp_path / f"reject-{value}.drives"), value)
        with pytest.raises(DriveSchemaVersionError):
            migration._reject_invalid_sidecar_marker(dest)
        assert Path(dest).exists()
        assert not Path(dest + ".corrupt").exists()


class TestQuarantineAndSweep:
    def test_missing_family_is_noop(self, tmp_path):
        migration._quarantine_incomplete_sidecar(str(tmp_path / "absent.drives"))
        assert list(tmp_path.iterdir()) == []

    def test_wal_only_is_quarantined(self, tmp_path):
        dest = str(tmp_path / "wal-only.drives")
        Path(dest + "-wal").write_bytes(b"wal")
        migration._quarantine_incomplete_sidecar(dest)
        assert not Path(dest + "-wal").exists()
        assert Path(dest + ".corrupt-wal").exists()

    def test_replace_oserror_unlinks_source(self, tmp_path, monkeypatch):
        dest = str(tmp_path / "replace.drives")
        Path(dest).write_bytes(b"incomplete")

        def boom_replace(self, target):
            raise OSError("replace blocked")

        monkeypatch.setattr(Path, "replace", boom_replace)
        migration._quarantine_incomplete_sidecar(dest)
        assert not Path(dest).exists()

    def test_sweep_skips_non_attempt_names(self, tmp_path):
        dest = str(tmp_path / "s.drives")
        prefix = Path(dest).name + ".migrating."
        keep_short = tmp_path / (prefix + "nope")
        keep_extra = tmp_path / (prefix + ("a" * 32) + "-extra")
        keep_upper = tmp_path / (prefix + ("B" * 32))
        drop = tmp_path / (prefix + ("c" * 32) + "-wal")
        for item in (keep_short, keep_extra, keep_upper, drop):
            item.write_bytes(b"tmp")
        migration._sweep_abandoned_migration_temps(dest)
        assert keep_short.exists()
        assert keep_extra.exists()
        assert keep_upper.exists()
        assert not drop.exists()


class TestMigrateEarlyReturns:
    def test_waiter_returns_when_peer_completes_without_lock(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(migration, "_MIGRATE_POLL_S", 0.01)
        path = empty_session(tmp_path, "peer.kohakutr")
        dest = sidecar(path)
        holder = FileLock(dest + ".migrate-lock")
        holder.acquire()
        done = threading.Event()
        errors: list[BaseException] = []

        def run():
            try:
                migration._migrate_same_file_drives(path, dest)
            except BaseException as exc:
                errors.append(exc)
            finally:
                done.set()

        worker = threading.Thread(target=run)
        worker.start()
        try:
            deadline = time.monotonic() + 2.0
            while (
                time.monotonic() < deadline and GATE.snapshot(path).legacy_active == 0
            ):
                time.sleep(0.01)
            complete_sidecar(path)
            assert done.wait(5)
            assert errors == []
            assert migration._sidecar_is_complete(dest) is True
        finally:
            holder.release()
            worker.join(5)
        assert_idle(path)

    def test_complete_after_lock_skips_rebuild(self, tmp_path, monkeypatch):
        path = empty_session(tmp_path, "after-lock.kohakutr")
        dest = sidecar(path)

        def sweep_and_complete(sidecar_path):
            complete_sidecar(path)

        monkeypatch.setattr(
            migration, "_sweep_abandoned_migration_temps", sweep_and_complete
        )
        migration._migrate_same_file_drives(path, dest)
        assert migration._sidecar_is_complete(dest) is True
        assert_idle(path)

    def test_incomplete_destination_is_quarantined_then_rebuilt(self, tmp_path):
        path = legacy_db(tmp_path, "rebuild-incomplete.kohakutr")
        dest = sidecar(path)
        Path(dest).write_bytes(b"incomplete-bytes")
        migration._migrate_same_file_drives(path, dest)
        assert Path(dest + ".corrupt").exists()
        assert migration._sidecar_is_complete(dest) is True
        assert_idle(path)

    def test_incomplete_destination_without_legacy_rows_is_quarantined(self, tmp_path):
        path = empty_session(tmp_path, "no-legacy.kohakutr")
        dest = sidecar(path)
        Path(dest).write_bytes(b"incomplete-bytes")
        migration._migrate_same_file_drives(path, dest)
        assert Path(dest + ".corrupt").exists()
        assert not Path(dest).exists()
        assert_idle(path)


class TestMigrationLockTimeouts:
    def test_live_holder_times_out_without_sidecar(self, tmp_path, monkeypatch):
        monkeypatch.setattr(migration, "_MIGRATE_LOCK_TIMEOUT_S", 0.05)
        monkeypatch.setattr(migration, "_MIGRATE_POLL_S", 0.01)
        dest = str(tmp_path / "held.drives")
        holder = FileLock(dest + ".migrate-lock")
        holder.acquire()
        try:
            with pytest.raises(TimeoutError, match="held past the"):
                with migration._migration_lock(dest):
                    pass
        finally:
            holder.release()

    def test_deadline_after_complete_probe_still_times_out(self, tmp_path, monkeypatch):
        dest = complete_sidecar(empty_session(tmp_path, "late.kohakutr"))
        holder = FileLock(dest + ".migrate-lock")
        holder.acquire()
        calls = {"n": 0}

        def slow_complete(sidecar_path, *, timeout_s=None):
            calls["n"] += 1
            time.sleep(0.03)
            return True

        monkeypatch.setattr(migration, "_MIGRATE_LOCK_TIMEOUT_S", 0.02)
        monkeypatch.setattr(migration, "_MIGRATE_POLL_S", 0.01)
        monkeypatch.setattr(migration, "_sidecar_is_complete", slow_complete)
        try:
            with pytest.raises(TimeoutError, match="held past the"):
                with migration._migration_lock(dest):
                    pass
            assert calls["n"] >= 1
        finally:
            holder.release()


class TestSidecarPathHelpers:
    def test_family_excludes_migration_lock(self, tmp_path):
        session = tmp_path / "alice.kohakutr"
        family = migration.drive_sidecar_family(session)
        assert family == [
            str(session) + ".drives",
            str(session) + ".drives-wal",
            str(session) + ".drives-shm",
        ]
        assert migration.drive_sidecar_path(session) == str(session) + ".drives"
        assert all(".migrate-lock" not in item for item in family)
