"""Unit tests for ``api.auth.invitations``."""

from datetime import datetime, timedelta, timezone

import pytest

from kohakuterrarium.api.auth.db import (
    connection,
    ensure_migrated,
    _reset_migration_state_for_tests,
)
from kohakuterrarium.api.auth.invitations import (
    consume,
    create,
    list_unused,
    peek,
    revoke,
)
from kohakuterrarium.api.auth.users import create_user

_TEST_ROUNDS = 4


@pytest.fixture
def admin_user(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    _reset_migration_state_for_tests()
    ensure_migrated()
    with connection() as conn:
        user = create_user(conn, "admin", "x", role="admin", bcrypt_rounds=_TEST_ROUNDS)
    yield user
    _reset_migration_state_for_tests()


class TestCreate:
    def test_returns_plaintext_and_row(self, admin_user):
        with connection() as conn:
            plaintext, invite = create(conn, created_by=admin_user.id, role="user")
        assert len(plaintext) == 64  # hex 32 bytes
        assert invite.role == "user"
        assert invite.expires_at is None
        assert invite.used_by is None

    def test_invalid_role_rejected(self, admin_user):
        with connection() as conn:
            with pytest.raises(ValueError):
                create(conn, created_by=admin_user.id, role="superuser")

    def test_with_expiry(self, admin_user):
        with connection() as conn:
            _, invite = create(
                conn, created_by=admin_user.id, role="user", expires_in_hours=24
            )
        assert invite.expires_at is not None


class TestPeek:
    def test_valid_invitation(self, admin_user):
        with connection() as conn:
            plaintext, _ = create(conn, created_by=admin_user.id)
            invite = peek(conn, plaintext)
        assert invite is not None
        assert invite.used_by is None

    def test_missing_token(self, admin_user):
        with connection() as conn:
            assert peek(conn, "garbage") is None

    def test_empty_token(self, admin_user):
        with connection() as conn:
            assert peek(conn, "") is None

    def test_expired_token(self, admin_user):
        # Insert an expired invitation directly.
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        # We need a plaintext that hashes to a known value — easier to
        # just create() normally then UPDATE expires_at to past.
        with connection() as conn:
            plaintext, invite = create(conn, created_by=admin_user.id)
            conn.execute(
                "UPDATE invitations SET expires_at = ? WHERE id = ?",
                (past, invite.id),
            )
            conn.commit()
            assert peek(conn, plaintext) is None

    def test_used_token(self, admin_user):
        with connection() as conn:
            plaintext, _ = create(conn, created_by=admin_user.id)
            consume(conn, plaintext, used_by=admin_user.id)
            assert peek(conn, plaintext) is None


class TestConsume:
    def test_atomic_claim(self, admin_user):
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            plaintext, _ = create(conn, created_by=admin_user.id)
            consumed = consume(conn, plaintext, used_by=user.id)
        assert consumed is not None
        assert consumed.used_by == user.id
        assert consumed.used_at is not None

    def test_double_consume_returns_none(self, admin_user):
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            plaintext, _ = create(conn, created_by=admin_user.id)
            consume(conn, plaintext, used_by=user.id)
            second = consume(conn, plaintext, used_by=user.id)
        assert second is None

    def test_unknown_token(self, admin_user):
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            assert consume(conn, "garbage", used_by=user.id) is None

    def test_expired_token_cannot_consume(self, admin_user):
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            plaintext, invite = create(conn, created_by=admin_user.id)
            past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            conn.execute(
                "UPDATE invitations SET expires_at = ? WHERE id = ?",
                (past, invite.id),
            )
            conn.commit()
            assert consume(conn, plaintext, used_by=user.id) is None


class TestListUnused:
    def test_only_unused_returned(self, admin_user):
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            p1, _ = create(conn, created_by=admin_user.id)
            p2, _ = create(conn, created_by=admin_user.id)
            consume(conn, p1, used_by=user.id)
            unused = list_unused(conn)
        assert len(unused) == 1


class TestRevoke:
    def test_revoke_unused(self, admin_user):
        with connection() as conn:
            _, invite = create(conn, created_by=admin_user.id)
            assert revoke(conn, invite.id) is True
            assert revoke(conn, invite.id) is False  # already gone

    def test_cannot_revoke_used(self, admin_user):
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            plaintext, invite = create(conn, created_by=admin_user.id)
            consume(conn, plaintext, used_by=user.id)
            # Used invitations stay in the DB as audit; revoke only
            # cleans up unused ones.
            assert revoke(conn, invite.id) is False
