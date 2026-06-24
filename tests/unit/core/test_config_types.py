"""Unit tests for :mod:`kohakuterrarium.core.config_types`."""

from kohakuterrarium.core.config_types import (
    AgentConfig,
    InputConfig,
    OutputConfig,
    OutputConfigItem,
    SubAgentConfigItem,
    ToolConfigItem,
    TriggerConfig,
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
