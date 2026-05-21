"""Unit tests for ``api.auth.tokens``."""

import pytest

from kohakuterrarium.api.auth.db import (
    connection,
    ensure_migrated,
    _reset_migration_state_for_tests,
)
from kohakuterrarium.api.auth.tokens import (
    create_token,
    delete_token,
    delete_token_admin,
    get_token_user,
    list_user_tokens,
)
from kohakuterrarium.api.auth.users import create_user, set_active

_TEST_ROUNDS = 4


@pytest.fixture
def two_users(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    _reset_migration_state_for_tests()
    ensure_migrated()
    with connection() as conn:
        alice = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
        bob = create_user(conn, "bob", "x", bcrypt_rounds=_TEST_ROUNDS)
    yield alice, bob
    _reset_migration_state_for_tests()


class TestCreateToken:
    def test_returns_plaintext_once(self, two_users):
        alice, _ = two_users
        with connection() as conn:
            plaintext, token = create_token(conn, alice.id, "kt-cli")
        # 32 bytes hex = 64 chars.
        assert len(plaintext) == 64
        assert token.name == "kt-cli"
        assert token.id > 0

    def test_empty_name_rejected(self, two_users):
        alice, _ = two_users
        with connection() as conn:
            with pytest.raises(ValueError):
                create_token(conn, alice.id, "")

    def test_whitespace_name_rejected(self, two_users):
        alice, _ = two_users
        with connection() as conn:
            with pytest.raises(ValueError):
                create_token(conn, alice.id, "   ")


class TestGetTokenUser:
    def test_plaintext_resolves_to_user(self, two_users):
        alice, _ = two_users
        with connection() as conn:
            plaintext, _ = create_token(conn, alice.id, "k")
            user = get_token_user(conn, plaintext)
        assert user is not None
        assert user.username == "alice"

    def test_wrong_token_returns_none(self, two_users):
        alice, _ = two_users
        with connection() as conn:
            create_token(conn, alice.id, "k")
            assert get_token_user(conn, "garbage") is None

    def test_empty_token_returns_none(self, two_users):
        with connection() as conn:
            assert get_token_user(conn, "") is None

    def test_inactive_user_token_returns_none(self, two_users):
        alice, _ = two_users
        with connection() as conn:
            plaintext, _ = create_token(conn, alice.id, "k")
            set_active(conn, alice.id, False)
            assert get_token_user(conn, plaintext) is None

    def test_last_used_updated_on_lookup(self, two_users):
        alice, _ = two_users
        with connection() as conn:
            plaintext, token = create_token(conn, alice.id, "k")
            assert token.last_used_at is None
            get_token_user(conn, plaintext)
            row = conn.execute(
                "SELECT last_used_at FROM api_tokens WHERE id = ?", (token.id,)
            ).fetchone()
        assert row["last_used_at"] is not None


class TestListUserTokens:
    def test_lists_only_owners(self, two_users):
        alice, bob = two_users
        with connection() as conn:
            create_token(conn, alice.id, "alice-1")
            create_token(conn, alice.id, "alice-2")
            create_token(conn, bob.id, "bob-1")
            alice_tokens = list_user_tokens(conn, alice.id)
            bob_tokens = list_user_tokens(conn, bob.id)
        assert {t.name for t in alice_tokens} == {"alice-1", "alice-2"}
        assert {t.name for t in bob_tokens} == {"bob-1"}


class TestDeleteToken:
    def test_owner_revoke_succeeds(self, two_users):
        alice, _ = two_users
        with connection() as conn:
            _, token = create_token(conn, alice.id, "k")
            assert delete_token(conn, alice.id, token.id) is True

    def test_non_owner_cannot_revoke(self, two_users):
        alice, bob = two_users
        with connection() as conn:
            _, token = create_token(conn, alice.id, "k")
            # Bob tries to revoke Alice's token by id — should fail.
            assert delete_token(conn, bob.id, token.id) is False
            # Token still works.
            row = conn.execute(
                "SELECT 1 FROM api_tokens WHERE id = ?", (token.id,)
            ).fetchone()
            assert row is not None

    def test_revoked_token_no_longer_resolves(self, two_users):
        alice, _ = two_users
        with connection() as conn:
            plaintext, token = create_token(conn, alice.id, "k")
            delete_token(conn, alice.id, token.id)
            assert get_token_user(conn, plaintext) is None


class TestDeleteTokenAdmin:
    def test_admin_can_delete_any(self, two_users):
        alice, bob = two_users
        with connection() as conn:
            _, alice_token = create_token(conn, alice.id, "k")
            assert delete_token_admin(conn, alice_token.id) is True


class TestCascadeOnUserDelete:
    def test_tokens_dropped_when_user_deleted(self, two_users):
        alice, _ = two_users
        with connection() as conn:
            plaintext, _ = create_token(conn, alice.id, "k")
            conn.execute("DELETE FROM users WHERE id = ?", (alice.id,))
            conn.commit()
            assert get_token_user(conn, plaintext) is None
