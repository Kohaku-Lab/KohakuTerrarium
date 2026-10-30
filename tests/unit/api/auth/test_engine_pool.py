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

import asyncio

import pytest

import kohakuterrarium.bootstrap.agent_init as agent_init
import kohakuterrarium.bootstrap.llm as bootstrap_llm
from kohakuterrarium.api.auth.db import (
    _reset_migration_state_for_tests,
    connection,
    ensure_migrated,
)
from kohakuterrarium.api.auth.engine_pool import (
    EnginePool,
    _user_session_dir,
    user_is_admin,
)
from kohakuterrarium.api.auth.users import create_user
from kohakuterrarium.terrarium import host_resources_allowed
from kohakuterrarium.terrarium.creature.host import Creature
from kohakuterrarium.testing.llm import ScriptedLLM
from kohakuterrarium.testing.terrarium import _FakeAgent


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
        pool = EnginePool(max_active=2)
        pool.get_or_create(7)
        assert (fresh_dirs / "users" / "7" / "sessions").is_dir()
        pool.evict_all()


class TestGetOrCreate:
    def test_imports_terrarium_only_when_creating_engine(self, fresh_dirs, monkeypatch):
        import kohakuterrarium

        created = []

        class _Terrarium:
            def __init__(self, **kwargs):
                created.append(kwargs)

        monkeypatch.setattr(kohakuterrarium, "Terrarium", _Terrarium)
        pool = EnginePool(max_active=1)

        engine = pool.get_or_create(7)

        assert isinstance(engine, _Terrarium)
        assert created == [
            {
                "session_dir": str(fresh_dirs / "users" / "7" / "sessions"),
                "host_resources": False,
            }
        ]

    def test_same_user_returns_same_engine(self, fresh_dirs):
        pool = EnginePool(max_active=4)
        e1 = pool.get_or_create(1)
        e2 = pool.get_or_create(1)
        assert e1 is e2
        pool.evict_all()

    def test_different_users_get_different_engines(self, fresh_dirs):
        pool = EnginePool(max_active=4)
        e1 = pool.get_or_create(1)
        e2 = pool.get_or_create(2)
        assert e1 is not e2
        pool.evict_all()

    def test_anonymous_slot_is_shared(self, fresh_dirs):
        pool = EnginePool(max_active=4)
        e1 = pool.get_or_create(None)
        e2 = pool.get_or_create(None)
        assert e1 is e2
        pool.evict_all()

    def test_anonymous_and_user_are_distinct(self, fresh_dirs):
        pool = EnginePool(max_active=4)
        anon = pool.get_or_create(None)
        named = pool.get_or_create(1)
        assert anon is not named
        pool.evict_all()


class TestHostResources:
    def test_only_admin_engines_get_host_resources(self, fresh_dirs):
        pool = EnginePool(max_active=4)
        assert pool.get_or_create(1, is_admin=True).host_resources is True
        assert pool.get_or_create(2, is_admin=False).host_resources is False
        assert pool.get_or_create(None, is_admin=True).host_resources is False
        pool.evict_all()

    def test_role_change_updates_the_live_engine(self, fresh_dirs):
        pool = EnginePool(max_active=4)
        engine = pool.get_or_create(1, is_admin=True)
        assert pool.get_or_create(1, is_admin=False) is engine
        assert engine.host_resources is False
        pool.get_or_create(1, is_admin=True)
        assert engine.host_resources is True
        pool.evict_all()

    def test_resolver_decides_when_the_caller_does_not(self, fresh_dirs):
        asked = []

        def resolver(user_id):
            asked.append(user_id)
            return user_id == 1

        pool = EnginePool(max_active=4, admin_resolver=resolver)
        assert pool.get_or_create(1).host_resources is True
        assert pool.get_or_create(2).host_resources is False
        assert pool.get_or_create(1, is_admin=False).host_resources is False
        assert asked == [1, 2]
        assert pool.peek(1).host_resources is False
        assert pool.peek(99) is None
        pool.evict_all()

    async def test_feed_triggers_see_their_engine_role(self, fresh_dirs, monkeypatch):
        def scripted(config, llm=None):
            return ScriptedLLM(["ok"])

        monkeypatch.setattr(agent_init, "create_llm_provider", scripted)
        monkeypatch.setattr(bootstrap_llm, "create_llm_provider", scripted)
        creature_dir = fresh_dirs / "feed"
        creature_dir.mkdir()
        (creature_dir / "config.yaml").write_text(
            "name: feed\ninput:\n  type: none\noutput:\n  type: stdout\n"
            "triggers:\n  - type: timer\n    name: tick\n    prompt: tick\n"
            "    options:\n      interval: 3600\n",
            encoding="utf-8",
        )
        pool = EnginePool(max_active=4)
        try:
            for user_id, admin in ((1, True), (2, False)):
                engine = pool.get_or_create(user_id, is_admin=admin)
                creature = await engine.add_creature(str(creature_dir))
                tick = creature.agent.trigger_manager.get_trigger("tick")
                assert host_resources_allowed(tick) is admin
        finally:
            await pool.evict_all_async()

    def test_failing_resolver_denies(self, fresh_dirs):
        def resolver(user_id):
            raise RuntimeError("db down")

        pool = EnginePool(max_active=4, admin_resolver=resolver)
        assert pool.get_or_create(1).host_resources is False
        pool.evict_all()

    def test_db_resolver_reads_the_user_role(self, fresh_dirs, monkeypatch):
        monkeypatch.setenv("KT_AUTH_DB", str(fresh_dirs / "auth.db"))
        _reset_migration_state_for_tests()
        ensure_migrated()
        with connection() as conn:
            admin = create_user(conn, "root", "pw", role="admin", bcrypt_rounds=4)
            user = create_user(conn, "bob", "pw", role="user", bcrypt_rounds=4)
        assert user_is_admin(admin.id) is True
        assert user_is_admin(user.id) is False
        assert user_is_admin(None) is False
        assert user_is_admin(12345) is False
        _reset_migration_state_for_tests()


