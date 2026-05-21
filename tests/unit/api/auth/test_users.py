"""Unit tests for ``api.auth.users``."""

import pytest

from kohakuterrarium.api.auth.db import (
    connection,
    ensure_migrated,
    _reset_migration_state_for_tests,
)
from kohakuterrarium.api.auth.users import (
    InvalidUsernameError,
    UserError,
    UserNotFoundError,
    UsernameInUseError,
    count_admins,
    create_user,
    delete_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    set_active,
    set_password,
    set_role,
    touch_last_login,
    validate_username,
    verify_user_password,
)

# Use a low bcrypt cost factor for fast tests — production uses 12.
_TEST_ROUNDS = 4


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    _reset_migration_state_for_tests()
    ensure_migrated()
    yield
    _reset_migration_state_for_tests()


class TestUsernameValidation:
    @pytest.mark.parametrize(
        "good",
        ["alice", "BOB", "user-1", "_underscore", "ab", "x" * 64],
    )
    def test_good_usernames_pass(self, good):
        assert validate_username(good) == good

    def test_whitespace_stripped(self):
        assert validate_username("  alice  ") == "alice"

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "a",  # too short
            "x" * 65,  # too long
            "has space",
            "has.dot",
            "has@at",
            "hello!",
            "ünıcode",
        ],
    )
    def test_bad_usernames_raise(self, bad):
        with pytest.raises(InvalidUsernameError):
            validate_username(bad)


class TestCreateUser:
    def test_creates_and_round_trips(self, fresh_db):
        with connection() as conn:
            user = create_user(conn, "alice", "pwd", bcrypt_rounds=_TEST_ROUNDS)
        assert user.id > 0
        assert user.username == "alice"
        assert user.role == "user"
        assert user.is_active is True
        assert user.last_login_at is None

    def test_duplicate_username_rejected(self, fresh_db):
        with connection() as conn:
            create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            with pytest.raises(UsernameInUseError):
                create_user(conn, "alice", "y", bcrypt_rounds=_TEST_ROUNDS)

    def test_duplicate_case_insensitive(self, fresh_db):
        with connection() as conn:
            create_user(conn, "Alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            with pytest.raises(UsernameInUseError):
                create_user(conn, "ALICE", "y", bcrypt_rounds=_TEST_ROUNDS)

    def test_empty_password_rejected(self, fresh_db):
        with connection() as conn:
            with pytest.raises(UserError):
                create_user(conn, "alice", "", bcrypt_rounds=_TEST_ROUNDS)

    def test_invalid_username_rejected(self, fresh_db):
        with connection() as conn:
            with pytest.raises(InvalidUsernameError):
                create_user(conn, "no space", "pwd", bcrypt_rounds=_TEST_ROUNDS)

    def test_admin_role(self, fresh_db):
        with connection() as conn:
            user = create_user(
                conn, "root", "pwd", role="admin", bcrypt_rounds=_TEST_ROUNDS
            )
        assert user.role == "admin"

    def test_invalid_role_rejected(self, fresh_db):
        with connection() as conn:
            with pytest.raises(UserError):
                create_user(
                    conn,
                    "x",
                    "pwd",
                    role="superuser",
                    bcrypt_rounds=_TEST_ROUNDS,
                )


class TestLookups:
    def test_get_by_id(self, fresh_db):
        with connection() as conn:
            created = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            found = get_user_by_id(conn, created.id)
        assert found is not None
        assert found.username == "alice"

    def test_get_by_id_missing(self, fresh_db):
        with connection() as conn:
            assert get_user_by_id(conn, 999) is None

    def test_get_by_username_case_insensitive(self, fresh_db):
        with connection() as conn:
            create_user(conn, "Alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            assert get_user_by_username(conn, "alice") is not None
            assert get_user_by_username(conn, "ALICE") is not None

    def test_list_users(self, fresh_db):
        with connection() as conn:
            create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            create_user(conn, "bob", "x", bcrypt_rounds=_TEST_ROUNDS)
            users = list_users(conn)
        names = {u.username for u in users}
        assert names == {"alice", "bob"}


class TestVerifyPassword:
    def test_correct_password(self, fresh_db):
        with connection() as conn:
            create_user(conn, "alice", "hunter2", bcrypt_rounds=_TEST_ROUNDS)
            user = verify_user_password(conn, "alice", "hunter2")
        assert user is not None
        assert user.username == "alice"

    def test_wrong_password(self, fresh_db):
        with connection() as conn:
            create_user(conn, "alice", "hunter2", bcrypt_rounds=_TEST_ROUNDS)
            user = verify_user_password(conn, "alice", "wrong")
        assert user is None

    def test_unknown_user(self, fresh_db):
        with connection() as conn:
            user = verify_user_password(conn, "nobody", "x")
        assert user is None

    def test_inactive_user(self, fresh_db):
        with connection() as conn:
            created = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            set_active(conn, created.id, False)
            user = verify_user_password(conn, "alice", "x")
        assert user is None

    def test_username_whitespace_normalized(self, fresh_db):
        with connection() as conn:
            create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            user = verify_user_password(conn, "  alice  ", "x")
        assert user is not None


class TestSetPassword:
    def test_set_then_verify(self, fresh_db):
        with connection() as conn:
            user = create_user(conn, "alice", "old", bcrypt_rounds=_TEST_ROUNDS)
            set_password(conn, user.id, "new", bcrypt_rounds=_TEST_ROUNDS)
            assert verify_user_password(conn, "alice", "old") is None
            assert verify_user_password(conn, "alice", "new") is not None

    def test_missing_user_raises(self, fresh_db):
        with connection() as conn:
            with pytest.raises(UserNotFoundError):
                set_password(conn, 999, "new", bcrypt_rounds=_TEST_ROUNDS)

    def test_empty_password_rejected(self, fresh_db):
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            with pytest.raises(UserError):
                set_password(conn, user.id, "", bcrypt_rounds=_TEST_ROUNDS)


class TestSetRole:
    def test_promote_to_admin(self, fresh_db):
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            set_role(conn, user.id, "admin")
            updated = get_user_by_id(conn, user.id)
        assert updated is not None
        assert updated.role == "admin"

    def test_invalid_role_rejected(self, fresh_db):
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            with pytest.raises(UserError):
                set_role(conn, user.id, "wizard")


class TestSetActive:
    def test_disable_user(self, fresh_db):
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            set_active(conn, user.id, False)
            updated = get_user_by_id(conn, user.id)
        assert updated is not None
        assert updated.is_active is False


class TestTouchLastLogin:
    def test_sets_timestamp(self, fresh_db):
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            touch_last_login(conn, user.id)
            updated = get_user_by_id(conn, user.id)
        assert updated is not None
        assert updated.last_login_at is not None


class TestDeleteUser:
    def test_delete_returns_true_when_found(self, fresh_db):
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            assert delete_user(conn, user.id) is True
            assert get_user_by_id(conn, user.id) is None

    def test_delete_returns_false_when_missing(self, fresh_db):
        with connection() as conn:
            assert delete_user(conn, 999) is False


class TestCountAdmins:
    def test_zero_admins_initially(self, fresh_db):
        with connection() as conn:
            assert count_admins(conn) == 0

    def test_counts_active_admins_only(self, fresh_db):
        with connection() as conn:
            create_user(conn, "admin1", "x", role="admin", bcrypt_rounds=_TEST_ROUNDS)
            b = create_user(
                conn, "admin2", "x", role="admin", bcrypt_rounds=_TEST_ROUNDS
            )
            assert count_admins(conn) == 2
            set_active(conn, b.id, False)
            assert count_admins(conn) == 1
