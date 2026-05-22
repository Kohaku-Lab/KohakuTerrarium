"""``kt admin`` — operator administration verbs.

All ``kt admin`` commands run locally and write to ``<config_dir>/`` (or
the auth.db) directly; they do NOT require the server to be running.
This keeps the bootstrap story simple: the operator sets up auth
secrets, creates the first admin user, then starts the server.

Verb surface:

| Verb | What |
|---|---|
| ``set-host-token``        | Generate + save host_token (32 random bytes) |
| ``set-admin-token``       | Generate + save admin_token |
| ``show-host-token``       | Print current host_token (gated behind ``--yes``) |
| ``rotate-host-token``     | Generate new, save, hint about restarting |
| ``users add``             | Interactive password prompt; sets role |
| ``users list``            | Tabular list of all users |
| ``users grant``           | Promote a user to admin |
| ``users disable``         | Set ``is_active=0`` |
| ``users delete``          | Drop the user row (cascades sessions + tokens) |
| ``invitations create``    | Generate an invite token |
| ``invitations list``      | List unused invitations |
| ``invitations revoke``    | Drop an unused invitation |
| ``migrate``               | Move shared-state UI prefs / sessions into a user dir |

Output is plain text (no rich tables) so the command is friendly for
SSH sessions / systemd journal grep / Docker exec.
"""

import argparse
import getpass
import secrets
import shutil
import sys
from pathlib import Path

from kohakuterrarium.api.auth import invitations as invitations_db
from kohakuterrarium.api.auth import users as users_db
from kohakuterrarium.api.auth.config import load_auth_config
from kohakuterrarium.api.auth.config_write import (
    config_toml_path,
    write_auth_section,
)
from kohakuterrarium.api.auth.db import connection, ensure_migrated
from kohakuterrarium.api.auth.namespace import (
    shared_session_dir,
    shared_ui_prefs_path,
    user_config_dir,
    user_ui_prefs_path,
    user_session_dir,
)
from kohakuterrarium.api.auth.sessions import delete_user_sessions
from kohakuterrarium.cli.admin_qr import show_host_qr
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def add_admin_subparser(subparsers: argparse._SubParsersAction) -> None:
    """Mount the ``kt admin`` parser tree."""
    admin = subparsers.add_parser(
        "admin",
        help="Operator administration — auth secrets, users, invitations, migrations",
    )
    admin_sub = admin.add_subparsers(dest="admin_command", required=False)

    # Token verbs --------------------------------------------------------
    admin_sub.add_parser(
        "set-host-token",
        help="Generate + save a new host_token in <config_dir>/config.toml",
    )
    admin_sub.add_parser(
        "set-admin-token",
        help="Generate + save a new admin_token in <config_dir>/config.toml",
    )
    show = admin_sub.add_parser(
        "show-host-token",
        help="Print the current host_token to stdout (requires --yes)",
    )
    show.add_argument(
        "--yes",
        action="store_true",
        help="Confirm you want to see the token in cleartext",
    )
    admin_sub.add_parser(
        "rotate-host-token",
        help="Generate + save a new host_token; existing clients lose access",
    )

    qr = admin_sub.add_parser(
        "show-host-qr",
        help=(
            "Print a QR code (ASCII art) that mobile clients scan to "
            "pair with this host.  Encodes the host URL + host_token "
            "as a ``ktconnect://`` URI."
        ),
    )
    qr.add_argument(
        "--url",
        default="",
        help=(
            "Public URL of this host (e.g. ``https://kt.home.lan:8001``). "
            "Defaults to ``http://<lan-ip>:8001`` when omitted; lookup is "
            "best-effort and may not pick the right interface on multi-NIC "
            "hosts — pass --url explicitly if so."
        ),
    )
    qr.add_argument(
        "--yes",
        action="store_true",
        help="Confirm you want to print the host_token in plaintext via QR",
    )

    # Users --------------------------------------------------------------
    users = admin_sub.add_parser("users", help="User account management")
    users_sub = users.add_subparsers(dest="users_command", required=True)
    add_user = users_sub.add_parser("add", help="Create a new user")
    add_user.add_argument("username")
    add_user.add_argument(
        "--role",
        choices=["user", "admin"],
        default="user",
        help="Role for the new user (default: user)",
    )
    users_sub.add_parser("list", help="List all users")
    grant = users_sub.add_parser("grant", help="Promote a user to admin")
    grant.add_argument("username")
    demote = users_sub.add_parser("demote", help="Demote an admin to user")
    demote.add_argument("username")
    disable = users_sub.add_parser(
        "disable", help="Disable a user (sessions are revoked)"
    )
    disable.add_argument("username")
    enable = users_sub.add_parser("enable", help="Re-enable a disabled user")
    enable.add_argument("username")
    delete = users_sub.add_parser(
        "delete", help="Delete a user (cascades sessions + API tokens)"
    )
    delete.add_argument("username")
    delete.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt",
    )

    # Invitations --------------------------------------------------------
    invitations = admin_sub.add_parser(
        "invitations", help="Invitation token management"
    )
    inv_sub = invitations.add_subparsers(dest="inv_command", required=True)
    create = inv_sub.add_parser("create", help="Create a new invitation")
    create.add_argument(
        "--role",
        choices=["user", "admin"],
        default="user",
        help="Role for the user who consumes this invitation",
    )
    create.add_argument(
        "--expires-in-hours",
        type=int,
        default=None,
        help="Invitation expires after this many hours (default: never)",
    )
    inv_sub.add_parser("list", help="List unused invitations")
    revoke = inv_sub.add_parser("revoke", help="Revoke an unused invitation by id")
    revoke.add_argument("invite_id", type=int)

    # Migrate ------------------------------------------------------------
    migrate = admin_sub.add_parser(
        "migrate",
        help="Move shared-state UI prefs / sessions into a user namespace",
    )
    migrate.add_argument(
        "--from-shared-state",
        action="store_true",
        help="Migrate the shared <config_dir>/ui_prefs.json + sessions/ dir",
    )
    migrate.add_argument(
        "--to-user",
        required=True,
        help="Target username (must exist in auth.db)",
    )
    migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be moved without touching the filesystem",
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def admin_cli(args: argparse.Namespace) -> int:
    """Top-level dispatch for ``kt admin <verb>``."""
    verb = getattr(args, "admin_command", None)
    if verb is None:
        print("usage: kt admin <command> ...")
        print(
            "commands: set-host-token, set-admin-token, show-host-token, "
            "rotate-host-token, users, invitations, migrate"
        )
        return 0
    if verb == "set-host-token":
        return _set_host_token(rotate=False)
    if verb == "set-admin-token":
        return _set_admin_token()
    if verb == "show-host-token":
        return _show_host_token(args.yes)
    if verb == "rotate-host-token":
        return _set_host_token(rotate=True)
    if verb == "show-host-qr":
        return show_host_qr(getattr(args, "url", ""), getattr(args, "yes", False))
    if verb == "users":
        return _dispatch_users(args)
    if verb == "invitations":
        return _dispatch_invitations(args)
    if verb == "migrate":
        return _migrate(args)
    print(f"unknown admin verb: {verb}", file=sys.stderr)
    return 2


