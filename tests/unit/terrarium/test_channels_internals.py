"""Branch-coverage tests for the lower-level helpers in
:mod:`kohakuterrarium.terrarium.channels` — the trigger spawn/teardown
machinery and the persistence callback's loop-less fallback.

These exercise the concurrency-sensitive arms the engine-level tests in
``test_channels`` / ``test_channels_more`` don't reach.
"""

import asyncio
from types import SimpleNamespace

from kohakuterrarium.core.environment import Environment
from kohakuterrarium.terrarium import channels as channels_mod
from kohakuterrarium.terrarium.topology import ChannelInfo


def _make_channel():
    env = Environment(env_id="env-1")
    info = ChannelInfo(name="chat", description="d")
    return channels_mod.register_channel_in_environment(env.shared_channels, info)


# ---------------------------------------------------------------------------
# _persist callback — timestamp + loop-less forward fallback
# ---------------------------------------------------------------------------


class _CapturingEngine:
    def __init__(self, broadcast=None):
        self._session_stores = {}
        self._broadcast_adapter = broadcast
        self.emitted = []

    def _emit(self, ev):
        self.emitted.append(ev)


def _persist_callback(channel):
    """Extract the single on_send persistence callback installed on a
    channel by ``_ensure_channel_persistence``."""
    for attr in ("_on_send_callbacks", "_send_callbacks", "_on_send"):
        cbs = getattr(channel, attr, None)
        if cbs:
            return list(cbs)[-1]
    raise AssertionError("could not locate the channel's on_send callbacks")


class TestPersistTimestampFallback:
    def test_message_without_timestamp_uses_walltime(self):
        """A message object that lacks a ``timestamp`` attribute still
        persists — the callback falls back to ``time.time()``."""
        eng = _CapturingEngine()

        class _Store:
            def __init__(self):
                self.saved = []

            def save_channel_message(self, name, payload):
                self.saved.append(payload)

        store = _Store()
        eng._session_stores["g1"] = store
        ch = _make_channel()
        channels_mod._ensure_channel_persistence(ch, eng, "g1")
        cb = _persist_callback(ch)
        # A bare namespace — no ``timestamp`` attribute at all.
        msg = SimpleNamespace(sender="a", content="x", message_id="m1")
        cb("chat", msg)
        assert store.saved
        assert isinstance(store.saved[0]["ts"], float)

    def test_forward_silently_dropped_without_running_loop(self, monkeypatch):
        """When peers exist but there's no running loop, the broadcast
        forward is silently dropped (can't schedule a task)."""

        class _Broadcast:
            def __init__(self):
                self.forwarded = []

            def peers_for(self, gid, name):
                return True

            async def forward_send(self, gid, name, payload):
                self.forwarded.append(payload)

        bc = _Broadcast()
        eng = _CapturingEngine(broadcast=bc)
        ch = _make_channel()
        channels_mod._ensure_channel_persistence(ch, eng, "g1")
        cb = _persist_callback(ch)

        def _no_loop():
            raise RuntimeError("no running loop")

        monkeypatch.setattr(channels_mod.asyncio, "get_running_loop", _no_loop)
        msg = SimpleNamespace(sender="a", content="x", message_id="m1")
        # Must not raise — the RuntimeError is caught and the send dropped.
        cb("chat", msg)
        assert bc.forwarded == []
        # The engine event still fired.
        assert eng.emitted


# ---------------------------------------------------------------------------
# _teardown_existing_trigger — running-loop task cancellation
# ---------------------------------------------------------------------------


class TestTeardownExistingTrigger:
    async def test_cancels_live_task(self):
        """A live (not-done) trigger task in the manager's ``_tasks`` is
        cancelled when its trigger is torn down."""

        async def _forever():
            await asyncio.sleep(100)

        task = asyncio.create_task(_forever())

        class _Trigger:
            async def stop(self):
                pass

        manager = SimpleNamespace(
            _triggers={"t1": _Trigger()},
            _tasks={"t1": task},
        )
        agent = SimpleNamespace(trigger_manager=manager)
        channels_mod._teardown_existing_trigger(agent, "t1")
        # Trigger removed and its run-loop task cancelled.
        assert "t1" not in manager._triggers
        await asyncio.sleep(0)
        assert task.cancelled()

    async def test_noop_when_trigger_absent(self):
        manager = SimpleNamespace(_triggers={}, _tasks={})
        agent = SimpleNamespace(trigger_manager=manager)
        # Nothing to tear down — must not raise.
        channels_mod._teardown_existing_trigger(agent, "ghost")


