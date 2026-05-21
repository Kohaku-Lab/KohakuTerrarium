"""Session-cookie CRUD.

Session ID = ``secrets.token_urlsafe(32)`` (256 bits).  Stored
verbatim in the cookie + DB.  Unlike API tokens, session IDs are NOT
hashed — they're short-lived (default 168h), high-entropy, and rotated
on every login.  Hashing would prevent us from rotating ``last_seen``
without recomputing the hash on every request.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from kohakuterrarium.api.auth.crypto import generate_session_id
from kohakuterrarium.api.auth.models import User, user_from_row


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_in(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _iso_minutes_ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def create_session(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    expire_hours: int,
    user_agent: str | None = None,
) -> tuple[str, str]:
    """Insert a new session row; return ``(session_id, expires_at)``."""
    session_id = generate_session_id()
    expires_at = _iso_in(expire_hours)
    created_at = _iso_now()
    conn.execute(
        "INSERT INTO sessions(session_id, user_id, expires_at, "
        "created_at, user_agent, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, user_id, expires_at, created_at, user_agent, created_at),
    )
    conn.commit()
    return session_id, expires_at


def get_session_user(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    idle_minutes: int = 0,
) -> User | None:
    """Return the :class:`User` for an active session, else ``None``.

    Considered active when (a) the row exists, (b) ``expires_at`` is
    in the future, (c) the user is still active, and (d) — when
    ``idle_minutes > 0`` — ``last_seen`` is within that window.
    Expired / disabled / idled-out sessions are ignored; the caller
    treats all of them as "needs re-login."
    """
    if not session_id:
        return None
    row = conn.execute(
        """
        SELECT u.id, u.username, u.role, u.is_active,
               u.created_at, u.last_login_at,
               s.expires_at, s.last_seen
        FROM sessions s
        JOIN users u ON u.id = s.user_id
        WHERE s.session_id = ?
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    if not row["is_active"]:
        return None
    # ISO-8601 string compare is lexically correct for UTC timestamps.
    if row["expires_at"] <= _iso_now():
        return None
    # Idle-expiry: ``last_seen`` is bumped on every authenticated
    # request via :func:`touch_last_seen`.  An idle window of zero
    # disables the check (default — matches the legacy "only
    # absolute expiry matters" behaviour).
    #
    # Invariant: ``create_session`` always seeds ``last_seen`` to
    # ``created_at``, so ``NULL`` here means a session row was
    # manually inserted (test fixtures, future migration leaving
    # the column blank).  In that case we treat the session as
    # active — locking everyone out on a fresh DB column would be
    # the wrong default.  Test ``test_null_last_seen_treated_as_active``
    # pins this so a refactor doesn't silently flip the semantic.
    if idle_minutes > 0:
        last_seen = row["last_seen"]
        if last_seen is not None and last_seen < _iso_minutes_ago(idle_minutes):
            return None
    return user_from_row(row)


def touch_last_seen(conn: sqlite3.Connection, session_id: str) -> None:
    """Bump ``last_seen``.  Best-effort; failures swallowed."""
    if not session_id:
        return
    try:
        conn.execute(
            "UPDATE sessions SET last_seen = ? WHERE session_id = ?",
            (_iso_now(), session_id),
        )
        conn.commit()
    except sqlite3.Error:
        # last_seen is observational; don't 500 a real request on it.
        pass


def delete_session(conn: sqlite3.Connection, session_id: str) -> bool:
    if not session_id:
        return False
    cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    conn.commit()
    return cur.rowcount > 0


def delete_user_sessions(conn: sqlite3.Connection, user_id: int) -> int:
    """Nuclear logout — drop every session for the user."""
    cur = conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    return cur.rowcount


def gc_expired(conn: sqlite3.Connection) -> int:
    """Remove every expired session row.  Returns the count deleted."""
    cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_iso_now(),))
    conn.commit()
    return cur.rowcount


__all__ = [
    "create_session",
    "delete_session",
    "delete_user_sessions",
    "gc_expired",
    "get_session_user",
    "touch_last_seen",
]
