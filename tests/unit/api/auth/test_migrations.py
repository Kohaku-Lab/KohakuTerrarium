"""Unit tests for the migration runner."""

import sqlite3

import pytest

from kohakuterrarium.api.auth.db import (
    connection,
    ensure_migrated,
    _reset_migration_state_for_tests,
)
from kohakuterrarium.api.auth.migrations import run_migrations


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "auth.db"
    monkeypatch.setenv("KT_AUTH_DB", str(path))
    _reset_migration_state_for_tests()
    yield path
    _reset_migration_state_for_tests()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    )
    return cur.fetchone() is not None


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


class TestInitialMigration:
    def test_fresh_db_creates_every_table(self, db_path):
        ensure_migrated()
        with connection() as conn:
            assert _table_exists(conn, "schema_version")
            assert _table_exists(conn, "users")
            assert _table_exists(conn, "sessions")
            assert _table_exists(conn, "api_tokens")
            assert _table_exists(conn, "invitations")

    def test_users_table_columns_match_schema(self, db_path):
        ensure_migrated()
        with connection() as conn:
            cols = _column_names(conn, "users")
        expected = {
            "id",
            "username",
            "password_hash",
            "role",
            "is_active",
            "created_at",
            "last_login_at",
        }
        assert expected.issubset(cols)

    def test_sessions_have_user_fk(self, db_path):
        ensure_migrated()
        with connection() as conn:
            cur = conn.execute("PRAGMA foreign_key_list(sessions)")
            fks = cur.fetchall()
        assert any(
            row["table"] == "users" and row["from"] == "user_id" for row in fks
        ), f"sessions.user_id should FK -> users.id; got {[dict(r) for r in fks]}"

    def test_indexes_exist(self, db_path):
        ensure_migrated()
        with connection() as conn:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            names = {row[0] for row in cur.fetchall()}
        # Migration 002 replaced ``idx_users_username`` with the
        # case-insensitive variant; the new name must exist after a
        # fresh-DB walk through both migrations.
        assert "idx_users_username_nocase" in names
        assert "idx_users_username" not in names
        assert "idx_sessions_user_id" in names
        assert "idx_sessions_expires" in names
        assert "idx_api_tokens_user_id" in names
        assert "idx_invitations_token_hash" in names

    def test_schema_version_row_recorded(self, db_path):
        ensure_migrated()
        with connection() as conn:
            cur = conn.execute("SELECT version FROM schema_version")
            versions = {row[0] for row in cur.fetchall()}
        assert 1 in versions
        # Migration 002 added in the audit pass; both must record.
        assert 2 in versions


class TestMigration002:
    """Audit fixes from migration 002 — case-insensitive uniqueness
    + invitations FK ON DELETE SET NULL."""

    def test_case_insensitive_username_uniqueness(self, db_path):
        ensure_migrated()
        with connection() as conn:
            conn.execute(
                "INSERT INTO users(username, password_hash, role, "
                "is_active, created_at) VALUES "
                "(?, ?, 'user', 1, '2026-05-22T00:00:00+00:00')",
                ("Alice", "x"),
            )
            # Direct INSERT bypassing the application-level check —
            # the DB-level UNIQUE INDEX on LOWER(username) must reject.
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO users(username, password_hash, role, "
                    "is_active, created_at) VALUES "
                    "(?, ?, 'user', 1, '2026-05-22T00:00:00+00:00')",
                    ("ALICE", "y"),
                )

    def test_invitations_created_by_set_null_on_user_delete(self, db_path):
        ensure_migrated()
        with connection() as conn:
            # Create an admin + an invitation they issued.
            conn.execute(
                "INSERT INTO users(username, password_hash, role, "
                "is_active, created_at) VALUES "
                "(?, ?, 'admin', 1, '2026-05-22T00:00:00+00:00')",
                ("admin", "x"),
            )
            admin_id = conn.execute(
                "SELECT id FROM users WHERE username='admin'"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO invitations(token_hash, created_by, role, "
                "created_at) VALUES "
                "(?, ?, 'user', '2026-05-22T00:00:00+00:00')",
                ("hash1", admin_id),
            )
            # Deleting the admin must succeed (not fail FK) and the
            # invitation row's created_by must flip to NULL.
            conn.execute("DELETE FROM users WHERE id = ?", (admin_id,))
            conn.commit()
            row = conn.execute(
                "SELECT created_by FROM invitations WHERE token_hash='hash1'"
            ).fetchone()
        assert row is not None  # invitation row survived
        assert row["created_by"] is None  # FK set null

    def test_invitations_used_by_set_null_on_user_delete(self, db_path):
        ensure_migrated()
        with connection() as conn:
            conn.execute(
                "INSERT INTO users(username, password_hash, role, "
                "is_active, created_at) VALUES "
                "(?, ?, 'user', 1, '2026-05-22T00:00:00+00:00')",
                ("alice", "x"),
            )
            uid = conn.execute(
                "SELECT id FROM users WHERE username='alice'"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO invitations(token_hash, created_by, used_by, "
                "used_at, role, created_at) VALUES "
                "(?, NULL, ?, '2026-05-22T00:00:00+00:00', 'user', "
                "'2026-05-22T00:00:00+00:00')",
                ("hash2", uid),
            )
            conn.execute("DELETE FROM users WHERE id = ?", (uid,))
            conn.commit()
            row = conn.execute(
                "SELECT used_by FROM invitations WHERE token_hash='hash2'"
            ).fetchone()
        assert row is not None
        assert row["used_by"] is None


class TestIdempotence:
    def test_running_twice_is_noop(self, db_path):
        ensure_migrated()
        # Insert a user — second migration call must NOT clobber the
        # table or the user.
        with connection() as conn:
            conn.execute(
                "INSERT INTO users(username, password_hash, role, is_active, created_at) "
                "VALUES ('alice', 'x', 'user', 1, '2026-05-21T00:00:00+00:00')"
            )
        _reset_migration_state_for_tests()
        ensure_migrated()  # second pass
        with connection() as conn:
            cur = conn.execute("SELECT username FROM users")
            usernames = [row[0] for row in cur.fetchall()]
        assert usernames == ["alice"]

    def test_in_process_cache_short_circuits(self, db_path):
        ensure_migrated()
        # Without resetting the in-process cache, second call doesn't
        # even open a connection — it returns immediately.  We can't
        # directly observe that here, but we can assert the second
        # call's return value matches the path.
        p = ensure_migrated()
        assert p == db_path


class TestCustomConnection:
    def test_run_migrations_against_explicit_conn(self, tmp_path):
        # Direct API — caller manages the connection.
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # ON DELETE SET NULL needs foreign keys enforced.
        conn.execute("PRAGMA foreign_keys = ON")
        version = run_migrations(conn)
        # Latest applied — bumps as new migrations land.
        assert version >= 2
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        assert {"users", "sessions", "api_tokens", "invitations"}.issubset(tables)
        conn.close()