# ---------------------------------------------------------------------------
# _spawn_trigger_runner — real-manager spawn path
# ---------------------------------------------------------------------------


class TestSpawnTriggerRunner:
    async def test_spawns_run_loop_task(self):
        """Against a manager exposing ``_run_loop`` + ``_tasks``, the
        runner starts the trigger and registers its run-loop task."""
        started = []
        ran = []

        class _Trigger:
            async def start(self):
                started.append(True)

        async def _run_loop(trigger_id, trigger):
            ran.append(trigger_id)
            await asyncio.sleep(100)

        manager = SimpleNamespace(_run_loop=_run_loop, _tasks={})
        agent = SimpleNamespace(trigger_manager=manager)
        trigger = _Trigger()
        channels_mod._spawn_trigger_runner(agent, "t1", trigger)
        # The outer _run coroutine is itself a task — let it run.
        await asyncio.sleep(0.05)
        assert started == [True]
        assert "t1" in manager._tasks
        assert ran == ["t1"]
        manager._tasks["t1"].cancel()

    async def test_noop_when_manager_lacks_run_loop(self):
        """A manager fake without ``_run_loop`` is skipped — the real
        agent's ``start_all`` picks the trigger up later."""
        manager = SimpleNamespace(_tasks={})  # no _run_loop
        agent = SimpleNamespace(trigger_manager=manager)
        channels_mod._spawn_trigger_runner(agent, "t1", SimpleNamespace())
        assert manager._tasks == {}

    def test_noop_without_running_loop(self):
        async def _run_loop(tid, tr):
            pass

        manager = SimpleNamespace(_run_loop=_run_loop, _tasks={})
        agent = SimpleNamespace(trigger_manager=manager)
        # Sync context → no loop → nothing scheduled.
        channels_mod._spawn_trigger_runner(agent, "t1", SimpleNamespace())
        assert manager._tasks == {}


# ---------------------------------------------------------------------------
# _merge_environment_into — defensive None branches
# ---------------------------------------------------------------------------


class _WeakrefEngine:
    """Engine stand-in that supports ``weakref.ref`` (SimpleNamespace
    does not) — needed for ``register_engine_handle``."""

    def __init__(self, environments, graphs, creatures):
        self._environments = environments
        self._topology = SimpleNamespace(graphs=graphs)
        self._creatures = creatures


class TestMergeEnvironmentIntoDefensive:
    def test_missing_drop_env_returns_early(self):
        """When the dropped graph has no environment entry, the merge
        helper returns immediately without touching the kept env."""
        keep_env = Environment(env_id="env_keep")
        engine = _WeakrefEngine({"keep": keep_env}, {}, {})
        # ``drop`` is absent from _environments → drop_env is None.
        channels_mod._merge_environment_into(engine, "keep", "drop")
        # keep_env untouched / no crash.
        assert engine._environments == {"keep": keep_env}

    def test_skips_missing_creature_in_kept_graph(self):
        """A creature_id present in the kept graph's membership but
        absent from ``engine._creatures`` is skipped, not crashed on."""
        keep_env = Environment(env_id="env_keep")
        drop_env = Environment(env_id="env_drop")
        keep_graph = SimpleNamespace(
            channels={}, creature_ids={"ghost"}, listen_edges={}
        )
        engine = _WeakrefEngine(
            {"keep": keep_env, "drop": drop_env},
            {"keep": keep_graph},
            {},  # "ghost" not here
        )
        channels_mod._merge_environment_into(engine, "keep", "drop")
        # drop_env consumed, no exception.
        assert "drop" not in engine._environments
