"""Invitation token CRUD.

Each invitation is single-use: ``used_by`` + ``used_at`` are set
atomically when consumed via :func:`consume`.  Optional ``expires_at``
clamps the validity window.

Like API tokens, the plaintext is shown once at creation; the DB
stores only the SHA3-512 hash so a DB leak can't be replayed.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from kohakuterrarium.api.auth.crypto import (
    generate_token,
    hash_invitation_token,
)


@dataclass(frozen=True)
class Invitation:
    id: int
    created_by: int | None
    role: str
    expires_at: str | None
    used_by: int | None
    used_at: str | None
    created_at: str


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _invite_from_row(row: sqlite3.Row | None) -> Invitation | None:
    if row is None:
        return None
    return Invitation(
        id=int(row["id"]),
        created_by=int(row["created_by"]) if row["created_by"] is not None else None,
        role=str(row["role"]),
        expires_at=str(row["expires_at"]) if row["expires_at"] is not None else None,
        used_by=int(row["used_by"]) if row["used_by"] is not None else None,
        used_at=str(row["used_at"]) if row["used_at"] is not None else None,
        created_at=str(row["created_at"]),
    )


def create(
    conn: sqlite3.Connection,
    *,
    created_by: int | None,
    role: str = "user",
    expires_in_hours: int | None = None,
) -> tuple[str, Invitation]:
    """Generate + store a new invitation.  Returns ``(plaintext, row)``."""
    if role not in {"user", "admin"}:
        raise ValueError(f"invalid role: {role!r}")
    plaintext = generate_token()
    token_hash = hash_invitation_token(plaintext)
    expires_at = (
        (datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)).isoformat()
        if expires_in_hours
        else None
    )
    created_at = _iso_now()
    cur = conn.execute(
        "INSERT INTO invitations(token_hash, created_by, role, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (token_hash, created_by, role, expires_at, created_at),
    )
    conn.commit()
    invite = Invitation(
        id=int(cur.lastrowid),
        created_by=created_by,
        role=role,
        expires_at=expires_at,
        used_by=None,
        used_at=None,
        created_at=created_at,
    )
    return plaintext, invite


def list_unused(conn: sqlite3.Connection) -> list[Invitation]:
    rows = conn.execute(
        "SELECT id, token_hash, created_by, role, expires_at, used_by, used_at, created_at "
        "FROM invitations WHERE used_by IS NULL ORDER BY id"
    ).fetchall()
    return [i for i in (_invite_from_row(r) for r in rows) if i is not None]


def peek(conn: sqlite3.Connection, plaintext: str) -> Invitation | None:
    """Look up an invitation by plaintext WITHOUT consuming.

    Returns the row when it exists, is unused, and not expired.
    Otherwise ``None``.  Used by registration so we can validate the
    invitation BEFORE creating the user — atomically consuming
    after the user exists.
    """
    if not plaintext:
        return None
    token_hash = hash_invitation_token(plaintext)
    now = _iso_now()
    row = conn.execute(
        """
        SELECT id, token_hash, created_by, role, expires_at, used_by, used_at, created_at
        FROM invitations
        WHERE token_hash = ?
          AND used_by IS NULL
          AND (expires_at IS NULL OR expires_at > ?)
        """,
        (token_hash, now),
    ).fetchone()
    return _invite_from_row(row)


def consume(
    conn: sqlite3.Connection, plaintext: str, *, used_by: int
) -> Invitation | None:
    """Atomically claim an invitation.

    Returns the consumed :class:`Invitation` on success, ``None``
    when:

    - Token doesn't match anything in the DB.
    - Token already consumed (``used_by`` non-null).
    - Token expired.

    Uses a conditional UPDATE so two simultaneous registers can't both
    succeed against the same invitation.
    """
    if not plaintext:
        return None
    token_hash = hash_invitation_token(plaintext)
    now = _iso_now()
    # Atomic claim — only succeeds if used_by IS NULL and not expired.
    cur = conn.execute(
        """
        UPDATE invitations
           SET used_by = ?, used_at = ?
         WHERE token_hash = ?
           AND used_by IS NULL
           AND (expires_at IS NULL OR expires_at > ?)
        """,
        (used_by, now, token_hash, now),
    )
    if cur.rowcount == 0:
        conn.commit()
        return None
    conn.commit()
    row = conn.execute(
        "SELECT id, token_hash, created_by, role, expires_at, used_by, used_at, created_at "
        "FROM invitations WHERE token_hash = ?",
        (token_hash,),
    ).fetchone()
    return _invite_from_row(row)


def revoke(conn: sqlite3.Connection, invite_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM invitations WHERE id = ? AND used_by IS NULL",
        (invite_id,),
    )
    conn.commit()
    return cur.rowcount > 0


__all__ = ["Invitation", "consume", "create", "list_unused", "peek", "revoke"]