# ---------------------------------------------------------------------------
# Token verbs — delegate to :mod:`api.auth.config_write` so the CLI and the
# admin-rotation API routes write the same TOML.
# ---------------------------------------------------------------------------


def _write_token_or_complain(field: str, value: str) -> int:
    """Wrap ``write_auth_section`` to translate ValueError into a
    clean operator-facing error instead of a raw Python traceback.

    Returns 0 on success, 2 on operator-fixable config error so
    shell scripts can react to the bad-config case distinctly from
    the success path.
    """
    try:
        write_auth_section({field: value})
    except ValueError as e:
        print(
            "error: config.toml contains a TOML shape the minimal "
            "writer cannot preserve (top-level scalar or nested table).",
            file=sys.stderr,
        )
        print(f"writer detail: {e}", file=sys.stderr)
        print(
            "fix: move any top-level keys into a [section] and re-run.",
            file=sys.stderr,
        )
        return 2
    return 0


def _set_host_token(*, rotate: bool) -> int:
    token = secrets.token_hex(32)
    rc = _write_token_or_complain("host_token", token)
    if rc != 0:
        return rc
    action = "rotated" if rotate else "saved"
    print(f"host_token {action} (length {len(token)} chars).")
    print(f"written to: {config_toml_path()}")
    if rotate:
        print(
            "note: existing clients will lose access on next request — "
            "restart any running 'kt serve' to pick up the new token."
        )
    return 0


def _set_admin_token() -> int:
    token = secrets.token_hex(32)
    rc = _write_token_or_complain("admin_token", token)
    if rc != 0:
        return rc
    print(f"admin_token saved (length {len(token)} chars).")
    print(f"written to: {config_toml_path()}")
    return 0


