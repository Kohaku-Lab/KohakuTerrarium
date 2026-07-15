"""Create, validate, refresh, and delete browser sessions.

Session identifiers are short-lived, high-entropy values rotated at login and stored
verbatim so request-time ``last_seen`` updates can address rows directly. Validation
requires an active user, future absolute expiry, and optional idle-window freshness.
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
    """Return the user only when the session and account remain active."""
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
    # Uniform UTC ISO-8601 timestamps preserve chronological order lexically.
    if row["expires_at"] <= _iso_now():
        return None
    # Zero disables idle expiry. New sessions seed ``last_seen``; a null value from
    # older or manually inserted rows remains active to preserve migration compatibility.
    if idle_minutes > 0:
        last_seen = row["last_seen"]
        if last_seen is not None and last_seen < _iso_minutes_ago(idle_minutes):
            return None
    return user_from_row(row)


def touch_last_seen(conn: sqlite3.Connection, session_id: str) -> None:
    """Refresh observational activity metadata without failing authentication."""
    if not session_id:
        return
    try:
        conn.execute(
            "UPDATE sessions SET last_seen = ? WHERE session_id = ?",
            (_iso_now(), session_id),
        )
        conn.commit()
    except sqlite3.Error:
        # Activity telemetry must not turn an otherwise valid request into an error.
        pass


def delete_session(conn: sqlite3.Connection, session_id: str) -> bool:
    if not session_id:
        return False
    cur = conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    conn.commit()
    return cur.rowcount > 0


def delete_user_sessions(conn: sqlite3.Connection, user_id: int) -> int:
    """Invalidate every browser session owned by the user."""
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
