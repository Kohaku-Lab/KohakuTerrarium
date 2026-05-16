"""Unit tests for :mod:`kohakuterrarium.core.config_types`."""

from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
    OutputConfigItem,
    SubAgentConfigItem,
    ToolConfigItem,
    TriggerConfig,
    _interpolate_env_vars,
)

# ── default factories isolated per instance ──────────────────────


class TestDefaultFactories:
    def test_input_options_independent(self):
        a = InputConfig()
        b = InputConfig()
        a.options["x"] = 1
        assert b.options == {}

    def test_agent_config_lists_independent(self):
        a = AgentConfig(name="a")
        b = AgentConfig(name="b")
        a.tools.append(ToolConfigItem(name="x"))
        assert b.tools == []
        a.variation_selections["k"] = "v"
        assert b.variation_selections == {}

    def test_output_config_named_outputs_independent(self):
        a = OutputConfig()
        b = OutputConfig()
        a.named_outputs["x"] = OutputConfigItem()
        assert b.named_outputs == {}


# ── dataclass shapes ─────────────────────────────────────────────


class TestDataclassDefaults:
    def test_input_config(self):
        c = InputConfig()
        assert c.type == "cli"
        assert c.prompt == "> "
        assert c.options == {}

    def test_trigger_config_requires_type(self):
        c = TriggerConfig(type="timer")
        assert c.name is None
        assert c.options == {}

    def test_tool_config_item_defaults(self):
        c = ToolConfigItem(name="bash")
        assert c.type == "builtin"
        assert c.options == {}

    def test_subagent_config_defaults(self):
        c = SubAgentConfigItem(name="explore")
        assert c.tools == []
        assert c.can_modify is False
        assert c.interactive is False

    def test_output_config_defaults(self):
        c = OutputConfig()
        assert c.type == "stdout"
        assert c.controller_direct is True

    def test_agent_config_defaults(self):
        c = AgentConfig(name="x")
        assert c.version == "1.0"
        assert c.temperature == 0.7
        assert c.skill_mode == "dynamic"
        assert c.include_tools_in_prompt is True
        assert c.sanitize_orphan_tool_calls is True
        assert c.tool_format == "bracket"
        assert isinstance(c.input, InputConfig)
        assert isinstance(c.output, OutputConfig)


# ── get_api_key ──────────────────────────────────────────────────


class TestGetApiKey:
    def test_returns_env(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "secret")
        c = AgentConfig(name="x", api_key_env="MY_KEY")
        assert c.get_api_key() == "secret"

    def test_missing_returns_none(self, monkeypatch):
        monkeypatch.delenv("ABSENT_KEY_XYZ", raising=False)
        c = AgentConfig(name="x", api_key_env="ABSENT_KEY_XYZ")
        assert c.get_api_key() is None


# ── _interpolate_env_vars ────────────────────────────────────────


class TestInterpolation:
    def test_str_passthrough(self, monkeypatch):
        monkeypatch.setenv("X", "ok")
        assert _interpolate_env_vars("hello ${X}") == "hello ok"

    def test_default_when_missing(self, monkeypatch):
        monkeypatch.delenv("MISSING_X", raising=False)
        assert _interpolate_env_vars("${MISSING_X:fallback}") == "fallback"

    def test_missing_no_default_becomes_empty(self, monkeypatch):
        monkeypatch.delenv("MISSING_Y", raising=False)
        assert _interpolate_env_vars("${MISSING_Y}") == ""

    def test_no_var_returned_verbatim(self):
        assert _interpolate_env_vars("plain") == "plain"

    def test_dict_recursive(self, monkeypatch):
        monkeypatch.setenv("K", "v")
        out = _interpolate_env_vars({"a": "${K}", "b": {"c": "${K}"}})
        assert out == {"a": "v", "b": {"c": "v"}}

    def test_list_recursive(self, monkeypatch):
        monkeypatch.setenv("Z", "zz")
        assert _interpolate_env_vars(["${Z}", "plain"]) == ["zz", "plain"]

    def test_non_str_non_collection_untouched(self):
        assert _interpolate_env_vars(42) == 42
        assert _interpolate_env_vars(None) is None
        assert _interpolate_env_vars(True) is True

    def test_multiple_vars_in_one_string(self, monkeypatch):
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        assert _interpolate_env_vars("${A}-${B}") == "1-2"
