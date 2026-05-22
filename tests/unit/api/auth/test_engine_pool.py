"""Unit tests for :class:`EnginePool`.

Behaviour pinned:

- ``get_or_create`` returns the same engine for the same user_id.
- Different user_ids produce different engines (per-user isolation).
- LRU eviction kicks in at ``max_active``.
- Session dir resolution honours ``KT_CONFIG_DIR``.
- Anonymous slot (``user_id=None``) shares one engine across calls.
- ``evict_all`` cleans up.

The tests use the real :class:`Terrarium` constructor — engine spin-up
is fast enough for a few-test pool to be cheap, and it exercises the
real session-dir creation that the pool depends on.
"""

import pytest

from kohakuterrarium.api.auth.engine_pool import EnginePool, _user_session_dir


@pytest.fixture
def fresh_dirs(tmp_path, monkeypatch):
    """Redirect KT_CONFIG_DIR so per-user session dirs land in tmp."""
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    yield tmp_path


class TestSessionDirResolution:
    def test_none_user_uses_shared_sessions(self, fresh_dirs):
        path = _user_session_dir(None)
        assert path == fresh_dirs / "sessions"

    def test_user_id_under_users_namespace(self, fresh_dirs):
        path = _user_session_dir(42)
        assert path == fresh_dirs / "users" / "42" / "sessions"

    def test_session_dir_is_created_on_first_access(self, fresh_dirs):
        pool = EnginePool(max_active=2, idle_timeout_s=0)
        pool.get_or_create(7)
        assert (fresh_dirs / "users" / "7" / "sessions").is_dir()
        pool.evict_all()


class TestGetOrCreate:
    def test_same_user_returns_same_engine(self, fresh_dirs):
        pool = EnginePool(max_active=4, idle_timeout_s=0)
        e1 = pool.get_or_create(1)
        e2 = pool.get_or_create(1)
        assert e1 is e2
        pool.evict_all()

    def test_different_users_get_different_engines(self, fresh_dirs):
        pool = EnginePool(max_active=4, idle_timeout_s=0)
        e1 = pool.get_or_create(1)
        e2 = pool.get_or_create(2)
        assert e1 is not e2
        pool.evict_all()

    def test_anonymous_slot_is_shared(self, fresh_dirs):
        pool = EnginePool(max_active=4, idle_timeout_s=0)
        e1 = pool.get_or_create(None)
        e2 = pool.get_or_create(None)
        assert e1 is e2
        pool.evict_all()

    def test_anonymous_and_user_are_distinct(self, fresh_dirs):
        pool = EnginePool(max_active=4, idle_timeout_s=0)
        anon = pool.get_or_create(None)
        named = pool.get_or_create(1)
        assert anon is not named
        pool.evict_all()


class TestLruEviction:
    def test_oldest_evicted_at_capacity(self, fresh_dirs):
        pool = EnginePool(max_active=2, idle_timeout_s=0)
        pool.get_or_create(1)  # oldest
        pool.get_or_create(2)
        # Insert third user → triggers eviction of the LRU.
        pool.get_or_create(3)
        live = set(pool.live_user_ids())
        assert 1 not in live
        assert {2, 3}.issubset(live)
        pool.evict_all()

    def test_touch_keeps_user_alive(self, fresh_dirs):
        pool = EnginePool(max_active=2, idle_timeout_s=0)
        pool.get_or_create(1)
        pool.get_or_create(2)
        # Touch 1 so it's no longer the LRU.
        pool.get_or_create(1)
        pool.get_or_create(3)  # forces eviction
        live = set(pool.live_user_ids())
        # 2 was LRU after the touch — it should be the one evicted.
        assert 2 not in live
        assert {1, 3}.issubset(live)
        pool.evict_all()

    def test_touch_under_clock_tie(self, fresh_dirs):
        # Simulates the Windows ``time.monotonic()`` low-resolution
        # case: back-to-back calls in a busy loop can all return the
        # same float value (the kernel scheduler tick + float
        # precision conspire to lose ~100ns deltas).  In that case
        # LRU eviction must fall back on dict iteration order =
        # insertion / touch order, not the tied timestamps.  Audit
        # caught this when the CI matrix for Windows + py312 went
        # red on ``test_touch_keeps_user_alive`` with the wrong
        # user evicted.
        pool = EnginePool(max_active=2, idle_timeout_s=0)
        # Pin every monotonic() read to the same value so ALL
        # touches tie.
        pool._monotonic = lambda: 42.0  # type: ignore[assignment]
        pool.get_or_create(1)
        pool.get_or_create(2)
        pool.get_or_create(1)  # touch 1 — must reorder despite tie
        pool.get_or_create(3)
        live = set(pool.live_user_ids())
        assert (
            2 not in live
        ), f"under clock-tie, 2 should be evicted (LRU); got live={live}"
        assert {1, 3}.issubset(live)
        pool.evict_all()


class TestEvict:
    def test_evict_specific_user(self, fresh_dirs):
        pool = EnginePool(max_active=4, idle_timeout_s=0)
        pool.get_or_create(1)
        pool.get_or_create(2)
        assert pool.evict(1) is True
        assert pool.evict(1) is False  # already gone
        assert 1 not in set(pool.live_user_ids())
        assert 2 in set(pool.live_user_ids())
        pool.evict_all()

    def test_evict_anonymous(self, fresh_dirs):
        pool = EnginePool(max_active=4, idle_timeout_s=0)
        pool.get_or_create(None)
        assert pool.evict(None) is True
        assert None not in set(pool.live_user_ids())

    def test_evict_all(self, fresh_dirs):
        pool = EnginePool(max_active=4, idle_timeout_s=0)
        pool.get_or_create(1)
        pool.get_or_create(2)
        pool.get_or_create(None)
        assert pool.evict_all() == 3
        assert pool.live_user_ids() == []


class TestReaper:
    @pytest.mark.asyncio
    async def test_reaper_starts_and_stops(self, fresh_dirs):
        # idle_timeout_s > 0 enables the reaper.  The reaper interval
        # is computed as max(30, idle/2) — too long for a unit test,
        # but starting + cancelling should still work cleanly.
        pool = EnginePool(max_active=4, idle_timeout_s=60)
        await pool.start_reaper()
        await pool.stop_reaper()

    @pytest.mark.asyncio
    async def test_reaper_with_zero_timeout_is_noop(self, fresh_dirs):
        pool = EnginePool(max_active=4, idle_timeout_s=0)
        await pool.start_reaper()  # no task created
        await pool.stop_reaper()  # idempotent
