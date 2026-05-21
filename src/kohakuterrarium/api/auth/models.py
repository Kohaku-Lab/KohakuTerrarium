"""Lightweight dataclasses for the auth tables.

No ORM — raw sqlite tuples are converted via these helpers.  Frozen
so handlers can pass them around without worrying about accidental
mutation.
"""

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    """Application-facing user dataclass.

    Mirrors the ``users`` table but EXCLUDES ``password_hash`` — that
    field never leaves the DB layer.  Routes / dependencies hand off a
    ``User`` to handlers; nobody outside :mod:`api.auth.users` should
    see the password hash.
    """

    id: int
    username: str
    role: str
    is_active: bool
    created_at: str
    last_login_at: str | None


def user_from_row(row: sqlite3.Row | None) -> User | None:
    """Translate a sqlite Row → :class:`User`; ``None`` passes through."""
    if row is None:
        return None
    return User(
        id=int(row["id"]),
        username=str(row["username"]),
        role=str(row["role"]),
        is_active=bool(row["is_active"]),
        created_at=str(row["created_at"]),
        last_login_at=(
            str(row["last_login_at"]) if row["last_login_at"] is not None else None
        ),
    )


@dataclass(frozen=True)
class ApiToken:
    """API-token row WITHOUT the hash — same hiding rule as :class:`User`."""

    id: int
    user_id: int
    name: str
    last_used_at: str | None
    created_at: str


def api_token_from_row(row: sqlite3.Row | None) -> ApiToken | None:
    if row is None:
        return None
    return ApiToken(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        name=str(row["name"]),
        last_used_at=(
            str(row["last_used_at"]) if row["last_used_at"] is not None else None
        ),
        created_at=str(row["created_at"]),
    )


__all__ = ["ApiToken", "User", "api_token_from_row", "user_from_row"]
