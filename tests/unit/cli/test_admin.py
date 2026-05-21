"""Unit tests for ``kt admin`` verbs.

The CLI module talks straight to the auth.db + config.toml on disk —
no server involved.  Tests redirect both via ``KT_CONFIG_DIR`` /
``KT_AUTH_DB`` to per-test tmpdirs.
"""

import argparse
from pathlib import Path

import pytest

from kohakuterrarium.api.auth.config_write import (
    config_toml_path as _config_toml_path,
    read_config_toml as _read_config_toml,
)
from kohakuterrarium.api.auth.db import (
    connection,
    ensure_migrated,
    _reset_migration_state_for_tests,
)
from kohakuterrarium.api.auth.users import create_user, get_user_by_username
from kohakuterrarium.cli.admin import admin_cli

_TEST_ROUNDS = 4


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Redirect config + auth.db to a per-test tmp area."""
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    _reset_migration_state_for_tests()
    ensure_migrated()
    yield tmp_path
    _reset_migration_state_for_tests()


def _ns(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


# ---------------------------------------------------------------------------
# Token verbs
# ---------------------------------------------------------------------------


class TestSetHostToken:
    def test_writes_token_to_config_toml(self, cli_env):
        rc = admin_cli(_ns(admin_command="set-host-token"))
        assert rc == 0
        data = _read_config_toml()
        assert data["auth"]["host_token"]
        assert len(data["auth"]["host_token"]) == 64  # hex 32 bytes

    def test_preserves_other_auth_fields(self, cli_env):
        # Pre-seed an admin_token then rotate the host_token; admin must survive.
        cfg_path = _config_toml_path()
        cfg_path.write_text('[auth]\nadmin_token = "preexisting"\n', encoding="utf-8")
        admin_cli(_ns(admin_command="set-host-token"))
        data = _read_config_toml()
        assert data["auth"]["admin_token"] == "preexisting"
        assert "host_token" in data["auth"]


class TestSetAdminToken:
    def test_writes_admin_token(self, cli_env):
        rc = admin_cli(_ns(admin_command="set-admin-token"))
        assert rc == 0
        data = _read_config_toml()
        assert data["auth"]["admin_token"]


class TestShowHostToken:
    def test_without_yes_flag_refuses(self, cli_env, capsys):
        admin_cli(_ns(admin_command="set-host-token"))
        rc = admin_cli(_ns(admin_command="show-host-token", yes=False))
        assert rc == 1

    def test_with_yes_flag_prints(self, cli_env, capsys):
        admin_cli(_ns(admin_command="set-host-token"))
        capsys.readouterr()  # drain output from the set-host-token call
        rc = admin_cli(_ns(admin_command="show-host-token", yes=True))
        captured = capsys.readouterr()
        assert rc == 0
        assert len(captured.out.strip()) == 64

    def test_unset_token_returns_zero_with_message(self, cli_env, capsys):
        rc = admin_cli(_ns(admin_command="show-host-token", yes=True))
        captured = capsys.readouterr()
        assert rc == 0
        assert "(host_token is not set)" in captured.out


class TestRotateHostToken:
    def test_rotation_generates_distinct_token(self, cli_env):
        admin_cli(_ns(admin_command="set-host-token"))
        before = _read_config_toml()["auth"]["host_token"]
        admin_cli(_ns(admin_command="rotate-host-token"))
        after = _read_config_toml()["auth"]["host_token"]
        assert before != after


class TestConfigShapeError:
    """Config.toml in a shape the minimal writer refuses must produce
    a clean operator message, not a Python traceback (audit nit)."""

    def test_top_level_scalar_returns_2_with_message(self, cli_env, capsys):
        (cli_env / "config.toml").write_text(
            'version = 7\n\n[auth]\nhost_token = "x"\n', encoding="utf-8"
        )
        rc = admin_cli(_ns(admin_command="set-host-token"))
        captured = capsys.readouterr()
        assert rc == 2
        assert "config.toml" in captured.err
        assert "fix:" in captured.err
        # Original token preserved — write_auth_section's raise leaves
        # the file untouched.
        assert _read_config_toml()["auth"]["host_token"] == "x"


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


class TestUsersAdd:
    def test_creates_user(self, cli_env, monkeypatch, capsys):
        # Bypass the interactive getpass prompt.
        passwords = iter(["secret", "secret"])
        monkeypatch.setattr(
            "kohakuterrarium.cli.admin.getpass.getpass",
            lambda prompt="Password: ": next(passwords),
        )
        rc = admin_cli(
            _ns(
                admin_command="users",
                users_command="add",
                username="alice",
                role="user",
            )
        )
        assert rc == 0
        captured = capsys.readouterr()
        assert "user created" in captured.out
        with connection() as conn:
            assert get_user_by_username(conn, "alice") is not None

    def test_mismatched_passwords_aborts(self, cli_env, monkeypatch):
        passwords = iter(["one", "two"])
        monkeypatch.setattr(
            "kohakuterrarium.cli.admin.getpass.getpass",
            lambda prompt="Password: ": next(passwords),
        )
        rc = admin_cli(
            _ns(
                admin_command="users",
                users_command="add",
                username="alice",
                role="user",
            )
        )
        assert rc == 1
        with connection() as conn:
            assert get_user_by_username(conn, "alice") is None


class TestUsersList:
    def test_empty(self, cli_env, capsys):
        rc = admin_cli(_ns(admin_command="users", users_command="list"))
        assert rc == 0
        assert "(no users)" in capsys.readouterr().out

    def test_with_users(self, cli_env, capsys):
        with connection() as conn:
            create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            create_user(conn, "bob", "x", bcrypt_rounds=_TEST_ROUNDS)
        rc = admin_cli(_ns(admin_command="users", users_command="list"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "alice" in out
        assert "bob" in out


class TestUsersRoleAndActive:
    def test_grant_promotes(self, cli_env):
        with connection() as conn:
            create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
        # Need at least one admin so the last-admin guard doesn't fire.
        with connection() as conn:
            create_user(conn, "root", "x", role="admin", bcrypt_rounds=_TEST_ROUNDS)
        rc = admin_cli(
            _ns(
                admin_command="users",
                users_command="grant",
                username="alice",
            )
        )
        assert rc == 0
        with connection() as conn:
            updated = get_user_by_username(conn, "alice")
        assert updated.role == "admin"

    def test_demote_blocked_when_only_admin(self, cli_env, capsys):
        with connection() as conn:
            create_user(conn, "root", "x", role="admin", bcrypt_rounds=_TEST_ROUNDS)
        rc = admin_cli(
            _ns(
                admin_command="users",
                users_command="demote",
                username="root",
            )
        )
        assert rc == 1
        assert "only active admin" in capsys.readouterr().err

    def test_disable_drops_sessions(self, cli_env):
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
            from kohakuterrarium.api.auth.sessions import create_session

            create_session(conn, user.id, expire_hours=24)
        rc = admin_cli(
            _ns(
                admin_command="users",
                users_command="disable",
                username="alice",
            )
        )
        assert rc == 0
        with connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE user_id = ?", (user.id,)
            ).fetchone()
        assert row[0] == 0


class TestUsersDelete:
    def test_delete_with_yes_skips_prompt(self, cli_env, capsys):
        with connection() as conn:
            create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
        rc = admin_cli(
            _ns(
                admin_command="users",
                users_command="delete",
                username="alice",
                yes=True,
            )
        )
        assert rc == 0
        with connection() as conn:
            assert get_user_by_username(conn, "alice") is None

    def test_delete_only_admin_blocked(self, cli_env, capsys):
        with connection() as conn:
            create_user(conn, "root", "x", role="admin", bcrypt_rounds=_TEST_ROUNDS)
        rc = admin_cli(
            _ns(
                admin_command="users",
                users_command="delete",
                username="root",
                yes=True,
            )
        )
        assert rc == 1


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


class TestInvitations:
    def test_create_then_list(self, cli_env, capsys):
        rc = admin_cli(
            _ns(
                admin_command="invitations",
                inv_command="create",
                role="user",
                expires_in_hours=None,
            )
        )
        assert rc == 0
        # Plaintext token printed.
        first = capsys.readouterr().out
        assert "token:" in first

        rc = admin_cli(_ns(admin_command="invitations", inv_command="list"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "user" in out

    def test_revoke_unused(self, cli_env):
        from kohakuterrarium.api.auth import invitations as invitations_db

        with connection() as conn:
            _, invite = invitations_db.create(conn, created_by=None)
        rc = admin_cli(
            _ns(
                admin_command="invitations",
                inv_command="revoke",
                invite_id=invite.id,
            )
        )
        assert rc == 0


# ---------------------------------------------------------------------------
# Migrate
# ---------------------------------------------------------------------------


class TestMigrate:
    def test_migrates_ui_prefs_and_sessions(self, cli_env, tmp_path):
        cfg = Path(cli_env)
        # Seed shared state.
        (cfg / "ui_prefs.json").write_text('{"theme": "dark"}', encoding="utf-8")
        (cfg / "sessions").mkdir()
        (cfg / "sessions" / "alpha.kohakutr").write_bytes(b"hello-session")

        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)

        rc = admin_cli(
            _ns(
                admin_command="migrate",
                from_shared_state=True,
                to_user="alice",
                dry_run=False,
            )
        )
        assert rc == 0
        assert not (cfg / "ui_prefs.json").exists()
        assert not (cfg / "sessions" / "alpha.kohakutr").exists()
        dst_prefs = cfg / "users" / str(user.id) / "ui_prefs.json"
        dst_session = cfg / "users" / str(user.id) / "sessions" / "alpha.kohakutr"
        assert dst_prefs.is_file()
        assert dst_session.is_file()
        assert dst_session.read_bytes() == b"hello-session"

    def test_dry_run_does_not_move(self, cli_env, capsys):
        cfg = Path(cli_env)
        (cfg / "ui_prefs.json").write_text("{}", encoding="utf-8")
        with connection() as conn:
            create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
        rc = admin_cli(
            _ns(
                admin_command="migrate",
                from_shared_state=True,
                to_user="alice",
                dry_run=True,
            )
        )
        assert rc == 0
        assert (cfg / "ui_prefs.json").exists()  # untouched

    def test_unknown_user_404s(self, cli_env, capsys):
        rc = admin_cli(
            _ns(
                admin_command="migrate",
                from_shared_state=True,
                to_user="nobody",
                dry_run=False,
            )
        )
        assert rc == 1


# ---------------------------------------------------------------------------
# Argparse wiring + dispatch edge cases
# ---------------------------------------------------------------------------


class TestParserWiring:
    def test_add_admin_subparser_builds_clean(self):
        # The function only mutates a passed-in subparsers object;
        # build a fresh parser and verify the verbs land where the
        # dispatch table expects them.
        import argparse

        from kohakuterrarium.cli.admin import add_admin_subparser

        parser = argparse.ArgumentParser(prog="kt-test")
        sub = parser.add_subparsers(dest="command", required=False)
        add_admin_subparser(sub)
        # All top-level verbs parse without error.
        for verb in (
            "set-host-token",
            "set-admin-token",
            "rotate-host-token",
        ):
            ns = parser.parse_args(["admin", verb])
            assert ns.command == "admin"
            assert ns.admin_command == verb

    def test_no_admin_command_prints_help(self, cli_env, capsys):
        rc = admin_cli(_ns(admin_command=None))
        out = capsys.readouterr().out
        assert rc == 0
        assert "usage:" in out

    def test_unknown_verb_returns_2(self, cli_env, capsys):
        rc = admin_cli(_ns(admin_command="not-a-real-verb"))
        assert rc == 2

    def test_unknown_users_verb_returns_2(self, cli_env, capsys):
        rc = admin_cli(_ns(admin_command="users", users_command="ascend"))
        assert rc == 2

    def test_unknown_invitations_verb_returns_2(self, cli_env):
        rc = admin_cli(_ns(admin_command="invitations", inv_command="evict"))
        assert rc == 2

    def test_migrate_without_from_shared_state_returns_2(self, cli_env):
        # The only supported source today is --from-shared-state.
        rc = admin_cli(
            _ns(
                admin_command="migrate",
                from_shared_state=False,
                to_user="alice",
                dry_run=False,
            )
        )
        assert rc == 2


class TestUserVerbsErrorPaths:
    def test_grant_unknown_user_fails(self, cli_env):
        rc = admin_cli(
            _ns(
                admin_command="users",
                users_command="grant",
                username="ghost",
            )
        )
        assert rc == 1

    def test_disable_unknown_user_fails(self, cli_env):
        rc = admin_cli(
            _ns(
                admin_command="users",
                users_command="disable",
                username="ghost",
            )
        )
        assert rc == 1

    def test_delete_unknown_user_fails(self, cli_env):
        rc = admin_cli(
            _ns(
                admin_command="users",
                users_command="delete",
                username="ghost",
                yes=True,
            )
        )
        assert rc == 1

    def test_add_invalid_username(self, cli_env, monkeypatch, capsys):
        passwords = iter(["x", "x"])
        monkeypatch.setattr(
            "kohakuterrarium.cli.admin.getpass.getpass",
            lambda prompt="Password: ": next(passwords),
        )
        rc = admin_cli(
            _ns(
                admin_command="users",
                users_command="add",
                username="has space",
                role="user",
            )
        )
        assert rc == 1

    def test_enable_user(self, cli_env, monkeypatch):
        passwords = iter(["x", "x"])
        monkeypatch.setattr(
            "kohakuterrarium.cli.admin.getpass.getpass",
            lambda prompt="Password: ": next(passwords),
        )
        # Add then disable then re-enable.
        admin_cli(
            _ns(
                admin_command="users",
                users_command="add",
                username="alice",
                role="user",
            )
        )
        admin_cli(
            _ns(
                admin_command="users",
                users_command="disable",
                username="alice",
            )
        )
        rc = admin_cli(
            _ns(
                admin_command="users",
                users_command="enable",
                username="alice",
            )
        )
        assert rc == 0


class TestInvitationsRevoke:
    def test_revoke_missing_returns_1(self, cli_env):
        rc = admin_cli(
            _ns(
                admin_command="invitations",
                inv_command="revoke",
                invite_id=9999,
            )
        )
        assert rc == 1


class TestDeleteWithoutYes:
    def test_aborts_when_confirm_mismatches(self, cli_env, monkeypatch, capsys):
        with connection() as conn:
            create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
        monkeypatch.setattr("builtins.input", lambda prompt="": "not-alice")
        rc = admin_cli(
            _ns(
                admin_command="users",
                users_command="delete",
                username="alice",
                yes=False,
            )
        )
        assert rc == 1
        assert get_user_by_username_via_conn("alice") is not None


def get_user_by_username_via_conn(username):
    """Helper — opens a fresh connection so the test doesn't leak one."""
    with connection() as conn:
        return get_user_by_username(conn, username)
