"""Unit tests for :mod:`kohakuterrarium.core.agent_selection`."""

import json

from kohakuterrarium.core.agent_selection import (
    load_model_selection,
    load_plugin_selection,
    persist_model_selection,
    persist_plugin_selection,
    restore_selections,
)


class _Store:
    def __init__(self):
        self.state = {}


class _Config:
    def __init__(self, name="alice"):
        self.name = name


class _Plugin:
    def __init__(self, name):
        self.name = name


class _PluginManager:
    def __init__(self, plugins):
        self._plugins = [p if isinstance(p, _Plugin) else _Plugin(p) for p in plugins]
        self._disabled = set()

    def get_plugin(self, name):
        return next((p for p in self._plugins if p.name == name), None)

    def is_enabled(self, name):
        return name not in self._disabled

    def enable(self, name):
        self._disabled.discard(name)

    def disable(self, name):
        self._disabled.add(name)

    def list_plugins(self):
        return [
            {"name": p.name, "enabled": p.name not in self._disabled}
            for p in self._plugins
        ]


class _Agent:
    def __init__(self, name="alice", store=None, plugins=None):
        self.config = _Config(name)
        self.session_store = store or _Store()
        self.plugins = plugins
        self.switched = []

    def switch_model(self, selector):
        if selector == "bad/model":
            raise ValueError("unknown profile")
        self.switched.append(selector)
        return selector


class TestPersist:
    def test_persist_model_selection(self):
        store = _Store()
        agent = _Agent(store=store)
        persist_model_selection(agent, "openai/gpt-5.4@reasoning=high")
        assert store.state["alice:model_selection"] == "openai/gpt-5.4@reasoning=high"

    def test_persist_plugin_selection_sorted_json(self):
        store = _Store()
        agent = _Agent(store=store)
        persist_plugin_selection(agent, ["budget", "permgate"])
        assert json.loads(store.state["alice:plugin_selection"]) == [
            "budget",
            "permgate",
        ]

    def test_persist_without_store_is_silent(self):
        agent = _Agent(store=None)
        persist_model_selection(agent, "openai/gpt-5.4")
        persist_plugin_selection(agent, ["budget"])


class TestLoad:
    def test_load_model_selection_absent(self):
        agent = _Agent()
        assert load_model_selection(agent) is None

    def test_load_plugin_selection_malformed(self):
        store = _Store()
        store.state["alice:plugin_selection"] = "{not json"
        agent = _Agent(store=store)
        assert load_plugin_selection(agent) == []

    def test_load_plugin_selection_filters_non_strings(self):
        store = _Store()
        store.state["alice:plugin_selection"] = json.dumps(["budget", 42])
        agent = _Agent(store=store)
        assert load_plugin_selection(agent) == ["budget"]


class TestRestore:
    def test_restore_model_selection(self):
        store = _Store()
        store.state["alice:model_selection"] = "openai/gpt-5.4"
        agent = _Agent(store=store)
        restore_selections(agent)
        assert agent.switched == ["openai/gpt-5.4"]

    def test_restore_ignores_invalid_model(self):
        store = _Store()
        store.state["alice:model_selection"] = "bad/model"
        agent = _Agent(store=store)
        restore_selections(agent)
        assert agent.switched == []

    def test_restore_enables_persisted_plugins(self):
        store = _Store()
        store.state["alice:plugin_selection"] = json.dumps(["budget", "gone"])
        agent = _Agent(store=store, plugins=_PluginManager(["budget", "other"]))
        agent.plugins.disable("budget")
        restore_selections(agent)
        assert agent.plugins.is_enabled("budget")
        # Unknown plugin names are skipped, not fatal.
        assert agent.plugins.is_enabled("other") is False

    def test_restore_disables_defaults_user_had_off(self):
        store = _Store()
        store.state["alice:plugin_selection"] = json.dumps(["budget"])
        agent = _Agent(store=store, plugins=_PluginManager(["budget", "other"]))
        # "other" is a config default the user had disabled before save.
        restore_selections(agent)
        assert agent.plugins.is_enabled("budget")
        assert agent.plugins.is_enabled("other") is False

    def test_restore_no_plugin_snapshot_keeps_defaults(self):
        agent = _Agent(plugins=_PluginManager(["budget"]))
        restore_selections(agent)
        assert agent.plugins.is_enabled("budget")

    def test_restore_empty_snapshot_disables_all(self):
        store = _Store()
        store.state["alice:plugin_selection"] = json.dumps([])
        agent = _Agent(store=store, plugins=_PluginManager(["budget"]))
        restore_selections(agent)
        assert agent.plugins.is_enabled("budget") is False

    def test_restore_no_selections_is_noop(self):
        agent = _Agent(plugins=_PluginManager(["budget"]))
        restore_selections(agent)
        assert agent.switched == []
        assert agent.plugins.is_enabled("budget")
