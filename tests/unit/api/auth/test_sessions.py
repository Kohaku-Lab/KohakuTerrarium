"""Unit tests for ``api.auth.sessions``."""

import time
from datetime import datetime, timedelta, timezone

import pytest

from kohakuterrarium.api.auth.db import (
    connection,
    ensure_migrated,
    _reset_migration_state_for_tests,
)
from kohakuterrarium.api.auth.sessions import (
    create_session,
    delete_session,
    delete_user_sessions,
    gc_expired,
    get_session_user,
    touch_last_seen,
)
from kohakuterrarium.api.auth.users import create_user, set_active

_TEST_ROUNDS = 4


@pytest.fixture
def db_with_user(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
    _reset_migration_state_for_tests()
    ensure_migrated()
    with connection() as conn:
        user = create_user(conn, "alice", "x", bcrypt_rounds=_TEST_ROUNDS)
    yield user
    _reset_migration_state_for_tests()


class TestCreateSession:
    def test_returns_session_id_and_expiry(self, db_with_user):
        with connection() as conn:
            sid, expires_at = create_session(conn, db_with_user.id, expire_hours=24)
        assert isinstance(sid, str) and len(sid) > 20
        # Future ISO timestamp.
        assert expires_at > datetime.now(timezone.utc).isoformat()

    def test_session_user_agent_stored(self, db_with_user):
        with connection() as conn:
            sid, _ = create_session(
                conn,
                db_with_user.id,
                expire_hours=24,
                user_agent="kt-mobile/1.0",
            )
            row = conn.execute(
                "SELECT user_agent FROM sessions WHERE session_id = ?", (sid,)
            ).fetchone()
        assert row["user_agent"] == "kt-mobile/1.0"


class TestGetSessionUser:
    def test_active_session_returns_user(self, db_with_user):
        with connection() as conn:
            sid, _ = create_session(conn, db_with_user.id, expire_hours=24)
            user = get_session_user(conn, sid)
        assert user is not None
        assert user.username == "alice"

    def test_empty_session_id(self, db_with_user):
        with connection() as conn:
            assert get_session_user(conn, "") is None

    def test_missing_session(self, db_with_user):
        with connection() as conn:
            assert get_session_user(conn, "nonexistent") is None

    def test_expired_session_returns_none(self, db_with_user):
        # Insert an expired session by hand.
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        with connection() as conn:
            conn.execute(
                "INSERT INTO sessions(session_id, user_id, expires_at, created_at, last_seen) "
                "VALUES (?, ?, ?, ?, ?)",
                ("expired-sid", db_with_user.id, past, now, now),
            )
            conn.commit()
            assert get_session_user(conn, "expired-sid") is None

    def test_inactive_user_session_returns_none(self, db_with_user):
        with connection() as conn:
            sid, _ = create_session(conn, db_with_user.id, expire_hours=24)
            set_active(conn, db_with_user.id, False)
            assert get_session_user(conn, sid) is None


class TestTouchLastSeen:
    def test_updates_field(self, db_with_user):
        with connection() as conn:
            sid, _ = create_session(conn, db_with_user.id, expire_hours=24)
            before = conn.execute(
                "SELECT last_seen FROM sessions WHERE session_id = ?", (sid,)
            ).fetchone()["last_seen"]
            time.sleep(0.01)
            touch_last_seen(conn, sid)
            after = conn.execute(
                "SELECT last_seen FROM sessions WHERE session_id = ?", (sid,)
            ).fetchone()["last_seen"]
        assert after > before

    def test_empty_session_id_is_silent(self, db_with_user):
        # No raise; no DB write.
        with connection() as conn:
            touch_last_seen(conn, "")


class TestDeleteSession:
    def test_returns_true_when_present(self, db_with_user):
        with connection() as conn:
            sid, _ = create_session(conn, db_with_user.id, expire_hours=24)
            assert delete_session(conn, sid) is True
            assert get_session_user(conn, sid) is None

    def test_returns_false_when_missing(self, db_with_user):
        with connection() as conn:
            assert delete_session(conn, "nope") is False


class TestDeleteUserSessions:
    def test_drops_all_for_user(self, db_with_user):
        with connection() as conn:
            sid1, _ = create_session(conn, db_with_user.id, expire_hours=24)
            sid2, _ = create_session(conn, db_with_user.id, expire_hours=24)
            removed = delete_user_sessions(conn, db_with_user.id)
        assert removed == 2


class TestGcExpired:
    def test_purges_expired_only(self, db_with_user):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        now = datetime.now(timezone.utc).isoformat()
        with connection() as conn:
            # Insert one expired, one active.
            conn.execute(
                "INSERT INTO sessions(session_id, user_id, expires_at, created_at, last_seen) "
                "VALUES (?, ?, ?, ?, ?)",
                ("expired", db_with_user.id, past, now, now),
            )
            sid_live, _ = create_session(conn, db_with_user.id, expire_hours=24)
            conn.commit()
            removed = gc_expired(conn)
        assert removed == 1
        # Live session still works.
        with connection() as conn:
            assert get_session_user(conn, sid_live) is not None


class TestIdleExpiry:
    """Pin ``session_idle_minutes`` semantics on :func:`get_session_user`.

    The re-audit caught this as wired-but-untested: an operator
    setting ``session_idle_minutes = 30`` expected idle sessions to
    require re-login, but no test verified the actual expiry path.
    Three cases nail it down: legacy (idle=0) lets old sessions
    through, idle-but-fresh passes, idle-and-stale returns None.
    """

    def test_default_idle_zero_skips_check(self, db_with_user):
        # last_seen far in the past, idle_minutes=0 → no expiry check
        # applied, session still valid.
        past = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        with connection() as conn:
            sid, _ = create_session(conn, db_with_user.id, expire_hours=168)
            conn.execute(
                "UPDATE sessions SET last_seen = ? WHERE session_id = ?",
                (past, sid),
            )
            conn.commit()
            user = get_session_user(conn, sid, idle_minutes=0)
        assert user is not None

    def test_fresh_session_within_window_passes(self, db_with_user):
        # Brand-new session (last_seen == created_at == now-ish);
        # idle_minutes=30 → well within the window, user resolves.
        with connection() as conn:
            sid, _ = create_session(conn, db_with_user.id, expire_hours=168)
            user = get_session_user(conn, sid, idle_minutes=30)
        assert user is not None
        assert user.username == "alice"

    def test_stale_session_outside_window_returns_none(self, db_with_user):
        # last_seen set to 60 minutes ago; idle_minutes=30 → expired.
        stale = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
        with connection() as conn:
            sid, _ = create_session(conn, db_with_user.id, expire_hours=168)
            conn.execute(
                "UPDATE sessions SET last_seen = ? WHERE session_id = ?",
                (stale, sid),
            )
            conn.commit()
            user = get_session_user(conn, sid, idle_minutes=30)
        assert user is None

    def test_boundary_just_inside_window(self, db_with_user):
        # last_seen exactly 29 minutes ago; idle_minutes=30 → still valid.
        boundary = (datetime.now(timezone.utc) - timedelta(minutes=29)).isoformat()
        with connection() as conn:
            sid, _ = create_session(conn, db_with_user.id, expire_hours=168)
            conn.execute(
                "UPDATE sessions SET last_seen = ? WHERE session_id = ?",
                (boundary, sid),
            )
            conn.commit()
            user = get_session_user(conn, sid, idle_minutes=30)
        assert user is not None

    def test_null_last_seen_treated_as_active(self, db_with_user):
        # Defensive: if last_seen is somehow NULL (manual DB write,
        # future migration leaving the column blank), the idle check
        # falls through rather than locking everyone out.  Documented
        # in get_session_user — pinned here so a future refactor that
        # flips this semantic gets a clear test failure.
        with connection() as conn:
            sid, _ = create_session(conn, db_with_user.id, expire_hours=168)
            conn.execute(
                "UPDATE sessions SET last_seen = NULL WHERE session_id = ?",
                (sid,),
            )
            conn.commit()
            user = get_session_user(conn, sid, idle_minutes=30)
        assert user is not None


class TestIdleExpiryThroughDependency:
    """Cross-layer: cfg.session_idle_minutes plumbs through
    :func:`get_optional_user` to :func:`get_session_user`.

    The wiring path is what the original bug was — the field existed
    on AuthConfig and was never consulted.  This test asserts the
    full chain by driving the dependency via a TestClient.
    """

    def test_idle_session_returns_none_via_dependency(self, tmp_path, monkeypatch):
        from fastapi import APIRouter, Depends, FastAPI
        from fastapi.testclient import TestClient

        from kohakuterrarium.api.auth.config import AuthConfig
        from kohakuterrarium.api.auth.dependencies import get_optional_user
        from kohakuterrarium.api.auth.db import (
            _reset_migration_state_for_tests,
            ensure_migrated as _ensure_migrated,
        )

        monkeypatch.setenv("KT_AUTH_DB", str(tmp_path / "auth.db"))
        _reset_migration_state_for_tests()
        _ensure_migrated()
        with connection() as conn:
            user = create_user(conn, "alice", "x", bcrypt_rounds=4)
            sid, _ = create_session(conn, user.id, expire_hours=168)
            # Mark the session as 60 minutes idle.
            stale = (datetime.now(timezone.utc) - timedelta(minutes=60)).isoformat()
            conn.execute(
                "UPDATE sessions SET last_seen = ? WHERE session_id = ?",
                (stale, sid),
            )
            conn.commit()

        app = FastAPI()
        app.state.auth_config = AuthConfig(
            multi_user="required",
            session_idle_minutes=30,
            bcrypt_rounds=4,
        )

        router = APIRouter()

        @router.get("/whoami")
        def whoami(user=Depends(get_optional_user)) -> dict[str, str | None]:
            return {"username": getattr(user, "username", None)}

        app.include_router(router)

        with TestClient(app) as client:
            client.cookies.set("kt_session", sid)
            r = client.get("/whoami")
        assert r.status_code == 200
        # Idle window elapsed → dependency returns None → 200 with
        # null username (the route used the optional variant).
        assert r.json() == {"username": None}

        _reset_migration_state_for_tests()


class TestCascadeOnUserDelete:
    def test_sessions_deleted_on_user_delete(self, db_with_user):
        with connection() as conn:
            sid, _ = create_session(conn, db_with_user.id, expire_hours=24)
            # FK ON DELETE CASCADE should drop the session row.
            conn.execute("DELETE FROM users WHERE id = ?", (db_with_user.id,))
            conn.commit()
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (sid,)
            ).fetchone()
        assert row is None
