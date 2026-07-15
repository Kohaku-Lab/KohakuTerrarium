"""Convert authentication rows into immutable application-facing records.

The records intentionally omit stored credential hashes so database-only secrets do
not cross into route and dependency layers.
"""

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class User:
    """Expose user identity and status without the stored password hash."""

    id: int
    username: str
    role: str
    is_active: bool
    created_at: str
    last_login_at: str | None


def user_from_row(row: sqlite3.Row | None) -> User | None:
    """Convert a SQLite row to a user while preserving a missing result."""
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
    """Expose API-token metadata without the stored token digest."""

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
