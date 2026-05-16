"""Unit tests for :mod:`kohakuterrarium.core.session` (Session + registry)."""

import pytest

import kohakuterrarium.core.session as session_mod
from kohakuterrarium.core.channel import ChannelRegistry
from kohakuterrarium.core.scratchpad import Scratchpad
from kohakuterrarium.core.session import (
    Session,
    get_channel_registry,
    get_scratchpad,
    get_session,
    list_sessions,
    remove_session,
    set_session,
)


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """The session registry is a module global; snapshot per-test."""
    monkeypatch.setattr(session_mod, "_sessions", {})
    yield


class TestSessionDataclass:
    def test_required_key_argument(self):
        s = Session(key="alice")
        assert s.key == "alice"

    def test_default_channels_is_per_instance_registry(self):
        a = Session(key="a")
        b = Session(key="b")
        assert isinstance(a.channels, ChannelRegistry)
        assert a.channels is not b.channels

    def test_default_scratchpad_is_per_instance(self):
        a = Session(key="a")
        b = Session(key="b")
        assert isinstance(a.scratchpad, Scratchpad)
        assert a.scratchpad is not b.scratchpad

    def test_default_extra_dict_per_instance(self):
        a = Session(key="a")
        b = Session(key="b")
        a.extra["k"] = "v"
        assert b.extra == {}

    def test_tui_defaults_to_none(self):
        assert Session(key="x").tui is None


class TestGetSession:
    def test_creates_on_first_access(self):
        s = get_session("alice")
        assert s.key == "alice"
        assert list_sessions() == ["alice"]

    def test_returns_same_instance_on_repeat(self):
        a = get_session("alice")
        b = get_session("alice")
        assert a is b

    def test_none_key_uses_default(self):
        a = get_session(None)
        b = get_session()
        assert a is b
        # Default key is the internal sentinel.
        assert a.key == "__default__"

    def test_distinct_keys_get_distinct_sessions(self):
        a = get_session("alice")
        b = get_session("bob")
        assert a is not b


class TestSetSession:
    def test_inject_replaces_existing(self):
        get_session("alice")
        custom = Session(key="alice")
        custom.extra["marker"] = "custom"
        set_session(custom, key="alice")
        again = get_session("alice")
        assert again is custom
        assert again.extra["marker"] == "custom"

    def test_inject_with_none_key_targets_default(self):
        custom = Session(key="__default__")
        set_session(custom)
        assert get_session() is custom


class TestRemoveSession:
    def test_removes_existing(self):
        get_session("alice")
        remove_session("alice")
        assert "alice" not in list_sessions()

    def test_removing_missing_is_noop(self):
        remove_session("ghost")
        assert list_sessions() == []

    def test_none_key_removes_default(self):
        get_session()  # creates default
        remove_session(None)
        assert "__default__" not in list_sessions()


class TestConvenienceAccessors:
    def test_get_scratchpad_returns_default_session_pad(self):
        pad = get_scratchpad()
        # Same as default session's scratchpad.
        assert pad is get_session().scratchpad

    def test_get_channel_registry_returns_default_session_channels(self):
        chans = get_channel_registry()
        assert chans is get_session().channels