def _show_host_token(yes: bool) -> int:
    if not yes:
        print(
            "this command prints the host_token in cleartext.",
            file=sys.stderr,
        )
        print(
            "re-run with --yes if you really want to see it.",
            file=sys.stderr,
        )
        return 1
    cfg = load_auth_config()
    if not cfg.host_token:
        print("(host_token is not set)")
        return 0
    print(cfg.host_token)
    return 0


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def _ensure_db() -> None:
    """Apply pending migrations before any DB op.  Idempotent."""
    ensure_migrated()


def _read_password_twice(prompt: str = "Password: ") -> str | None:
    """Prompt twice; return the password or ``None`` if mismatched."""
    pw1 = getpass.getpass(prompt)
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("passwords don't match — aborting.", file=sys.stderr)
        return None
    if not pw1:
        print("password must not be empty — aborting.", file=sys.stderr)
        return None
    return pw1


def _dispatch_users(args: argparse.Namespace) -> int:
    cmd = args.users_command
    if cmd == "add":
        return _users_add(args.username, args.role)
    if cmd == "list":
        return _users_list()
    if cmd == "grant":
        return _users_set_role(args.username, "admin")
    if cmd == "demote":
        return _users_set_role(args.username, "user")
    if cmd == "disable":
        return _users_set_active(args.username, False)
    if cmd == "enable":
        return _users_set_active(args.username, True)
    if cmd == "delete":
        return _users_delete(args.username, args.yes)
    print(f"unknown users command: {cmd}", file=sys.stderr)
    return 2


def _users_add(username: str, role: str) -> int:
    _ensure_db()
    password = _read_password_twice()
    if password is None:
        return 1
    cfg = load_auth_config()
    try:
        with connection() as conn:
            user = users_db.create_user(
                conn,
                username,
                password,
                role=role,
                bcrypt_rounds=cfg.bcrypt_rounds,
            )
    except users_db.UsernameInUseError as e:
        print(str(e), file=sys.stderr)
        return 1
    except users_db.InvalidUsernameError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"user created: id={user.id} username={user.username} role={user.role}")
    return 0


def _users_list() -> int:
    _ensure_db()
    with connection() as conn:
        users = users_db.list_users(conn)
    if not users:
        print("(no users)")
        return 0
    # Plain text table, fixed-width.
    print(f"{'ID':<4}  {'USERNAME':<24}  {'ROLE':<8}  {'ACTIVE':<8}  LAST_LOGIN")
    print("-" * 70)
    for u in users:
        print(
            f"{u.id:<4}  {u.username:<24}  {u.role:<8}  "
            f"{'yes' if u.is_active else 'no':<8}  "
            f"{u.last_login_at or '-'}"
        )
    return 0


def _users_set_role(username: str, role: str) -> int:
    _ensure_db()
    with connection() as conn:
        user = users_db.get_user_by_username(conn, username)
        if user is None:
            print(f"user not found: {username}", file=sys.stderr)
            return 1
        # Last-admin guard: refuse to demote the only active admin.
        if (
            user.role == "admin"
            and role != "admin"
            and users_db.count_admins(conn) <= 1
        ):
            print(
                "refusing: this is the only active admin — promote another "
                "user first.",
                file=sys.stderr,
            )
            return 1
        users_db.set_role(conn, user.id, role)
    print(f"user {username!r} role set to {role}")
    return 0


def _users_set_active(username: str, is_active: bool) -> int:
    _ensure_db()
    with connection() as conn:
        user = users_db.get_user_by_username(conn, username)
        if user is None:
            print(f"user not found: {username}", file=sys.stderr)
            return 1
        if user.role == "admin" and not is_active and users_db.count_admins(conn) <= 1:
            print(
                "refusing: this is the only active admin.",
                file=sys.stderr,
            )
            return 1
        users_db.set_active(conn, user.id, is_active)
        if not is_active:
            # Nuke all sessions for a disabled user — keeps them out
            # immediately rather than waiting for cookie expiry.
            removed = delete_user_sessions(conn, user.id)
            if removed:
                print(f"  (dropped {removed} active session(s))")
    state = "enabled" if is_active else "disabled"
    print(f"user {username!r} {state}")
    return 0


