"""Unit tests for :mod:`kohakuterrarium.bootstrap.tools`."""

import textwrap

import pytest

from kohakuterrarium.bootstrap.tools import (
    _coerce_tool_config_value,
    _lookup_trigger_class,
    _universal_trigger_classes,
    config_tool_origin,
    create_tool,
    init_tools,
)
from kohakuterrarium.core.config.types import (
    AgentConfig,
    ToolConfigItem,
)
from kohakuterrarium.core.loader import ModuleLoader
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.errors import ConfigError
from kohakuterrarium.modules.tool.origin import ToolOrigin
from kohakuterrarium.packages import locations as loc_mod

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
    def test_replay_override_is_a_framework_option(self):
        cfg = ToolConfigItem(name="image_gen", options={"request_replay": "forbid"})
        tool = create_tool(cfg, None, strict=True)
        assert tool.config.request_replay == "forbid"
        assert "request_replay" not in tool.config.extra

    def test_invalid_replay_override_is_rejected(self):
        cfg = ToolConfigItem(name="image_gen", options={"request_replay": "maybe"})
        with pytest.raises(ConfigError, match="request_replay"):
            create_tool(cfg, None, strict=True)

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

    def test_lenient_web_search_keeps_unavailable_config_editable(self, monkeypatch):
        from kohakuterrarium.builtins.tools.web import search as web_search

        monkeypatch.setattr(web_search, "has_api_key", lambda provider: False)
        cfg = ToolConfigItem(
            name="web_search",
            type="builtin",
            options={"backend": "deepseek"},
        )

        tool = create_tool(cfg, loader=None, strict=False)

        assert tool is not None
        assert tool.backend == "deepseek"


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

    @pytest.mark.parametrize("policy", [None, "allow", "forbid"])
    def test_load_success(self, tmp_path, policy):
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
            options={} if policy is None else {"request_replay": policy},
        )
        tool = create_tool(cfg, loader=loader)
        assert tool is not None
        assert tool.tool_name == "my_tool"
        assert tool.config.request_replay == policy


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
        # The unknown one was skipped without crashing (lenient default
        # of the bare factory — Agent construction passes strict=True).
        assert "definitely_no_tool" not in reg.list_tools()

    def test_registered_tools_carry_their_config_origin(self, tmp_path):
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
        cfg = AgentConfig(
            name="local-agent",
            agent_path=tmp_path,
            tools=[
                ToolConfigItem(name="bash", type="builtin"),
                ToolConfigItem(
                    name="my_tool",
                    type="custom",
                    module="custom/my_tool.py",
                    class_name="MyTool",
                ),
            ],
        )
        reg = Registry()
        init_tools(cfg, reg, loader=ModuleLoader(agent_path=tmp_path))
        assert reg.get_origin("bash") == ToolOrigin("builtin")
        assert reg.get_origin("my_tool") == ToolOrigin("custom", "local-agent")


# ── config_tool_origin ──────────────────────────────────────────


@pytest.fixture
def pkg_dir(tmp_path, monkeypatch):
    d = tmp_path / "packages"
    d.mkdir()
    monkeypatch.setattr(loc_mod, "PACKAGES_DIR", d)
    return d


