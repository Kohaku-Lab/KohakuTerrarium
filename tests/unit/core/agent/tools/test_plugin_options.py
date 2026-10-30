"""Unit tests for :mod:`kohakuterrarium.core.agent_plugin_options`."""

import types
from typing import Any

import pytest

from kohakuterrarium.core.agent_plugin_options import (
    PLUGIN_OPTIONS_STATE_SUFFIX,
    PluginOptions,
)


class _FakeManager:
    """Mimics PluginManager.set_plugin_options."""

    def __init__(self, registered=None):
        self._registered = set(registered) if registered is not None else {"budget"}
        self.calls: list[tuple] = []
        self.return_for: dict[str, dict] = {}
        self.raise_with: Exception | None = None

    def set_plugin_options(self, name, values):
        self.calls.append((name, dict(values)))
        if self.raise_with is not None:
            raise self.raise_with
        if name not in self._registered:
            raise KeyError(name)
        return dict(self.return_for.get(name, values))


class _FakeStore:
    def __init__(self):
        self.state: dict[str, Any] = {}


def _make_agent(*, manager=None, store=None, session=None, name="alice"):
    return types.SimpleNamespace(
        config=types.SimpleNamespace(name=name),
        plugins=manager,
        session_store=store,
        session=session,
    )


# ── set ──────────────────────────────────────────────────────────


class TestSet:
    def test_no_manager_raises_keyerror(self):
        agent = _make_agent(manager=None)
        opts = PluginOptions(agent)
        with pytest.raises(KeyError):
            opts.set("any", {"k": "v"})

    def test_records_applied_values(self):
        mgr = _FakeManager(registered=["budget"])
        agent = _make_agent(manager=mgr)
        opts = PluginOptions(agent)
        out = opts.set("budget", {"max_turns": 5})
        assert out == {"max_turns": 5}
        assert opts.get("budget") == {"max_turns": 5}

    def test_empty_dict_clears(self):
        mgr = _FakeManager(registered=["budget"])
        mgr.return_for["budget"] = {}
        agent = _make_agent(manager=mgr)
        opts = PluginOptions(agent)
        opts._values["budget"] = {"k": "v"}
        out = opts.set("budget", {})
        assert out == {}
        assert opts.get("budget") == {}
        assert "budget" not in opts._values

    def test_unknown_plugin_propagates(self):
        mgr = _FakeManager(registered=["budget"])
        agent = _make_agent(manager=mgr)
        opts = PluginOptions(agent)
        with pytest.raises(KeyError):
            opts.set("ghost", {"k": "v"})


# ── persistence ──────────────────────────────────────────────────


class TestPersistence:
    def test_session_store_written(self):
        mgr = _FakeManager()
        store = _FakeStore()
        agent = _make_agent(manager=mgr, store=store)
        opts = PluginOptions(agent)
        opts.set("budget", {"max_turns": 5})
        key = f"alice:{PLUGIN_OPTIONS_STATE_SUFFIX}"
        assert store.state[key]["budget"] == {"max_turns": 5}

    def test_session_extra_written(self):
        mgr = _FakeManager()
        session = types.SimpleNamespace(extra={})
        agent = _make_agent(manager=mgr, session=session)
        opts = PluginOptions(agent)
        opts.set("budget", {"max_turns": 5})
        assert session.extra[PLUGIN_OPTIONS_STATE_SUFFIX]["budget"] == {"max_turns": 5}

    def test_session_extra_removed_when_empty(self):
        mgr = _FakeManager(registered=["budget"])
        mgr.return_for["budget"] = {}  # plugin reports cleared
        session = types.SimpleNamespace(
            extra={PLUGIN_OPTIONS_STATE_SUFFIX: {"budget": {"k": "v"}}}
        )
        agent = _make_agent(manager=mgr, session=session)
        opts = PluginOptions(agent)
        opts._values["budget"] = {"k": "v"}
        opts.set("budget", {})
        # All cleared → entry removed.
        assert PLUGIN_OPTIONS_STATE_SUFFIX not in session.extra


# ── apply ────────────────────────────────────────────────────────


