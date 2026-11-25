"""Unit tests for :mod:`kohakuterrarium.core.agent.history.selection`."""

import json

from kohakuterrarium import Agent
from kohakuterrarium.core.agent.history.selection import (
    load_model_selection,
    load_plugin_selection,
    load_tool_selection,
    persist_model_selection,
    persist_plugin_selection,
    persist_tool_selection,
    restore_selections,
)
from kohakuterrarium.session.store import SessionStore
from kohakuterrarium.testing.llm import ScriptedLLM


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
        self.restored_tools = []

    def restore_disabled_tools(self, names):
        self.restored_tools.append(list(names))

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
        names, ok = load_plugin_selection(agent)
        assert names == [] and ok is False

    def test_load_plugin_selection_blank_value_is_not_empty_snapshot(self):
        # A present-but-blank key is corruption, not "user disabled all".
        for blank in (None, ""):
            store = _Store()
            store.state["alice:plugin_selection"] = blank
            agent = _Agent(store=store)
            names, ok = load_plugin_selection(agent)
            assert names == [] and ok is False

    def test_load_plugin_selection_absent(self):
        agent = _Agent()
        names, ok = load_plugin_selection(agent)
        assert names == [] and ok is False

    def test_load_plugin_selection_empty_ok(self):
        store = _Store()
        store.state["alice:plugin_selection"] = json.dumps([])
        agent = _Agent(store=store)
        names, ok = load_plugin_selection(agent)
        assert names == [] and ok is True

    def test_load_plugin_selection_filters_non_strings(self):
        store = _Store()
        store.state["alice:plugin_selection"] = json.dumps(["budget", 42])
        agent = _Agent(store=store)
        names, ok = load_plugin_selection(agent)
        assert names == ["budget"] and ok is True


class TestRestore:
    def test_restore_model_selection(self):
        store = _Store()
        store.state["alice:model_selection"] = "openai/gpt-5.4"
        agent = _Agent(store=store)
        restore_selections(agent)
        assert agent.switched == ["openai/gpt-5.4"]

    def test_restore_model_selection_with_explicit_store(self):
        # Resume calls restore before attach_session_store; the store must
        # be passable explicitly and still restore the model.
        store = _Store()
        store.state["alice:model_selection"] = "openai/gpt-5.4"
        agent = _Agent(store=None)  # session_store not yet attached
        restore_selections(agent, store)
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

    def test_restore_malformed_plugin_snapshot_keeps_defaults(self):
        # A corrupted snapshot must be treated like "never saved": do not
        # disable every plugin.
        store = _Store()
        store.state["alice:plugin_selection"] = "{not json"
        agent = _Agent(store=store, plugins=_PluginManager(["budget"]))
        restore_selections(agent)
        assert agent.plugins.is_enabled("budget") is True

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


class TestToolSelection:
    def test_persist_tool_selection_sorted_json(self):
        agent = _Agent()
        persist_tool_selection(agent, ["web_fetch", "bash"])
        assert json.loads(agent.session_store.state["alice:disabled_tools"]) == [
            "bash",
            "web_fetch",
        ]

    def test_persist_tool_selection_without_store_is_silent(self):
        agent = _Agent()
        agent.session_store = None
        persist_tool_selection(agent, ["bash"])

    def test_load_tool_selection_round_trip_and_absent(self):
        agent = _Agent()
        assert load_tool_selection(agent) == ([], False)
        persist_tool_selection(agent, [])
        assert load_tool_selection(agent) == ([], True)
        persist_tool_selection(agent, ["bash"])
        assert load_tool_selection(agent) == (["bash"], True)

    def test_restore_applies_saved_tool_names(self):
        agent = _Agent()
        agent.session_store.state["alice:disabled_tools"] = json.dumps(["bash"])
        restore_selections(agent)
        assert agent.restored_tools == [["bash"]]

    def test_restore_without_tool_snapshot_leaves_tools_alone(self):
        agent = _Agent()
        agent.session_store.state["alice:disabled_tools"] = "not json"
        restore_selections(agent)
        assert agent.restored_tools == []


class TestRuntimeToggleIsPersisted:
    async def test_enabling_a_plugin_outside_the_toggle_api_survives_resume(
        self, tmp_path
    ):
        config = tmp_path / "config.yaml"
        config.write_text(
            "name: picker\nsystem_prompt: offline\ninput: {type: none}\n"
            "output: {type: stdout}\n"
        )
        agent = await Agent.build(
            str(config), llm=ScriptedLLM(["OK"]), io="headless", pwd=tmp_path
        )
        store = SessionStore(str(tmp_path / "s.kohakutr"))
        try:
            agent.attach_session_store(store)
            name = next(
                p["name"] for p in agent.plugins.list_plugins() if not p["enabled"]
            )

            agent.plugins.enable(name)
            enabled, ok = load_plugin_selection(agent, store)
            assert ok and name in enabled

            agent.plugins.disable(name)
            enabled, ok = load_plugin_selection(agent, store)
            assert ok and name not in enabled
        finally:
            store.close(update_status=False)
