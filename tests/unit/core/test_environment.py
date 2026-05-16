"""Unit tests for :mod:`kohakuterrarium.core.environment`."""

from kohakuterrarium.core.channel import ChannelRegistry
from kohakuterrarium.core.environment import Environment
from kohakuterrarium.core.session import Session


class TestEnvironmentInit:
    def test_env_id_unique_per_instance(self):
        a = Environment()
        b = Environment()
        assert a.env_id != b.env_id
        # env_id is prefixed.
        assert a.env_id.startswith("env_")

    def test_explicit_env_id_preserved(self):
        e = Environment(env_id="custom")
        assert e.env_id == "custom"

    def test_shared_channels_is_per_instance_registry(self):
        a = Environment()
        b = Environment()
        assert isinstance(a.shared_channels, ChannelRegistry)
        # Two envs MUST NOT share the same registry object.
        assert a.shared_channels is not b.shared_channels

    def test_initial_sessions_empty(self):
        e = Environment()
        assert e.list_sessions() == []


class TestSessions:
    def test_get_session_creates_on_first_access(self):
        e = Environment()
        s = e.get_session("alice")
        assert isinstance(s, Session)
        assert s.key == "alice"
        assert e.list_sessions() == ["alice"]

    def test_get_session_returns_same_instance_on_repeat(self):
        e = Environment()
        first = e.get_session("alice")
        second = e.get_session("alice")
        assert first is second

    def test_list_sessions_in_insertion_order(self):
        e = Environment()
        e.get_session("alice")
        e.get_session("bob")
        e.get_session("carol")
        assert e.list_sessions() == ["alice", "bob", "carol"]


class TestContextRegistry:
    def test_register_and_get(self):
        e = Environment()
        e.register("plugin_state", {"x": 1})
        assert e.get("plugin_state") == {"x": 1}

    def test_get_missing_returns_default(self):
        e = Environment()
        assert e.get("missing") is None
        sentinel = object()
        assert e.get("missing", default=sentinel) is sentinel

    def test_register_overrides_previous_value(self):
        e = Environment()
        e.register("k", "first")
        e.register("k", "second")
        assert e.get("k") == "second"


class TestIsolation:
    def test_two_environments_dont_share_sessions(self):
        a = Environment()
        b = Environment()
        a.get_session("alice")
        assert b.list_sessions() == []

    def test_two_environments_dont_share_context(self):
        a = Environment()
        b = Environment()
        a.register("k", "value-a")
        assert b.get("k") is None