class TestApply:
    def test_no_manager_no_op(self):
        store = _FakeStore()
        store.state[f"alice:{PLUGIN_OPTIONS_STATE_SUFFIX}"] = {
            "budget": {"max_turns": 5}
        }
        agent = _make_agent(manager=None, store=store)
        opts = PluginOptions(agent)
        opts.apply()
        # Live state empty because no manager.
        assert opts.list() == {}

    def test_loads_from_store_and_pushes_to_manager(self):
        mgr = _FakeManager(registered=["budget"])
        store = _FakeStore()
        store.state[f"alice:{PLUGIN_OPTIONS_STATE_SUFFIX}"] = {
            "budget": {"max_turns": 5}
        }
        agent = _make_agent(manager=mgr, store=store)
        opts = PluginOptions(agent)
        opts.apply()
        assert opts.get("budget") == {"max_turns": 5}
        # Push went through.
        assert ("budget", {"max_turns": 5}) in mgr.calls

    def test_loads_from_session_extra(self):
        mgr = _FakeManager(registered=["budget"])
        session = types.SimpleNamespace(
            extra={PLUGIN_OPTIONS_STATE_SUFFIX: {"budget": {"max_turns": 7}}}
        )
        agent = _make_agent(manager=mgr, session=session)
        opts = PluginOptions(agent)
        opts.apply()
        assert opts.get("budget") == {"max_turns": 7}

    def test_keyerror_during_apply_skipped(self):
        mgr = _FakeManager(registered=[])  # plugin not registered → KeyError
        store = _FakeStore()
        store.state[f"alice:{PLUGIN_OPTIONS_STATE_SUFFIX}"] = {
            "budget": {"max_turns": 5}
        }
        agent = _make_agent(manager=mgr, store=store)
        opts = PluginOptions(agent)
        opts.apply()
        # Entry silently dropped — no crash.
        assert opts.list() == {}

    def test_valueerror_during_apply_skipped(self):
        mgr = _FakeManager(registered=["budget"])
        mgr.raise_with = ValueError("bad option")
        store = _FakeStore()
        store.state[f"alice:{PLUGIN_OPTIONS_STATE_SUFFIX}"] = {
            "budget": {"max_turns": 99}
        }
        agent = _make_agent(manager=mgr, store=store)
        opts = PluginOptions(agent)
        opts.apply()
        assert opts.list() == {}

    def test_empty_entry_skipped(self):
        mgr = _FakeManager(registered=["budget"])
        store = _FakeStore()
        store.state[f"alice:{PLUGIN_OPTIONS_STATE_SUFFIX}"] = {"budget": {}}
        agent = _make_agent(manager=mgr, store=store)
        opts = PluginOptions(agent)
        opts.apply()
        # No call to manager — empty values skipped.
        assert mgr.calls == []

    def test_non_dict_entry_skipped(self):
        mgr = _FakeManager(registered=["budget"])
        store = _FakeStore()
        store.state[f"alice:{PLUGIN_OPTIONS_STATE_SUFFIX}"] = {"budget": "not a dict"}
        agent = _make_agent(manager=mgr, store=store)
        opts = PluginOptions(agent)
        opts.apply()
        assert mgr.calls == []


# ── read accessors ───────────────────────────────────────────────


class TestRead:
    def test_get_missing_returns_empty(self):
        agent = _make_agent()
        opts = PluginOptions(agent)
        assert opts.get("nope") == {}

    def test_list_returns_independent_copy(self):
        agent = _make_agent()
        opts = PluginOptions(agent)
        opts._values["budget"] = {"k": 1}
        snap = opts.list()
        snap["budget"]["k"] = 99
        assert opts._values["budget"]["k"] == 1


# ── apply with manager=None early-return (line 80) ──────────────


class TestApplyNoManager:
    def test_apply_with_data_but_no_manager(self):
        store = _FakeStore()
        store.state[f"alice:{PLUGIN_OPTIONS_STATE_SUFFIX}"] = {"budget": {"x": 1}}
        # plugins=None on agent.
        agent = _make_agent(manager=None, store=store)
        opts = PluginOptions(agent)
        opts.apply()
        # No-op because no plugin manager.
        assert opts.list() == {}

    def test_apply_non_dict_state_returns(self):
        """When _load_private_state returns non-dict, ``apply`` bails
        before consulting the plugin manager (line 77)."""
        agent = _make_agent(manager=None)
        opts = PluginOptions(agent)
        opts._load_private_state = lambda: "garbage"  # type: ignore[method-assign]
        opts.apply()
        assert opts.list() == {}


# ── _load_private_state store.state.get raises (lines 116-117) ──


class TestLoadPrivateStateStoreFailure:
    def test_store_state_raises_keyerror(self):
        class _BadState:
            def get(self, key):
                raise KeyError("nope")

        store = types.SimpleNamespace(state=_BadState())
        agent = _make_agent(manager=None, store=store)
        opts = PluginOptions(agent)
        # Should not raise — returns empty.
        out = opts._load_private_state()
        assert out == {}

    def test_store_state_raises_typeerror(self):
        class _BadState:
            def get(self, key):
                raise TypeError("bad")

        store = types.SimpleNamespace(state=_BadState())
        agent = _make_agent(manager=None, store=store)
        opts = PluginOptions(agent)
        out = opts._load_private_state()
        assert out == {}