class TestConfigToolOrigin:
    def _origin(self, item, agent_path=None, name="creature-a"):
        return config_tool_origin(item, AgentConfig(name=name, agent_path=agent_path))

    def test_builtin_and_trigger_tools_are_builtin(self):
        assert self._origin(ToolConfigItem(name="bash")).label == "builtin"
        assert (
            self._origin(ToolConfigItem(name="add_timer", type="trigger")).label
            == "builtin"
        )

    def test_package_module_shipped_by_an_installed_package(self, pkg_dir):
        (pkg_dir / "kt-biome" / "kt_biome").mkdir(parents=True)
        item = ToolConfigItem(
            name="t", type="package", module="kt_biome.tools.t", class_name="T"
        )
        assert self._origin(item) == ToolOrigin("package", "kt-biome")

    def test_package_module_no_package_claims_uses_its_top_module(self, pkg_dir):
        item = ToolConfigItem(
            name="t", type="package", module="thirdparty.tools", class_name="T"
        )
        assert self._origin(item).label == "package:thirdparty"

    def test_custom_package_reference(self, pkg_dir):
        item = ToolConfigItem(
            name="t", type="custom", module="@kt-biome/tools/t.py", class_name="T"
        )
        assert self._origin(item).label == "package:kt-biome"

    def test_custom_file_inside_an_installed_package(self, pkg_dir):
        creature = pkg_dir / "kt-biome" / "creatures" / "swe"
        creature.mkdir(parents=True)
        item = ToolConfigItem(
            name="t", type="custom", module="custom/t.py", class_name="T"
        )
        assert self._origin(item, agent_path=creature).label == "package:kt-biome"

    def test_custom_file_outside_packages_is_custom(self, pkg_dir, tmp_path):
        folder = tmp_path / "mine"
        folder.mkdir()
        item = ToolConfigItem(
            name="t", type="custom", module="custom/t.py", class_name="T"
        )
        assert self._origin(item, agent_path=folder).label == "custom:creature-a"
        absolute = ToolConfigItem(
            name="t", type="custom", module=str(folder / "t.py"), class_name="T"
        )
        assert self._origin(absolute).label == "custom:creature-a"

    def test_custom_without_location_is_custom(self, pkg_dir):
        no_path = ToolConfigItem(name="t", type="custom", module="t.py")
        assert self._origin(no_path).label == "custom:creature-a"
        no_module = ToolConfigItem(name="t", type="custom")
        assert self._origin(no_module).label == "custom:creature-a"
        local_ref = ToolConfigItem(name="t", type="custom", module="@/tools/t.py")
        assert self._origin(local_ref).label == "custom:creature-a"


class TestStrictMode:
    """E4: strict raises ConfigError instead of warn-skipping."""

    def test_unknown_builtin_raises(self):
        from kohakuterrarium.errors import ConfigError

        cfg = ToolConfigItem(name="definitely_no_tool", type="builtin")
        with pytest.raises(ConfigError, match="Unknown built-in tool"):
            create_tool(cfg, loader=None, strict=True)

    def test_unknown_type_raises(self):
        from kohakuterrarium.errors import ConfigError

        cfg = ToolConfigItem(name="x", type="not_a_real_type")
        with pytest.raises(ConfigError, match="Unknown tool type"):
            create_tool(cfg, loader=None, strict=True)

    def test_custom_missing_module_raises(self):
        from kohakuterrarium.errors import ConfigError

        cfg = ToolConfigItem(name="x", type="custom")
        with pytest.raises(ConfigError, match="missing"):
            create_tool(cfg, loader=None, strict=True)

    def test_invalid_config_value_raises(self):
        from kohakuterrarium.errors import ConfigError

        cfg = ToolConfigItem(name="bash", type="builtin", options={"timeout": "soon"})
        with pytest.raises(ConfigError, match="Invalid config value"):
            create_tool(cfg, loader=None, strict=True)

    def test_deepseek_search_without_key_raises(self, monkeypatch):
        from kohakuterrarium.builtins.tools.web import search as web_search
        from kohakuterrarium.errors import ConfigError

        monkeypatch.setattr(web_search, "has_api_key", lambda provider: False)
        cfg = ToolConfigItem(
            name="web_search",
            type="builtin",
            options={"backend": "deepseek"},
        )

        with pytest.raises(ConfigError, match="DeepSeek API key"):
            create_tool(cfg, loader=None, strict=True)

    def test_init_tools_strict_raises_on_first_bad(self):
        from kohakuterrarium.errors import ConfigError

        cfg = AgentConfig(
            name="a",
            tools=[ToolConfigItem(name="definitely_no_tool", type="builtin")],
        )
        with pytest.raises(ConfigError):
            init_tools(cfg, Registry(), loader=None, strict=True)
