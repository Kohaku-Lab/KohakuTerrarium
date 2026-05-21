"""User CRUD — raw sqlite, no ORM.

Every function takes an explicit ``sqlite3.Connection`` so the caller
controls the transaction boundary.  All writes commit on success;
read-only helpers don't.

Username case-folding: stored as-typed for display, but uniqueness is
enforced via ``LOWER(username)`` lookups so ``Alice`` and ``alice``
can't both register.  KohakuHub does the same.
"""

import re
import sqlite3
from datetime import datetime, timezone
from typing import Iterable

from kohakuterrarium.api.auth.crypto import hash_password, verify_password
from kohakuterrarium.api.auth.models import User, user_from_row

_VALID_ROLES: frozenset[str] = frozenset({"user", "admin"})
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{2,64}$")


class UserError(ValueError):
    """Base for user-CRUD errors raised by this module."""


class UsernameInUseError(UserError):
    """Raised when a username collision blocks registration."""


class InvalidUsernameError(UserError):
    """Raised when a username doesn't match the documented charset."""


class UserNotFoundError(UserError):
    """Raised when a CRUD op targets a missing user."""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_role(role: str) -> str:
    if role not in _VALID_ROLES:
        raise UserError(
            f"invalid role: {role!r}; expected one of {sorted(_VALID_ROLES)}"
        )
    return role


def validate_username(username: str) -> str:
    """Strip + validate; raise :class:`InvalidUsernameError` on bad input."""
    cleaned = (username or "").strip()
    if not _USERNAME_RE.match(cleaned):
        raise InvalidUsernameError(
            f"username must match {_USERNAME_RE.pattern!r}; got {cleaned!r}"
        )
    return cleaned


def create_user(
    conn: sqlite3.Connection,
    username: str,
    password: str,
    *,
    role: str = "user",
    bcrypt_rounds: int = 12,
) -> User:
    """Insert a new user.  Returns the freshly-created :class:`User`.

    Validates the username charset and uniqueness BEFORE hashing the
    password (bcrypt is slow; failing fast saves CPU on bad-username
    inputs).
    """
    cleaned = validate_username(username)
    role_clean = _normalize_role(role)
    # Case-insensitive uniqueness check.
    existing = conn.execute(
        "SELECT 1 FROM users WHERE LOWER(username) = LOWER(?)", (cleaned,)
    ).fetchone()
    if existing:
        raise UsernameInUseError(f"username already taken: {cleaned!r}")
    if not password:
        raise UserError("password must not be empty")

    pw_hash = hash_password(password, rounds=bcrypt_rounds)
    created_at = _iso_now()
    cur = conn.execute(
        "INSERT INTO users(username, password_hash, role, is_active, created_at) "
        "VALUES (?, ?, ?, 1, ?)",
        (cleaned, pw_hash, role_clean, created_at),
    )
    conn.commit()
    return User(
        id=int(cur.lastrowid),
        username=cleaned,
        role=role_clean,
        is_active=True,
        created_at=created_at,
        last_login_at=None,
    )


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute(
        "SELECT id, username, role, is_active, created_at, last_login_at "
        "FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    return user_from_row(row)


def get_user_by_username(conn: sqlite3.Connection, username: str) -> User | None:
    row = conn.execute(
        "SELECT id, username, role, is_active, created_at, last_login_at "
        "FROM users WHERE LOWER(username) = LOWER(?)",
        ((username or "").strip(),),
    ).fetchone()
    return user_from_row(row)


def verify_user_password(
    conn: sqlite3.Connection, username: str, password: str
) -> User | None:
    """Look up + verify in one shot.  Returns the :class:`User` on
    match, ``None`` otherwise (any reason — missing user, wrong
    password, inactive)."""
    row = conn.execute(
        "SELECT id, username, password_hash, role, is_active, "
        "created_at, last_login_at FROM users "
        "WHERE LOWER(username) = LOWER(?)",
        ((username or "").strip(),),
    ).fetchone()
    if row is None:
        return None
    if not row["is_active"]:
        return None
    if not verify_password(password, str(row["password_hash"])):
        return None
    return user_from_row(row)


def list_users(conn: sqlite3.Connection) -> list[User]:
    rows = conn.execute(
        "SELECT id, username, role, is_active, created_at, last_login_at "
        "FROM users ORDER BY id"
    ).fetchall()
    return [u for u in (user_from_row(r) for r in rows) if u is not None]


def set_password(
    conn: sqlite3.Connection,
    user_id: int,
    new_password: str,
    *,
    bcrypt_rounds: int = 12,
) -> None:
    if not new_password:
        raise UserError("password must not be empty")
    if get_user_by_id(conn, user_id) is None:
        raise UserNotFoundError(f"user id={user_id} not found")
    pw_hash = hash_password(new_password, rounds=bcrypt_rounds)
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user_id))
    conn.commit()


def set_role(conn: sqlite3.Connection, user_id: int, role: str) -> None:
    role_clean = _normalize_role(role)
    if get_user_by_id(conn, user_id) is None:
        raise UserNotFoundError(f"user id={user_id} not found")
    conn.execute("UPDATE users SET role = ? WHERE id = ?", (role_clean, user_id))
    conn.commit()


def set_active(conn: sqlite3.Connection, user_id: int, is_active: bool) -> None:
    if get_user_by_id(conn, user_id) is None:
        raise UserNotFoundError(f"user id={user_id} not found")
    conn.execute(
        "UPDATE users SET is_active = ? WHERE id = ?",
        (1 if is_active else 0, user_id),
    )
    conn.commit()


def touch_last_login(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute(
        "UPDATE users SET last_login_at = ? WHERE id = ?",
        (_iso_now(), user_id),
    )
    conn.commit()


def delete_user(conn: sqlite3.Connection, user_id: int) -> bool:
    """Returns True if a row was deleted, False if nothing matched."""
    cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    return cur.rowcount > 0


def count_admins(conn: sqlite3.Connection) -> int:
    """Number of currently-active admins.  Used to prevent the only
    admin from disabling / demoting themselves and locking the host."""
    row = conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
    ).fetchone()
    return int(row[0]) if row else 0


__all__: Iterable[str] = (
    "InvalidUsernameError",
    "UserError",
    "UserNotFoundError",
    "UsernameInUseError",
    "count_admins",
    "create_user",
    "delete_user",
    "get_user_by_id",
    "get_user_by_username",
    "list_users",
    "set_active",
    "set_password",
    "set_role",
    "touch_last_login",
    "validate_username",
    "verify_user_password",
)
