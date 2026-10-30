"""Source release and initialization faults in legacy Drive migration."""

import sqlite3
from pathlib import Path

import pytest

from kohakuterrarium.session.occupancy.gate import GATE, try_fast
from kohakuterrarium.terrarium.drive import store_migration as migration
from kohakuterrarium.terrarium.drive.store_migration_resources import (
    MigrationSqlResources,
)
from kohakuterrarium.session.occupancy.gate import acquire_legacy
from tests.unit.terrarium.drive.store_migration_resource_fixtures import (
    _REAL_CONNECT,
    assert_idle,
    legacy_db,
    sidecar,
)


@pytest.mark.parametrize("rebuild", [False, True])
def test_ledger_allocation_failure_does_not_enroll(tmp_path, monkeypatch, rebuild):
    path = legacy_db(tmp_path, "allocation.kohakutr")

    def fail():
        raise MemoryError("ledger allocation")

    monkeypatch.setattr(migration, "new_cursors", fail)
    with pytest.raises(MemoryError):
        if rebuild:
            migration._rebuild_sidecar_from_legacy(path, sidecar(path))
        else:
            migration._legacy_same_file_drives(path)
    assert GATE.snapshot(path).legacy_active == 0
    assert_idle(path)


def test_attach_succeeds_before_execute_raises_and_close_fails(tmp_path, monkeypatch):
    path = legacy_db(tmp_path, "partial-attach.kohakutr")
    opened = []
    primary = sqlite3.OperationalError("post attach failure")

    class Connection(sqlite3.Connection):
        def execute(self, sql, params=()):
            cursor = super().execute(sql, params)
            if sql.startswith("ATTACH"):
                cursor.close()
                raise primary
            return cursor

        def close(self):
            raise RuntimeError("close failed")

    def connect(*args, **kwargs):
        conn = _REAL_CONNECT(*args, factory=Connection, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(migration.sqlite3, "connect", connect)
    try:
        with pytest.raises(sqlite3.OperationalError) as caught:
            migration._rebuild_sidecar_from_legacy(path, sidecar(path))
        assert caught.value is primary
        assert try_fast(path, eligible=True).reason == "legacy_release_uncertain"
    finally:
        for conn in opened:
            sqlite3.Connection.close(conn)


def test_failed_copy_with_definite_close_releases_source(tmp_path, monkeypatch):
    path = legacy_db(tmp_path, "copy-fail.kohakutr")

    class Connection(sqlite3.Connection):
        def execute(self, sql, params=()):
            if sql.startswith("INSERT INTO drives SELECT"):
                raise RuntimeError("copy failed")
            return super().execute(sql, params)

    monkeypatch.setattr(
        migration.sqlite3,
        "connect",
        lambda *a, **kw: _REAL_CONNECT(*a, factory=Connection, **kw),
    )
    with pytest.raises(RuntimeError, match="copy failed"):
        migration._rebuild_sidecar_from_legacy(path, sidecar(path))
    assert_idle(path)
    assert not list(tmp_path.glob("*.migrating.*"))


@pytest.mark.parametrize("cursor_error", [False, True])
def test_cleanup_preserves_existing_exception_chain(tmp_path, cursor_error):
    path = tmp_path / "causes.sqlite"
    root = ValueError("root failure")
    primary = RuntimeError("primary failure")
    primary.__cause__ = root
    secondary = OSError("close failure")

    class Cursor(sqlite3.Cursor):
        def close(self):
            super().close()
            raise primary

    class Connection(sqlite3.Connection):
        def close(self):
            super().close()
            raise secondary

    conn = _REAL_CONNECT(path, factory=Connection)
    resources = MigrationSqlResources(acquire_legacy(path, "test_cleanup"))
    resources.mark_source_handle()
    resources.adopt(conn)
    if cursor_error:
        resources.retain_cursor(conn.cursor(factory=Cursor))
    with pytest.raises(RuntimeError) as caught:
        resources.finish(None if cursor_error else primary)
    assert caught.value is primary
    assert primary.__cause__ is root
    assert root.__cause__ is secondary
    assert try_fast(path, eligible=True).reason == "legacy_release_uncertain"


def test_publish_failure_cleans_temporary_database(tmp_path, monkeypatch):
    path = legacy_db(tmp_path, "publish.kohakutr")

    def fail_replace(self, target):
        raise OSError("publication failed")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="publication failed"):
        migration._rebuild_sidecar_from_legacy(path, sidecar(path))
    assert not list(tmp_path.glob("*.migrating.*"))
    assert_idle(path)
