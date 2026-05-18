"""Unit tests for :mod:`kohakuterrarium.bootstrap.tools`."""

import textwrap

import pytest

from kohakuterrarium.bootstrap.tools import (
    _coerce_tool_config_value,
    _lookup_trigger_class,
    _universal_trigger_classes,
    create_tool,
    init_tools,
)
from kohakuterrarium.core.config_types import (
    AgentConfig,
    ToolConfigItem,
)
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.core.registry import Registry

# ── _coerce_tool_config_value ────────────────────────────────────


class TestCoerceToolConfigValue:
    def test_max_output_int(self):
        assert _coerce_tool_config_value("max_output", "100") == 100

    def test_max_output_invalid_raises(self):
        with pytest.raises(ValueError, match="integer"):
            _coerce_tool_config_value("max_output", "xx")

    def test_max_output_negative_raises(self):
        with pytest.raises(ValueError, match=">= 0"):
            _coerce_tool_config_value("max_output", -1)

    def test_timeout_float(self):
        assert _coerce_tool_config_value("timeout", "5.5") == 5.5

    def test_timeout_invalid_raises(self):
        with pytest.raises(ValueError, match="numeric"):
            _coerce_tool_config_value("timeout", "x")

    def test_timeout_negative_raises(self):
        with pytest.raises(ValueError, match=">= 0"):
            _coerce_tool_config_value("timeout", -1.0)

    def test_unknown_key_passthrough(self):
        assert _coerce_tool_config_value("unknown", "x") == "x"


# ── _universal_trigger_classes / _lookup_trigger_class ──────────


class TestUniversalTriggers:
    def test_lookup_finds_a_universal_trigger(self):
        # Every class returned by ``_universal_trigger_classes`` must be
        # resolvable by its ``setup_tool_name`` via ``_lookup_trigger_class``
        # — the two helpers share one registry.
        classes = _universal_trigger_classes()
        assert classes, "expected at least one universal trigger class"
        for cls in classes:
            assert _lookup_trigger_class(cls.setup_tool_name) is cls

    def test_lookup_unknown_returns_none(self):
        assert _lookup_trigger_class("definitely_no_such_trigger_xyz") is None


# ── create_tool: builtin ────────────────────────────────────────


class TestCreateToolBuiltin:
    def test_unknown_builtin_returns_none(self):
        cfg = ToolConfigItem(name="definitely_no_tool", type="builtin")
        assert create_tool(cfg, loader=None) is None

    def test_invalid_max_output_returns_none(self):
        cfg = ToolConfigItem(
            name="bash",
            type="builtin",
            options={"max_output": "not-a-number"},
        )
        assert create_tool(cfg, loader=None) is None

    def test_builtin_with_valid_options(self):
        cfg = ToolConfigItem(
            name="bash",
            type="builtin",
            options={"max_output": 1024, "timeout": 30.0},
        )
        tool = create_tool(cfg, loader=None)
        # bash is a real builtin — it MUST resolve, and the coerced options
        # MUST land on the tool's config.
        assert tool is not None
        assert tool.tool_name == "bash"
        assert tool.config.max_output == 1024
        assert tool.config.timeout == 30.0


# ── create_tool: trigger ────────────────────────────────────────


class TestCreateToolTrigger:
    def test_unknown_trigger_returns_none(self):
        cfg = ToolConfigItem(name="no_such_trigger_xyz", type="trigger")
        assert create_tool(cfg, loader=None) is None

    def test_known_trigger_builds_callable_trigger_tool(self):
        from kohakuterrarium.modules.trigger.callable import CallableTriggerTool

        # Pick a real universal trigger by its setup_tool_name.
        trigger_cls = _universal_trigger_classes()[0]
        cfg = ToolConfigItem(name=trigger_cls.setup_tool_name, type="trigger")
        tool = create_tool(cfg, loader=None)
        # The trigger resolves to a CallableTriggerTool wrapping that class.
        assert isinstance(tool, CallableTriggerTool)


# ── create_tool: custom/package ─────────────────────────────────


class TestCreateToolCustom:
    def test_missing_module_returns_none(self):
        cfg = ToolConfigItem(name="x", type="custom", module=None, class_name="X")
        assert create_tool(cfg, loader=None) is None

    def test_no_loader_returns_none(self):
        cfg = ToolConfigItem(name="x", type="custom", module="m.py", class_name="X")
        assert create_tool(cfg, loader=None) is None

    def test_load_failure_returns_none(self, tmp_path):
        loader = ModuleLoader(agent_path=tmp_path)
        cfg = ToolConfigItem(
            name="x",
            type="custom",
            module="nope/missing.py",
            class_name="X",
        )
        assert create_tool(cfg, loader=loader) is None

    def test_load_success(self, tmp_path):
        custom = tmp_path / "custom"
        custom.mkdir()
        (custom / "my_tool.py").write_text(textwrap.dedent("""
                from kohakuterrarium.modules.tool.base import BaseTool, ToolResult

                class MyTool(BaseTool):
                    @property
                    def tool_name(self):
                        return "my_tool"

                    @property
                    def description(self):
                        return "my tool"

                    async def _execute(self, args, **kwargs):
                        return ToolResult(output="x")
                """))
        loader = ModuleLoader(agent_path=tmp_path)
        cfg = ToolConfigItem(
            name="my_tool",
            type="custom",
            module="custom/my_tool.py",
            class_name="MyTool",
        )
        tool = create_tool(cfg, loader=loader)
        assert tool is not None
        assert tool.tool_name == "my_tool"


# ── create_tool: unknown type ───────────────────────────────────


class TestCreateToolUnknownType:
    def test_unknown_type_returns_none(self):
        cfg = ToolConfigItem(name="x", type="not_a_real_type")
        assert create_tool(cfg, loader=None) is None


# ── init_tools ──────────────────────────────────────────────────


class TestInitTools:
    def test_registers_successful_tools(self):
        cfg = AgentConfig(
            name="a",
            tools=[ToolConfigItem(name="bash", type="builtin")],
        )
        reg = Registry()
        init_tools(cfg, reg, loader=None)
        # bash is a real builtin — it MUST be registered, and the registered
        # object MUST be the bash tool.
        assert "bash" in reg.list_tools()
        assert reg.get_tool("bash").tool_name == "bash"

    def test_skips_failed_tools(self):
        cfg = AgentConfig(
            name="a",
            tools=[
                ToolConfigItem(name="definitely_no_tool", type="builtin"),
                ToolConfigItem(name="bash", type="builtin"),
            ],
        )
        reg = Registry()
        init_tools(cfg, reg, loader=None)
        # The unknown one was skipped without crashing.
        assert "definitely_no_tool" not in reg.list_tools()
