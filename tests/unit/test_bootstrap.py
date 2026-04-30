"""
Tests for all 5 bootstrap modules and the tool_catalog deferred loader mechanism.

Covers:
- builtins/tool_catalog.py (registration, deferred loaders, lookup)
- bootstrap/llm.py (LLM provider factory)
- bootstrap/tools.py (tool creation and init)
- bootstrap/io.py (input/output module factories)
- bootstrap/subagents.py (sub-agent config creation and init)
- bootstrap/triggers.py (trigger creation and init)
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from kohakuterrarium.builtins.tool_catalog import (
    _BUILTIN_TOOLS,
    _DEFERRED_LOADERS,
    get_builtin_tool,
    is_builtin_tool,
    list_builtin_tools,
    register_builtin,
    register_deferred_loader,
)
from kohakuterrarium.core.config import (
    AgentConfig,
    InputConfig,
    OutputConfig,
    SubAgentConfigItem,
    ToolConfigItem,
    TriggerConfig,
)
from kohakuterrarium.core.loader import ModuleLoader, ModuleLoadError
from kohakuterrarium.modules.tool.base import BaseTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class DummyTool(BaseTool):
    """Minimal tool for testing registration and creation."""

    tool_name = "dummy"
    description = "A dummy tool for testing."

    async def execute(self, args, context=None):
        return "ok"


class AnotherDummyTool(BaseTool):
    """Second minimal tool to test deferred loading."""

    tool_name = "another_dummy"
    description = "Another dummy tool."

    async def execute(self, args, context=None):
        return "ok"


# ---------------------------------------------------------------------------
# tool_catalog.py
# ---------------------------------------------------------------------------


class TestToolCatalog:
    """Tests for the builtin tool catalog (registration, lookup, deferred loaders)."""

    @pytest.fixture(autouse=True)
    def _isolate_catalog(self):
        """Save and restore catalog state around each test."""
        saved_tools = dict(_BUILTIN_TOOLS)
        saved_loaders = list(_DEFERRED_LOADERS)
        yield
        _BUILTIN_TOOLS.clear()
        _BUILTIN_TOOLS.update(saved_tools)
        _DEFERRED_LOADERS.clear()
        _DEFERRED_LOADERS.extend(saved_loaders)

    def test_register_and_get_builtin_tool(self):
        _BUILTIN_TOOLS["test_dummy"] = DummyTool
        instance = get_builtin_tool("test_dummy")
        assert instance is not None
        assert isinstance(instance, DummyTool)

    def test_get_builtin_tool_returns_none_for_unknown(self):
        # Clear deferred loaders so the miss path returns None immediately
        _DEFERRED_LOADERS.clear()
        result = get_builtin_tool("nonexistent_tool_xyz")
        assert result is None

    def test_register_deferred_loader_fires_on_miss(self):
        """Deferred loader is invoked when a tool is not in the catalog."""

        def loader():
            _BUILTIN_TOOLS["lazy_tool"] = DummyTool

        _DEFERRED_LOADERS.clear()
        register_deferred_loader(loader)
        assert "lazy_tool" not in _BUILTIN_TOOLS

        instance = get_builtin_tool("lazy_tool")
        assert instance is not None
        assert isinstance(instance, DummyTool)

    def test_deferred_loaders_cleared_after_firing(self):
        """After deferred loaders fire once, they are removed."""
        call_count = 0

        def counting_loader():
            nonlocal call_count
            call_count += 1
            _BUILTIN_TOOLS["counted_tool"] = DummyTool

        _DEFERRED_LOADERS.clear()
        register_deferred_loader(counting_loader)

        get_builtin_tool("counted_tool")
        assert call_count == 1
        assert len(_DEFERRED_LOADERS) == 0

        # Second miss should NOT re-invoke the loader
        get_builtin_tool("some_other_missing")
        assert call_count == 1

    def test_is_builtin_tool(self):
        _BUILTIN_TOOLS["present"] = DummyTool
        assert is_builtin_tool("present") is True
        assert is_builtin_tool("absent_xyz") is False

    def test_list_builtin_tools(self):
        _BUILTIN_TOOLS.clear()
        _BUILTIN_TOOLS["alpha"] = DummyTool
        _BUILTIN_TOOLS["beta"] = AnotherDummyTool
        names = list_builtin_tools()
        assert set(names) == {"alpha", "beta"}

    def test_register_builtin_decorator(self):
        """The @register_builtin decorator registers the class."""
        _BUILTIN_TOOLS.pop("decorated_tool", None)

        @register_builtin("decorated_tool")
        class DecoratedTool(BaseTool):
            tool_name = "decorated_tool"
            description = "test"

            async def execute(self, args, context=None):
                return ""

        assert "decorated_tool" in _BUILTIN_TOOLS
        assert _BUILTIN_TOOLS["decorated_tool"] is DecoratedTool


# ---------------------------------------------------------------------------
# bootstrap/llm.py
# ---------------------------------------------------------------------------


class TestBootstrapLLM:
    """Tests for the LLM provider factory."""

    @patch("kohakuterrarium.bootstrap.llm.CodexOAuthProvider")
    def test_create_llm_provider_codex_oauth(self, mock_codex_cls):
        from kohakuterrarium.bootstrap.llm import create_llm_provider

        mock_instance = MagicMock()
        mock_codex_cls.return_value = mock_instance

        config = AgentConfig(
            name="test",
            auth_mode="codex-oauth",
            model="o3-mini",
            reasoning_effort="high",
        )
        result = create_llm_provider(config)

        mock_codex_cls.assert_called_once_with(
            model="o3-mini",
            reasoning_effort="high",
            service_tier=None,
            retry_policy=None,
        )
        assert result is mock_instance

    @patch("kohakuterrarium.bootstrap.llm.OpenAIProvider")
    def test_create_llm_provider_openai(self, mock_openai_cls):
        from kohakuterrarium.bootstrap.llm import create_llm_provider

        mock_instance = MagicMock()
        mock_openai_cls.return_value = mock_instance

        config = AgentConfig(
            name="test",
            auth_mode="api-key",
            api_key_env="TEST_KEY_FOR_BOOTSTRAP",
            base_url="https://example.com/v1",
            model="gpt-4o",
            provider="custom-inline",
            temperature=0.5,
            reasoning_effort="high",
            max_tokens=2048,
        )

        with patch.dict("os.environ", {"TEST_KEY_FOR_BOOTSTRAP": "sk-test123"}):
            result = create_llm_provider(config)

        mock_openai_cls.assert_called_once_with(
            api_key="sk-test123",
            base_url="https://example.com/v1",
            model="gpt-4o",
            temperature=0.5,
            max_tokens=2048,
            extra_body=None,
            retry_policy=None,
        )
        assert result is mock_instance

    def test_create_llm_provider_missing_api_key_raises(self):
        from kohakuterrarium.bootstrap.llm import create_llm_provider

        config = AgentConfig(
            name="test",
            model="test-model",
            auth_mode="api-key",
            api_key_env="NONEXISTENT_KEY_BOOTSTRAP_TEST_XYZ",
        )

        with (
            patch.dict("os.environ", {}, clear=False),
            patch("kohakuterrarium.llm.profiles._is_available", return_value=False),
        ):
            # Make sure the key is absent and no auto-default kicks in
            import os

            os.environ.pop("NONEXISTENT_KEY_BOOTSTRAP_TEST_XYZ", None)
            with pytest.raises(ValueError, match="API key not found"):
                create_llm_provider(config)

    @patch("kohakuterrarium.bootstrap.llm.resolve_controller_llm")
    def test_extract_controller_data_does_not_send_agent_defaults(self, mock_resolve):
        from kohakuterrarium.bootstrap.llm import create_llm_provider

        mock_resolve.return_value = None
        config = AgentConfig(name="test", llm_profile="gpt-5.4")

        with pytest.raises(ValueError, match="No LLM model configured"):
            create_llm_provider(config)

        controller_data = mock_resolve.call_args.args[0]
        assert controller_data == {"llm": "gpt-5.4"}

    @patch("kohakuterrarium.bootstrap.llm.resolve_controller_llm")
    def test_extract_controller_data_passes_extra_body_and_meaningful_overrides(
        self, mock_resolve
    ):
        from kohakuterrarium.bootstrap.llm import create_llm_provider
        from kohakuterrarium.llm.profile_types import LLMProfile

        mock_resolve.return_value = LLMProfile(
            name="x", model="gpt-5.4", provider="openai"
        )
        config = AgentConfig(
            name="test",
            llm_profile="gpt-5.4",
            reasoning_effort="high",
            extra_body={"reasoning": {"effort": "low"}},
            max_tokens=4096,
        )

        with patch(
            "kohakuterrarium.bootstrap.llm._create_from_profile",
            return_value=MagicMock(),
        ):
            create_llm_provider(config)

        controller_data = mock_resolve.call_args.args[0]
        assert controller_data["llm"] == "gpt-5.4"
        assert controller_data["reasoning_effort"] == "high"
        assert controller_data["max_tokens"] == 4096
        assert controller_data["extra_body"] == {"reasoning": {"effort": "low"}}

    @patch("kohakuterrarium.bootstrap.llm.resolve_controller_llm")
    def test_extract_controller_data_passes_retry_policy(self, mock_resolve):
        from kohakuterrarium.bootstrap.llm import create_llm_provider
        from kohakuterrarium.llm.profile_types import LLMProfile

        mock_resolve.return_value = LLMProfile(
            name="x", model="gpt-5.4", provider="openai"
        )
        config = AgentConfig(
            name="test",
            llm_profile="gpt-5.4",
            retry_policy={"max_retries": 1, "base_delay": 0},
        )

        with patch(
            "kohakuterrarium.bootstrap.llm._create_from_profile",
            return_value=MagicMock(),
        ):
            create_llm_provider(config)

        controller_data = mock_resolve.call_args.args[0]
        assert controller_data["retry_policy"] == {"max_retries": 1, "base_delay": 0}


# ---------------------------------------------------------------------------
# bootstrap/tools.py
# ---------------------------------------------------------------------------


class TestBootstrapTools:
    """Tests for tool creation and registration."""

    @pytest.fixture(autouse=True)
    def _isolate_catalog(self):
        """Save and restore catalog state around each test."""
        saved_tools = dict(_BUILTIN_TOOLS)
        saved_loaders = list(_DEFERRED_LOADERS)
        yield
        _BUILTIN_TOOLS.clear()
        _BUILTIN_TOOLS.update(saved_tools)
        _DEFERRED_LOADERS.clear()
        _DEFERRED_LOADERS.extend(saved_loaders)

    def test_create_tool_builtin_found(self):
        from kohakuterrarium.bootstrap.tools import create_tool

        _BUILTIN_TOOLS["test_bt"] = DummyTool
        _DEFERRED_LOADERS.clear()

        tc = ToolConfigItem(name="test_bt", type="builtin")
        result = create_tool(tc, loader=None)
        assert result is not None
        assert isinstance(result, DummyTool)

    def test_create_tool_builtin_not_found(self):
        from kohakuterrarium.bootstrap.tools import create_tool

        _DEFERRED_LOADERS.clear()

        tc = ToolConfigItem(name="missing_builtin_xyz", type="builtin")
        result = create_tool(tc, loader=None)
        assert result is None

    def test_create_tool_custom_missing_module(self):
        from kohakuterrarium.bootstrap.tools import create_tool

        tc = ToolConfigItem(
            name="custom_no_module",
            type="custom",
            module=None,
            class_name=None,
        )
        loader = MagicMock(spec=ModuleLoader)
        result = create_tool(tc, loader=loader)
        assert result is None

    def test_create_tool_custom_no_loader(self):
        from kohakuterrarium.bootstrap.tools import create_tool

        tc = ToolConfigItem(
            name="custom_no_loader",
            type="custom",
            module="./custom/tool.py",
            class_name="MyTool",
        )
        result = create_tool(tc, loader=None)
        assert result is None

    def test_create_tool_custom_loader_error(self):
        from kohakuterrarium.bootstrap.tools import create_tool

        loader = MagicMock(spec=ModuleLoader)
        loader.load_instance.side_effect = ModuleLoadError("not found")

        tc = ToolConfigItem(
            name="custom_err",
            type="custom",
            module="./custom/tool.py",
            class_name="MyTool",
        )
        result = create_tool(tc, loader=loader)
        assert result is None

    def test_create_tool_unknown_type(self):
        from kohakuterrarium.bootstrap.tools import create_tool

        tc = ToolConfigItem(name="weird", type="alien")
        result = create_tool(tc, loader=None)
        assert result is None

    def test_init_tools_registers_all(self):
        from kohakuterrarium.bootstrap.tools import init_tools
        from kohakuterrarium.core.registry import Registry

        _BUILTIN_TOOLS["reg_a"] = DummyTool
        _BUILTIN_TOOLS["reg_b"] = AnotherDummyTool
        _DEFERRED_LOADERS.clear()

        config = AgentConfig(
            name="test",
            tools=[
                ToolConfigItem(name="reg_a", type="builtin"),
                ToolConfigItem(name="reg_b", type="builtin"),
                ToolConfigItem(name="missing_xyz", type="builtin"),
            ],
        )
        registry = Registry()
        init_tools(config, registry, loader=None)

        registered = registry.list_tools()
        assert "dummy" in registered
        assert "another_dummy" in registered
        # Missing tool should not be registered (no error, just skipped)
        assert len(registered) == 2


# ---------------------------------------------------------------------------
# bootstrap/io.py
# ---------------------------------------------------------------------------


class TestBootstrapInput:
    """Tests for input module creation."""

    def test_create_input_with_override_returns_override(self):
        from kohakuterrarium.bootstrap.io import create_input
        from kohakuterrarium.modules.input.base import InputModule

        override = MagicMock(spec=InputModule)
        config = AgentConfig(name="test")
        result = create_input(config, input_override=override, loader=None)
        assert result is override

    def test_create_input_builtin_cli(self):
        from kohakuterrarium.bootstrap.io import create_input
        from kohakuterrarium.builtins.inputs import CLIInput

        config = AgentConfig(
            name="test",
            input=InputConfig(type="cli", prompt=">> "),
        )
        result = create_input(config, input_override=None, loader=None)
        assert isinstance(result, CLIInput)

    def test_create_input_unknown_type_fallback(self):
        from kohakuterrarium.bootstrap.io import create_input
        from kohakuterrarium.builtins.inputs import CLIInput

        config = AgentConfig(
            name="test",
            input=InputConfig(type="martian_input"),
        )
        result = create_input(config, input_override=None, loader=None)
        assert isinstance(result, CLIInput)


class TestBootstrapOutput:
    """Tests for output module creation."""

    def test_create_output_with_override(self):
        from kohakuterrarium.bootstrap.io import create_output
        from kohakuterrarium.modules.output.base import OutputModule

        override = MagicMock(spec=OutputModule)
        config = AgentConfig(name="test")
        default_out, named = create_output(
            config, output_override=override, loader=None
        )
        assert default_out is override
        assert named == {}

    def test_create_output_builtin_stdout(self):
        from kohakuterrarium.bootstrap.io import create_output
        from kohakuterrarium.builtins.outputs import StdoutOutput

        config = AgentConfig(
            name="test",
            output=OutputConfig(type="stdout"),
        )
        default_out, named = create_output(config, output_override=None, loader=None)
        assert isinstance(default_out, StdoutOutput)
        assert named == {}

    def test_create_output_unknown_type_fallback(self):
        from kohakuterrarium.bootstrap.io import create_output
        from kohakuterrarium.builtins.outputs import StdoutOutput

        config = AgentConfig(
            name="test",
            output=OutputConfig(type="quantum_output"),
        )
        default_out, named = create_output(config, output_override=None, loader=None)
        assert isinstance(default_out, StdoutOutput)


# ---------------------------------------------------------------------------
# bootstrap/subagents.py
# ---------------------------------------------------------------------------


class TestBootstrapSubagents:
    """Tests for sub-agent config creation."""

    def test_create_subagent_config_builtin(self):
        from kohakuterrarium.bootstrap.subagents import create_subagent_config

        item = SubAgentConfigItem(name="worker", type="builtin")

        result = create_subagent_config(item, loader=None)
        assert result is not None
        assert result.name == "worker"

    def test_create_subagent_config_builtin_not_found(self):
        from kohakuterrarium.bootstrap.subagents import create_subagent_config

        item = SubAgentConfigItem(name="nonexistent_agent_xyz", type="builtin")

        result = create_subagent_config(item, loader=None)
        assert result is None

    def test_create_subagent_config_unknown_type(self):
        from kohakuterrarium.bootstrap.subagents import create_subagent_config

        item = SubAgentConfigItem(name="mystery", type="alien")

        result = create_subagent_config(item, loader=None)
        assert result is None

    def test_create_subagent_config_custom_inline(self):
        from kohakuterrarium.bootstrap.subagents import create_subagent_config
        from kohakuterrarium.modules.subagent.config import SubAgentConfig

        item = SubAgentConfigItem(
            name="my_agent",
            type="custom",
            description="A custom agent",
            tools=["bash", "read"],
            can_modify=True,
            interactive=False,
            options={},
        )

        result = create_subagent_config(item, loader=None)
        assert result is not None
        assert isinstance(result, SubAgentConfig)
        assert result.name == "my_agent"

    def test_create_subagent_config_custom_inline_with_budget_plugin_options(self):
        from kohakuterrarium.bootstrap.subagents import create_subagent_config

        item = SubAgentConfigItem(
            name="inline_researcher",
            type="custom",
            tools=["read", "grep"],
            options={
                "system_prompt": "You are an inline sub-agent.",
                "default_plugins": ["auto-compact"],
                "plugins": [
                    {
                        "name": "budget",
                        "options": {
                            "turn_budget": [40, 60],
                            "tool_call_budget": {"soft": 75, "hard": 100},
                        },
                    }
                ],
            },
        )

        result = create_subagent_config(item, loader=None)
        assert result is not None
        assert result.name == "inline_researcher"
        assert result.system_prompt == "You are an inline sub-agent."
        assert result.default_plugins == ["auto-compact"]
        assert result.plugins == [
            {
                "name": "budget",
                "options": {
                    "turn_budget": [40, 60],
                    "tool_call_budget": {"soft": 75, "hard": 100},
                },
            }
        ]

    def test_create_subagent_config_custom_no_loader(self):
        from kohakuterrarium.bootstrap.subagents import create_subagent_config

        item = SubAgentConfigItem(
            name="custom_mod",
            type="custom",
            module="./custom/agent.py",
            config_name="MY_CONFIG",
        )

        result = create_subagent_config(item, loader=None)
        assert result is None

    def test_create_subagent_config_builtin_extra_prompt(self):
        from kohakuterrarium.bootstrap.subagents import create_subagent_config

        item = SubAgentConfigItem(
            name="worker",
            type="builtin",
            options={"extra_prompt": "Be extra careful."},
        )

        result = create_subagent_config(item, loader=None)
        assert result is not None
        assert result.extra_prompt == "Be extra careful."


# ---------------------------------------------------------------------------
# bootstrap/triggers.py
# ---------------------------------------------------------------------------


class TestBootstrapTriggers:
    """Tests for trigger creation."""

    def test_create_trigger_timer(self):
        from kohakuterrarium.bootstrap.triggers import create_trigger
        from kohakuterrarium.modules.trigger import TimerTrigger

        tc = TriggerConfig(
            type="timer",
            prompt="Check status",
            options={"interval": 30.0, "immediate": True},
        )
        result = create_trigger(tc, session=None, loader=None)
        assert result is not None
        assert isinstance(result, TimerTrigger)

    def test_create_trigger_context(self):
        from kohakuterrarium.bootstrap.triggers import create_trigger
        from kohakuterrarium.modules.trigger import ContextUpdateTrigger

        tc = TriggerConfig(
            type="context",
            prompt="Context changed",
            options={"debounce_ms": 200},
        )
        result = create_trigger(tc, session=None, loader=None)
        assert result is not None
        assert isinstance(result, ContextUpdateTrigger)

    def test_create_trigger_channel(self):
        from kohakuterrarium.bootstrap.triggers import create_trigger
        from kohakuterrarium.modules.trigger import ChannelTrigger

        tc = TriggerConfig(
            type="channel",
            prompt="Message received",
            options={"channel": "inbox", "filter_sender": "user"},
        )
        result = create_trigger(tc, session=None, loader=None)
        assert result is not None
        assert isinstance(result, ChannelTrigger)

    def test_create_trigger_unknown_type(self):
        from kohakuterrarium.bootstrap.triggers import create_trigger

        tc = TriggerConfig(
            type="quantum_trigger",
            prompt="Entangle",
            options={},
        )
        result = create_trigger(tc, session=None, loader=None)
        assert result is None

    def test_create_trigger_custom_no_loader(self):
        from kohakuterrarium.bootstrap.triggers import create_trigger

        tc = TriggerConfig(
            type="custom",
            module="./custom/trigger.py",
            class_name="MyTrigger",
            prompt="custom",
            options={},
        )
        result = create_trigger(tc, session=None, loader=None)
        assert result is None

    def test_create_trigger_custom_missing_module(self):
        from kohakuterrarium.bootstrap.triggers import create_trigger

        tc = TriggerConfig(
            type="custom",
            module=None,
            class_name=None,
            prompt="custom",
            options={},
        )
        result = create_trigger(tc, session=None, loader=None)
        assert result is None

    def test_init_triggers_registers_all(self):
        from kohakuterrarium.bootstrap.triggers import init_triggers
        from kohakuterrarium.core.trigger_manager import TriggerManager

        config = AgentConfig(
            name="test",
            triggers=[
                TriggerConfig(type="timer", prompt="tick", options={"interval": 10}),
                TriggerConfig(type="context", prompt="ctx", options={}),
                TriggerConfig(type="unknown_xyz", prompt="nope", options={}),
            ],
        )

        async def noop(event):
            pass

        manager = TriggerManager(noop)
        init_triggers(config, manager, session=None, loader=None)

        # timer and context should be registered; unknown should be skipped
        assert len(manager._triggers) == 2
        assert all(isinstance(ts, datetime) for ts in manager._created_at.values())
