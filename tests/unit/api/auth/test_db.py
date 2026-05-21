"""Unit tests for :mod:`api.auth.db` — connection factory + pragmas."""

import sqlite3

import pytest

from kohakuterrarium.api.auth.db import (
    auth_db_path,
    connection,
    ensure_migrated,
    open_connection,
    _reset_migration_state_for_tests,
)


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    """Point auth.db at a fresh per-test sqlite file."""
    path = tmp_path / "auth.db"
    monkeypatch.setenv("KT_AUTH_DB", str(path))
    _reset_migration_state_for_tests()
    yield path
    _reset_migration_state_for_tests()


class TestPathResolution:
    def test_env_var_wins(self, db_path):
        assert auth_db_path() == db_path

    def test_default_is_under_config_dir(self, monkeypatch):
        monkeypatch.delenv("KT_AUTH_DB", raising=False)
        # KT_CONFIG_DIR set by the autouse conftest fixture.
        p = auth_db_path()
        assert p.name == "auth.db"


class TestPragmas:
    def test_foreign_keys_enabled(self, db_path):
        with connection() as conn:
            cur = conn.execute("PRAGMA foreign_keys")
            assert cur.fetchone()[0] == 1

    def test_journal_mode_is_wal(self, db_path):
        with connection() as conn:
            cur = conn.execute("PRAGMA journal_mode")
            mode = cur.fetchone()[0].lower()
            # On some filesystems WAL falls back; we accept wal or delete
            # (the standard fallback) — the important thing is no
            # exception was raised by the pragma SET.
            assert mode in {"wal", "delete", "memory"}

    def test_row_factory_lets_index_by_name(self, db_path):
        ensure_migrated()
        with connection() as conn:
            conn.execute(
                "INSERT INTO users(username, password_hash, role, is_active, created_at) "
                "VALUES (?, ?, 'user', 1, '2026-05-21T00:00:00+00:00')",
                ("alice", "x"),
            )
            row = conn.execute(
                "SELECT id, username FROM users WHERE username = ?", ("alice",)
            ).fetchone()
            assert row is not None
            assert row["username"] == "alice"
            assert isinstance(row["id"], int)


class TestConnectionLifecycle:
    def test_context_closes_connection(self, db_path):
        with connection() as conn:
            conn.execute("SELECT 1").fetchone()
        # After context exit, using the conn raises sqlite ProgrammingError.
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")

    def test_open_connection_returns_usable_handle(self, db_path):
        conn = open_connection()
        try:
            assert conn.execute("SELECT 1 + 1").fetchone()[0] == 2
        finally:
            conn.close()

    def test_creates_parent_dir(self, tmp_path, monkeypatch):
        # Nested non-existent dir — open_connection should mkdir -p.
        target = tmp_path / "a" / "b" / "c" / "auth.db"
        monkeypatch.setenv("KT_AUTH_DB", str(target))
        _reset_migration_state_for_tests()
        conn = open_connection()
        try:
            assert target.exists()
        finally:
            conn.close()
