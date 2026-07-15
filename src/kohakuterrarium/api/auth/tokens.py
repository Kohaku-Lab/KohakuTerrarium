"""Create, resolve, list, and revoke user API tokens.

Plaintext exists only at creation; persistence stores a SHA3-512 digest so database
contents cannot be replayed directly as bearer credentials.
"""

import sqlite3
from datetime import datetime, timezone

from kohakuterrarium.api.auth.crypto import (
    generate_token,
    hash_token,
)
from kohakuterrarium.api.auth.models import (
    ApiToken,
    User,
    api_token_from_row,
    user_from_row,
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_token(
    conn: sqlite3.Connection, user_id: int, name: str
) -> tuple[str, ApiToken]:
    """Create a token and return its one-time plaintext with public metadata."""
    cleaned_name = (name or "").strip()
    if not cleaned_name:
        raise ValueError("token name must not be empty")
    plaintext = generate_token()
    token_hash = hash_token(plaintext)
    created_at = _iso_now()
    cur = conn.execute(
        "INSERT INTO api_tokens(user_id, token_hash, name, last_used_at, created_at) "
        "VALUES (?, ?, ?, NULL, ?)",
        (user_id, token_hash, cleaned_name, created_at),
    )
    conn.commit()
    return (
        plaintext,
        ApiToken(
            id=int(cur.lastrowid),
            user_id=user_id,
            name=cleaned_name,
            last_used_at=None,
            created_at=created_at,
        ),
    )


def get_token_user(conn: sqlite3.Connection, plaintext: str) -> User | None:
    """Resolve an active token owner and best-effort refresh usage metadata."""
    if not plaintext:
        return None
    token_hash = hash_token(plaintext)
    row = conn.execute(
        """
        SELECT u.id, u.username, u.role, u.is_active,
               u.created_at, u.last_login_at, t.id AS token_id
        FROM api_tokens t
        JOIN users u ON u.id = t.user_id
        WHERE t.token_hash = ?
        """,
        (token_hash,),
    ).fetchone()
    if row is None:
        return None
    if not row["is_active"]:
        return None
    # Usage telemetry must not invalidate an otherwise valid credential lookup.
    try:
        conn.execute(
            "UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
            (_iso_now(), int(row["token_id"])),
        )
        conn.commit()
    except sqlite3.Error:
        pass
    return user_from_row(row)


def list_user_tokens(conn: sqlite3.Connection, user_id: int) -> list[ApiToken]:
    rows = conn.execute(
        "SELECT id, user_id, name, last_used_at, created_at "
        "FROM api_tokens WHERE user_id = ? ORDER BY id",
        (user_id,),
    ).fetchall()
    return [t for t in (api_token_from_row(r) for r in rows) if t is not None]


def delete_token(conn: sqlite3.Connection, user_id: int, token_id: int) -> bool:
    """Revoke a token only when it belongs to the supplied user ID."""
    cur = conn.execute(
        "DELETE FROM api_tokens WHERE id = ? AND user_id = ?",
        (token_id, user_id),
    )
    conn.commit()
    return cur.rowcount > 0


def delete_token_admin(conn: sqlite3.Connection, token_id: int) -> bool:
    """Revoke a token without applying owner scope."""
    cur = conn.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))
    conn.commit()
    return cur.rowcount > 0


__all__ = [
    "create_token",
    "delete_token",
    "delete_token_admin",
    "get_token_user",
    "list_user_tokens",
]
