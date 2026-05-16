"""Unit tests for :mod:`kohakuterrarium.terrarium.channels`."""

from types import SimpleNamespace


from kohakuterrarium.core.environment import Environment
from kohakuterrarium.terrarium import channels as channels_mod
from kohakuterrarium.terrarium.topology import ChannelInfo

# ── register_channel_in_environment ───────────────────────────


class TestRegisterChannelInEnvironment:
    def test_basic(self):
        env = Environment(env_id="env-1")
        info = ChannelInfo(name="chat", description="d")
        out = channels_mod.register_channel_in_environment(env.shared_channels, info)
        assert out is not None
        # ChannelRegistry stores by name.
        assert "chat" in env.shared_channels._channels

    def test_idempotent_reregister(self):
        env = Environment(env_id="env-1")
        info = ChannelInfo(name="chat")
        ch1 = channels_mod.register_channel_in_environment(env.shared_channels, info)
        ch2 = channels_mod.register_channel_in_environment(env.shared_channels, info)
        assert ch1 is ch2


# ── _ensure_channel_persistence ───────────────────────────────


class _FakeStore:
    def __init__(self):
        self.saved = []

    def save_channel_message(self, channel_name, payload):
        self.saved.append((channel_name, payload))


class _FakeEngine:
    def __init__(self, store=None):
        self._session_stores = {"g1": store} if store else {}


class TestEnsureChannelPersistence:
    def test_marks_graph_id(self):
        channel = SimpleNamespace(on_send=lambda fn: None)
        engine = _FakeEngine()
        channels_mod._ensure_channel_persistence(channel, engine, "g1")
        assert channel._terrarium_graph_id == "g1"

    def test_idempotent(self):
        # Second call doesn't re-install on_send.
        installed = []

        class _Ch:
            def __init__(self):
                self._terrarium_graph_id = None
                self._terrarium_persistence_installed = False

            def on_send(self, fn):
                installed.append(fn)

        ch = _Ch()
        engine = _FakeEngine()
        channels_mod._ensure_channel_persistence(ch, engine, "g1")
        # Simulate the flag being set after first install.
        ch._terrarium_persistence_installed = True
        channels_mod._ensure_channel_persistence(ch, engine, "g2")
        # Graph id refreshed.
        assert ch._terrarium_graph_id == "g2"

    def test_refreshes_graph_id_on_subsequent_calls(self):
        class _Ch:
            _terrarium_persistence_installed = False

            def on_send(self, fn):
                pass

        ch = _Ch()
        channels_mod._ensure_channel_persistence(ch, _FakeEngine(), "g1")
        channels_mod._ensure_channel_persistence(ch, _FakeEngine(), "g2")
        assert ch._terrarium_graph_id == "g2"


# ── bind_creature_to_environment ──────────────────────────────


class TestBindCreatureToEnvironment:
    def test_assigns_environment(self):
        env = Environment(env_id="env-1")
        agent = SimpleNamespace(environment=None, executor=None)
        creature = SimpleNamespace(agent=agent)
        channels_mod.bind_creature_to_environment(creature, env)
        assert agent.environment is env

    def test_assigns_executor_env(self):
        env = Environment(env_id="env-1")
        executor = SimpleNamespace(_environment=None)
        agent = SimpleNamespace(environment=None, executor=executor)
        creature = SimpleNamespace(agent=agent)
        channels_mod.bind_creature_to_environment(creature, env)
        assert executor._environment is env

    def test_idempotent_no_change(self):
        env = Environment(env_id="env-1")
        agent = SimpleNamespace(environment=env, executor=None)
        creature = SimpleNamespace(agent=agent)
        channels_mod.bind_creature_to_environment(creature, env)
        assert agent.environment is env


# ── register_engine_handle ────────────────────────────────────


class _WeakRefable:
    """Plain class that allows weakref (SimpleNamespace doesn't)."""


class TestRegisterEngineHandle:
    def test_registers_weakref(self):
        env = Environment(env_id="env-1")
        engine = _WeakRefable()
        channels_mod.register_engine_handle(env, engine)
        ref = env.get(channels_mod.TERRARIUM_ENGINE_KEY)
        assert callable(ref)
        assert ref() is engine

    def test_idempotent_replaces(self):
        env = Environment(env_id="env-1")
        engine1 = _WeakRefable()
        engine2 = _WeakRefable()
        channels_mod.register_engine_handle(env, engine1)
        channels_mod.register_engine_handle(env, engine2)
        ref = env.get(channels_mod.TERRARIUM_ENGINE_KEY)
        assert ref() is engine2


# ── inject_channel_trigger ────────────────────────────────────


class _FakeTriggerManager:
    def __init__(self):
        self._triggers = {}
        self._created_at = {}
        self._tasks = {}


class _FakeAgent:
    def __init__(self):
        self.trigger_manager = _FakeTriggerManager()
        # _creature_id is read as a fallback ignore_sender_id.
        self._creature_id = "alice"


class TestInjectChannelTrigger:
    def test_basic(self):
        env = Environment(env_id="e")
        info = ChannelInfo(name="chat")
        channels_mod.register_channel_in_environment(env.shared_channels, info)
        agent = _FakeAgent()
        tid = channels_mod.inject_channel_trigger(
            agent,
            subscriber_id="alice",
            channel_name="chat",
            registry=env.shared_channels,
        )
        assert tid == "channel_alice_chat"
        assert tid in agent.trigger_manager._triggers
        # Created-at stamped.
        assert tid in agent.trigger_manager._created_at

    def test_reinjection_tears_down(self):
        env = Environment(env_id="e")
        info = ChannelInfo(name="chat")
        channels_mod.register_channel_in_environment(env.shared_channels, info)
        agent = _FakeAgent()
        # First inject.
        tid = channels_mod.inject_channel_trigger(
            agent,
            subscriber_id="alice",
            channel_name="chat",
            registry=env.shared_channels,
        )
        first_trigger = agent.trigger_manager._triggers[tid]
        # Second inject — same id, but trigger object replaced.
        tid2 = channels_mod.inject_channel_trigger(
            agent,
            subscriber_id="alice",
            channel_name="chat",
            registry=env.shared_channels,
        )
        assert tid == tid2
        assert agent.trigger_manager._triggers[tid] is not first_trigger

    def test_remove_when_absent_returns_false(self):
        agent = _FakeAgent()
        out = channels_mod.remove_channel_trigger(
            agent, subscriber_id="alice", channel_name="chat"
        )
        assert out is False

    def test_remove_after_inject(self):
        env = Environment(env_id="e")
        info = ChannelInfo(name="chat")
        channels_mod.register_channel_in_environment(env.shared_channels, info)
        agent = _FakeAgent()
        channels_mod.inject_channel_trigger(
            agent,
            subscriber_id="alice",
            channel_name="chat",
            registry=env.shared_channels,
        )
        out = channels_mod.remove_channel_trigger(
            agent, subscriber_id="alice", channel_name="chat"
        )
        assert out is True


# ── _teardown_existing_trigger ────────────────────────────────


class TestTeardownExistingTrigger:
    def test_no_manager_silent(self):
        agent = SimpleNamespace(trigger_manager=None)
        # Doesn't raise.
        channels_mod._teardown_existing_trigger(agent, "tid")

    def test_no_triggers_dict_silent(self):
        agent = SimpleNamespace(trigger_manager=SimpleNamespace())
        channels_mod._teardown_existing_trigger(agent, "tid")