class TestLruEviction:
    def test_oldest_evicted_at_capacity(self, fresh_dirs):
        pool = EnginePool(max_active=2)
        pool.get_or_create(1)  # oldest
        pool.get_or_create(2)
        # Insert third user → triggers eviction of the LRU.
        pool.get_or_create(3)
        live = set(pool.live_user_ids())
        assert 1 not in live
        assert {2, 3}.issubset(live)
        pool.evict_all()

    def test_touch_keeps_user_alive(self, fresh_dirs):
        pool = EnginePool(max_active=2)
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
        pool = EnginePool(max_active=2)
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
        pool = EnginePool(max_active=4)
        pool.get_or_create(1)
        pool.get_or_create(2)
        assert pool.evict(1) is True
        assert pool.evict(1) is False  # already gone
        assert 1 not in set(pool.live_user_ids())
        assert 2 in set(pool.live_user_ids())
        pool.evict_all()

    def test_evict_anonymous(self, fresh_dirs):
        pool = EnginePool(max_active=4)
        pool.get_or_create(None)
        assert pool.evict(None) is True
        assert None not in set(pool.live_user_ids())

    def test_evict_all(self, fresh_dirs):
        pool = EnginePool(max_active=4)
        pool.get_or_create(1)
        pool.get_or_create(2)
        pool.get_or_create(None)
        assert pool.evict_all() == 3
        assert pool.live_user_ids() == []


async def busy_engine(pool, user_id):
    """An engine that hosts a running creature (for example one waiting on a trigger)."""
    engine = pool.get_or_create(user_id)
    creature = Creature(creature_id="worker", name="worker", agent=_FakeAgent("worker"))
    await engine.add_creature(creature)
    return engine


class TestEnginesHostingCreaturesAreNeverStopped:
    async def test_capacity_pressure_does_not_stop_a_busy_engine(self, fresh_dirs):
        pool = EnginePool(max_active=1)
        busy = await busy_engine(pool, 1)

        pool.get_or_create(2)
        await asyncio.sleep(0)

        assert set(pool.live_user_ids()) == {1, 2}
        assert [c.name for c in busy.list_creatures()] == ["worker"]
        await pool.evict_all_async()

    async def test_capacity_pressure_stops_an_idle_engine_before_a_busy_one(
        self, fresh_dirs
    ):
        pool = EnginePool(max_active=2)
        busy = await busy_engine(pool, 1)
        pool.get_or_create(2)

        pool.get_or_create(3)
        await asyncio.sleep(0)

        assert set(pool.live_user_ids()) == {1, 3}
        assert len(busy.list_creatures()) == 1
        await pool.evict_all_async()

    async def test_engines_are_not_expired_by_the_passage_of_time(self, fresh_dirs):
        clock = [1000.0]
        pool = EnginePool(max_active=4)
        pool._monotonic = lambda: clock[0]  # type: ignore[assignment]
        busy = await busy_engine(pool, 1)
        idle = pool.get_or_create(2)

        clock[0] += 30 * 24 * 3600
        await asyncio.sleep(0)

        assert pool.get_or_create(1) is busy
        assert pool.get_or_create(2) is idle
        assert len(busy.list_creatures()) == 1
        await pool.evict_all_async()
