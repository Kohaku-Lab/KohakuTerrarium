"""Tests for the ``/module`` slash command.

Covers parsing, dispatch, and the interaction with
:class:`PluginManager` / :class:`PluginOptions` /
:class:`NativeToolOptions` helpers in-process — so the helpers must
already be set up correctly. Mocks the agent surface narrowly: only
the attributes the command actually reads.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from kohakuterrarium.builtins.user_commands import get_builtin_user_command
from kohakuterrarium.builtins.user_commands.module import (
    _inventory,
    _parse_value,
    _resolve_or_error,
)
from kohakuterrarium.modules.user_command.base import UserCommandContext

# ── Fixtures ─────────────────────────────────────────────────────


def _make_agent(
    *,
    plugin_listing: list[dict[str, Any]] | None = None,
    plugin_options_set: Any | None = None,
    native_tools: list[dict[str, Any]] | None = None,
    native_options: dict[str, dict[str, Any]] | None = None,
) -> Any:
    """Build a minimal agent stub with the surface ``/module`` reads.

    Only the attributes the command actually touches are populated:

    - ``agent.plugins`` — plugin manager-like with
      ``list_plugins_with_options``, ``is_enabled``, ``enable``,
      ``disable``, ``load_pending``.
    - ``agent.plugin_options.set(name, values)``
    - ``agent.registry`` — for native_tool listing
    - ``agent.native_tool_options.get/set``
    """
    agent = MagicMock()

    plugin_listing = plugin_listing or []
    agent.plugins.list_plugins_with_options.return_value = plugin_listing
    agent.plugins.is_enabled.side_effect = lambda n: any(
        p["name"] == n and p.get("enabled") for p in plugin_listing
    )
    agent.plugins.enable.return_value = True
    agent.plugins.disable.return_value = True

    async def _load_pending():
        return None

    agent.plugins.load_pending.side_effect = _load_pending

    if plugin_options_set is None:
        plugin_options_set = MagicMock(side_effect=lambda name, values: dict(values))
    agent.plugin_options.set = plugin_options_set

    native_tools = native_tools or []
    native_options = native_options or {}

    class _ToolStub:
        def __init__(self, name: str, schema: dict[str, Any], description: str = ""):
            self.name = name
            self.description = description
            self.is_provider_native = True
            self._schema = schema

        @classmethod
        def make(
            cls,
            name: str,
            schema: dict[str, Any],
            description: str = "",
        ) -> "_ToolStub":
            t = cls(name, schema, description)

            class _Cls:
                @staticmethod
                def provider_native_option_schema():
                    return schema

            t.__class__ = type(name, (cls,), {})
            t.__class__.provider_native_option_schema = staticmethod(lambda s=schema: s)
            return t

    agent.registry.list_tools.return_value = [t["name"] for t in native_tools]
    tool_objs = {
        t["name"]: _ToolStub.make(t["name"], t["schema"]) for t in native_tools
    }
    agent.registry.get_tool.side_effect = lambda n: tool_objs.get(n)

    agent.native_tool_options.get.side_effect = lambda n: dict(
        native_options.get(n, {})
    )

    def _native_set(name, values):
        native_options[name] = dict(values)
        return dict(values)

    agent.native_tool_options.set.side_effect = _native_set
    return agent


@pytest.fixture
def cmd():
    return get_builtin_user_command("module")


@pytest.fixture
def basic_agent():
    return _make_agent(
        plugin_listing=[
            {
                "name": "permgate",
                "priority": 100,
                "enabled": True,
                "description": "Pause tools",
                "schema": {
                    "surface": {
                        "type": "enum",
                        "values": ["modal", "chat"],
                        "default": "modal",
                    },
                    "timeout_s": {"type": "float", "default": None},
                    "gated_tools": {
                        "type": "list",
                        "item_type": "string",
                        "default": [],
                    },
                },
                "options": {
                    "surface": "modal",
                    "timeout_s": None,
                    "gated_tools": [],
                },
            },
            {
                "name": "budget",
                "priority": 5,
                "enabled": False,
                "description": "Multi-axis budget",
                "schema": {"turn_budget": {"type": "dict", "default": None}},
                "options": {"turn_budget": None},
            },
        ],
        native_tools=[
            {
                "name": "image_gen",
                "schema": {
                    "size": {
                        "type": "string",
                        "default": "1024x1024",
                    }
                },
            },
        ],
    )


# ── Helpers ──────────────────────────────────────────────────────


class TestParseValue:
    def test_string_when_bare(self):
        assert _parse_value("modal") == "modal"

    def test_int(self):
        assert _parse_value("30") == 30

    def test_float(self):
        assert _parse_value("3.14") == 3.14

    def test_negative(self):
        assert _parse_value("-1") == -1

    def test_json_literals(self):
        assert _parse_value("true") is True
        assert _parse_value("false") is False
        assert _parse_value("null") is None

    def test_json_list(self):
        assert _parse_value('["bash","write"]') == ["bash", "write"]

    def test_json_dict(self):
        assert _parse_value('{"soft": 30, "hard": 50}') == {"soft": 30, "hard": 50}

    def test_quoted_string(self):
        # A quoted-looking string that fails JSON parse falls back to raw.
        assert _parse_value('"hello world"') == "hello world"

    def test_empty(self):
        assert _parse_value("") is None
        assert _parse_value("   ") is None


class TestResolve:
    def test_unique_name(self, basic_agent):
        m, err = _resolve_or_error(basic_agent, "permgate")
        assert err is None
        assert m["type"] == "plugin"

    def test_explicit_type_prefix(self, basic_agent):
        m, err = _resolve_or_error(basic_agent, "native_tool/image_gen")
        assert err is None
        assert m["type"] == "native_tool"

    def test_missing(self, basic_agent):
        m, err = _resolve_or_error(basic_agent, "ghost")
        assert m is None
        assert "not found" in err.lower()

    def test_inventory_includes_all_types(self, basic_agent):
        inv = _inventory(basic_agent)
        types = {m["type"] for m in inv}
        assert types == {"plugin", "native_tool"}


# ── Subcommand dispatch ──────────────────────────────────────────


class TestList:
    async def test_default_lists_grouped(self, cmd, basic_agent):
        result = await cmd.execute("", UserCommandContext(agent=basic_agent))
        assert result.success
        # Plugins section header + Enabled/Disabled subheaders.
        assert "Plugins" in result.output
        assert "Enabled" in result.output
        assert "Disabled" in result.output
        # Native tools section.
        assert "Native tools" in result.output
        # Module names.
        assert "permgate" in result.output
        assert "budget" in result.output
        assert "image_gen" in result.output

    async def test_filter_by_type(self, cmd, basic_agent):
        result = await cmd.execute(
            "list native_tool", UserCommandContext(agent=basic_agent)
        )
        assert result.success
        assert "image_gen" in result.output
        assert "permgate" not in result.output


class TestShow:
    async def test_show_renders_options(self, cmd, basic_agent):
        result = await cmd.execute(
            "show permgate", UserCommandContext(agent=basic_agent)
        )
        assert result.success
        assert "permgate" in result.output
        assert "surface" in result.output
        assert "modal" in result.output
        assert "Pause tools" in result.output

    async def test_show_missing(self, cmd, basic_agent):
        result = await cmd.execute("show ghost", UserCommandContext(agent=basic_agent))
        assert not result.success
        assert "not found" in result.error.lower()


class TestToggle:
    async def test_enable_calls_manager(self, cmd, basic_agent):
        result = await cmd.execute(
            "enable budget", UserCommandContext(agent=basic_agent)
        )
        assert result.success
        basic_agent.plugins.enable.assert_called_with("budget")

    async def test_disable_calls_manager(self, cmd, basic_agent):
        result = await cmd.execute(
            "disable permgate", UserCommandContext(agent=basic_agent)
        )
        assert result.success
        basic_agent.plugins.disable.assert_called_with("permgate")

    async def test_toggle_native_tool_rejected(self, cmd, basic_agent):
        result = await cmd.execute(
            "enable image_gen", UserCommandContext(agent=basic_agent)
        )
        assert not result.success
        assert "only plugins" in result.error.lower()


class TestSet:
    async def test_set_plugin_option(self, cmd, basic_agent):
        result = await cmd.execute(
            "set permgate surface chat",
            UserCommandContext(agent=basic_agent),
        )
        assert result.success, result.error
        basic_agent.plugin_options.set.assert_called_with(
            "permgate", {"surface": "chat"}
        )

    async def test_set_native_tool_option(self, cmd, basic_agent):
        result = await cmd.execute(
            "set image_gen size 2048x2048",
            UserCommandContext(agent=basic_agent),
        )
        assert result.success
        # native_tool_options.set is called with merged dict, not the
        # incremental update — verify by checking the value made it in.
        args, _ = basic_agent.native_tool_options.set.call_args
        assert args[0] == "image_gen"
        assert args[1] == {"size": "2048x2048"}

    async def test_set_with_json_list(self, cmd, basic_agent):
        # User must single-quote JSON in shell-style so shlex preserves
        # the inner double quotes — same as ``/module set foo bar
        # '[...]'`` in a real shell.
        result = await cmd.execute(
            'set permgate gated_tools \'["bash","write"]\'',
            UserCommandContext(agent=basic_agent),
        )
        assert result.success, result.error
        basic_agent.plugin_options.set.assert_called_with(
            "permgate", {"gated_tools": ["bash", "write"]}
        )

    async def test_set_int_value(self, cmd, basic_agent):
        result = await cmd.execute(
            "set permgate timeout_s 30",
            UserCommandContext(agent=basic_agent),
        )
        assert result.success
        basic_agent.plugin_options.set.assert_called_with("permgate", {"timeout_s": 30})

    async def test_set_missing_args(self, cmd, basic_agent):
        result = await cmd.execute(
            "set permgate", UserCommandContext(agent=basic_agent)
        )
        assert not result.success
        assert "Usage" in result.error


class TestReset:
    async def test_reset_one_key_to_default(self, cmd, basic_agent):
        result = await cmd.execute(
            "reset permgate surface", UserCommandContext(agent=basic_agent)
        )
        assert result.success
        basic_agent.plugin_options.set.assert_called_with(
            "permgate", {"surface": "modal"}
        )

    async def test_reset_all_native_clears(self, cmd, basic_agent):
        result = await cmd.execute(
            "reset native_tool/image_gen", UserCommandContext(agent=basic_agent)
        )
        assert result.success
        basic_agent.native_tool_options.set.assert_called_with("image_gen", {})


class TestUnknownSubcommand:
    async def test_unknown_returns_error(self, cmd, basic_agent):
        result = await cmd.execute("foobar", UserCommandContext(agent=basic_agent))
        assert not result.success
        assert "Unknown subcommand" in result.error


# ── Edit ($EDITOR) — tested via internal helper, not a real spawn ──


class TestEdit:
    async def test_edit_no_editor(self, cmd, basic_agent, monkeypatch):
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.delenv("VISUAL", raising=False)
        result = await cmd.execute(
            "edit permgate", UserCommandContext(agent=basic_agent)
        )
        assert not result.success
        assert "EDITOR" in result.error

    async def test_edit_no_options_short_circuits(self, cmd, monkeypatch):
        agent = _make_agent(
            plugin_listing=[
                {
                    "name": "compact.auto",
                    "priority": 30,
                    "enabled": True,
                    "description": "Auto compact",
                    "schema": {},
                    "options": {},
                }
            ]
        )
        monkeypatch.setenv("EDITOR", "true")
        result = await cmd.execute("edit compact.auto", UserCommandContext(agent=agent))
        assert result.success
        assert "no runtime-mutable options" in result.output