def _users_delete(username: str, yes: bool) -> int:
    _ensure_db()
    with connection() as conn:
        user = users_db.get_user_by_username(conn, username)
        if user is None:
            print(f"user not found: {username}", file=sys.stderr)
            return 1
        if user.role == "admin" and users_db.count_admins(conn) <= 1:
            print(
                "refusing: this is the only active admin.",
                file=sys.stderr,
            )
            return 1
    if not yes:
        confirm = input(
            f"delete user {username!r}? this cascades sessions + tokens. "
            "type the username to confirm: "
        )
        if confirm.strip() != username:
            print("aborted.")
            return 1
    with connection() as conn:
        users_db.delete_user(conn, user.id)
    print(f"user {username!r} deleted (id={user.id}).")
    # Note: per-user dir on disk is left intact.  Removing it is a
    # separate operator decision because it carries the user's data.
    nspace = user_config_dir(user.id)
    if nspace.exists():
        print(
            f"note: per-user dir {nspace} kept (rm -rf to discard the "
            "user's sessions / prefs)."
        )
    return 0


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------


def _dispatch_invitations(args: argparse.Namespace) -> int:
    cmd = args.inv_command
    if cmd == "create":
        return _invitations_create(args.role, args.expires_in_hours)
    if cmd == "list":
        return _invitations_list()
    if cmd == "revoke":
        return _invitations_revoke(args.invite_id)
    print(f"unknown invitations command: {cmd}", file=sys.stderr)
    return 2


def _invitations_create(role: str, expires_in_hours: int | None) -> int:
    _ensure_db()
    with connection() as conn:
        plaintext, invite = invitations_db.create(
            conn,
            created_by=None,  # CLI-issued — no actor user id
            role=role,
            expires_in_hours=expires_in_hours,
        )
    print(f"invitation created (id={invite.id}, role={invite.role}):")
    print(f"  token: {plaintext}")
    if invite.expires_at:
        print(f"  expires_at: {invite.expires_at}")
    else:
        print("  expires_at: never")
    print()
    print("share this token with the user — it grants one-shot registration.")
    return 0


def _invitations_list() -> int:
    _ensure_db()
    with connection() as conn:
        unused = invitations_db.list_unused(conn)
    if not unused:
        print("(no unused invitations)")
        return 0
    print(f"{'ID':<4}  {'ROLE':<8}  {'EXPIRES_AT':<32}  CREATED_AT")
    print("-" * 70)
    for inv in unused:
        print(
            f"{inv.id:<4}  {inv.role:<8}  "
            f"{(inv.expires_at or '-'):<32}  {inv.created_at}"
        )
    return 0


def _invitations_revoke(invite_id: int) -> int:
    _ensure_db()
    with connection() as conn:
        ok = invitations_db.revoke(conn, invite_id)
    if not ok:
        print(f"invitation not found or already used: id={invite_id}", file=sys.stderr)
        return 1
    print(f"invitation revoked: id={invite_id}")
    return 0


# ---------------------------------------------------------------------------
# Migrate shared-state → user namespace
# ---------------------------------------------------------------------------


def _migrate(args: argparse.Namespace) -> int:
    if not args.from_shared_state:
        print(
            "this verb currently supports only --from-shared-state",
            file=sys.stderr,
        )
        return 2
    _ensure_db()
    with connection() as conn:
        target = users_db.get_user_by_username(conn, args.to_user)
        if target is None:
            print(f"user not found: {args.to_user}", file=sys.stderr)
            return 1

    src_ui_prefs = shared_ui_prefs_path()
    src_sessions = shared_session_dir()
    dst_ui_prefs = user_ui_prefs_path(target.id)
    dst_sessions = user_session_dir(target.id)

    plan: list[tuple[str, Path, Path]] = []
    if src_ui_prefs.is_file():
        plan.append(("ui_prefs.json", src_ui_prefs, dst_ui_prefs))
    if src_sessions.is_dir():
        for session_file in sorted(src_sessions.glob("*.kohakutr")):
            plan.append(
                (
                    f"session: {session_file.name}",
                    session_file,
                    dst_sessions / session_file.name,
                )
            )

    if not plan:
        print(f"nothing to migrate from {shared_ui_prefs_path().parent}")
        return 0

    for label, src, dst in plan:
        if dst.exists():
            print(f"  skip {label}: target already exists at {dst}")
            continue
        if args.dry_run:
            print(f"  would move {label}: {src} -> {dst}")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            print(f"  moved {label} -> {dst}")
    if args.dry_run:
        print("(dry-run; nothing actually changed)")
    return 0


__all__ = ["add_admin_subparser", "admin_cli"]
