"""Unit tests for :mod:`kohakuterrarium.core.registry`."""

from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.tool.base import ExecutionMode, ToolResult
from kohakuterrarium.modules.tool.origin import BUILTIN_ORIGIN, ToolOrigin

# ── helpers ──────────────────────────────────────────────────────


class _FakeTool:
    """Minimal Tool-protocol-compatible test double."""

    def __init__(self, name: str = "fake", desc: str = "A fake tool"):
        self._name = name
        self._desc = desc

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._desc

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def execute(self, args, context=None) -> ToolResult:
        return ToolResult(output="ok")


# ── Registry — tools ─────────────────────────────────────────────


class TestRegistryTools:
    def test_register_and_get(self):
        r = Registry()
        t = _FakeTool(name="foo")
        r.register_tool(t)
        assert r.get_tool("foo") is t

    def test_unknown_tool_returns_none(self):
        r = Registry()
        assert r.get_tool("missing") is None

    def test_tool_info_recorded(self):
        r = Registry()
        r.register_tool(_FakeTool(name="foo", desc="Foo desc"))
        info = r.get_tool_info("foo")
        assert info is not None
        assert info.tool_name == "foo"
        assert info.description == "Foo desc"
        assert info.execution_mode is ExecutionMode.DIRECT

    def test_re_register_overwrites(self):
        r = Registry()
        r.register_tool(_FakeTool(name="x", desc="one"))
        r.register_tool(_FakeTool(name="x", desc="two"))
        assert r.get_tool_info("x").description == "two"

    def test_list_tools(self):
        r = Registry()
        r.register_tool(_FakeTool(name="a"))
        r.register_tool(_FakeTool(name="b"))
        names = r.list_tools()
        assert set(names) == {"a", "b"}

    def test_unregister_existing(self):
        r = Registry()
        r.register_tool(_FakeTool(name="x"))
        assert r.unregister_tool("x") is True
        assert r.get_tool("x") is None
        assert r.get_tool_info("x") is None

    def test_unregister_absent_returns_false(self):
        r = Registry()
        assert r.unregister_tool("ghost") is False


class TestToolsPrompt:
    def test_empty_when_no_tools(self):
        r = Registry()
        assert r.get_tools_prompt() == ""

    def test_renders_lines(self):
        r = Registry()
        r.register_tool(_FakeTool(name="a", desc="alpha"))
        r.register_tool(_FakeTool(name="b", desc="beta"))
        out = r.get_tools_prompt()
        assert out.splitlines()[0] == "## Available Tools"
        assert "- a: alpha" in out
        assert "- b: beta" in out


# ── Registry — commands ──────────────────────────────────────────


class TestRegistryCommands:
    def test_register_and_get(self):
        r = Registry()

        def handler():
            return "ok"

        r.register_command("read", handler)
        assert r.get_command("read") is handler

    def test_unknown_command_none(self):
        assert Registry().get_command("nope") is None

    def test_list_commands(self):
        r = Registry()
        r.register_command("a", lambda: 1)
        r.register_command("b", lambda: 2)
        assert set(r.list_commands()) == {"a", "b"}


# ── Registry — subagents ─────────────────────────────────────────


class TestRegistrySubAgents:
    def test_register_and_get(self):
        r = Registry()
        obj = object()
        r.register_subagent("sa", obj)
        assert r.get_subagent("sa") is obj

    def test_unknown_subagent(self):
        assert Registry().get_subagent("x") is None

    def test_list(self):
        r = Registry()
        r.register_subagent("a", 1)
        r.register_subagent("b", 2)
        assert set(r.list_subagents()) == {"a", "b"}


# ── Registry — clear ─────────────────────────────────────────────


class TestToolOrigins:
    def test_origin_defaults_to_builtin(self):
        r = Registry()
        r.register_tool(_FakeTool(name="read"))
        assert r.get_origin("read") == BUILTIN_ORIGIN

    def test_explicit_origin_is_stored(self):
        r = Registry()
        r.register_tool(_FakeTool(name="lab"), origin=ToolOrigin("mcp", "lab"))
        assert r.get_origin("lab").label == "mcp:lab"

    def test_reregistering_replaces_the_origin(self):
        r = Registry()
        r.register_tool(_FakeTool(name="t"), origin=ToolOrigin("package", "p"))
        r.register_tool(_FakeTool(name="t"), origin=ToolOrigin("runtime"))
        assert r.get_origin("t") == ToolOrigin("runtime")

    def test_unregister_drops_the_origin(self):
        r = Registry()
        r.register_tool(_FakeTool(name="t"), origin=ToolOrigin("plugin", "x"))
        r.unregister_tool("t")
        assert r.get_origin("t") is None

    def test_unknown_tool_has_no_origin(self):
        assert Registry().get_origin("ghost") is None


class TestToolEnabled:
    def test_tools_start_enabled(self):
        r = Registry()
        r.register_tool(_FakeTool(name="t"))
        assert r.is_tool_enabled("t") is True
        assert r.disabled_tools == frozenset()

    def test_turning_off_and_on_reports_changes(self):
        r = Registry()
        r.register_tool(_FakeTool(name="t"))
        assert r.set_tool_enabled("t", False) is True
        assert r.set_tool_enabled("t", False) is False
        assert r.is_tool_enabled("t") is False
        assert r.disabled_tools == frozenset({"t"})
        assert r.set_tool_enabled("t", True) is True
        assert r.set_tool_enabled("t", True) is False
        assert r.is_tool_enabled("t") is True

    def test_off_state_survives_unregister_and_reregister(self):
        r = Registry()
        r.register_tool(_FakeTool(name="t"))
        r.set_tool_enabled("t", False)
        r.unregister_tool("t")
        assert r.disabled_tools == frozenset({"t"})
        r.register_tool(_FakeTool(name="t"))
        assert r.is_tool_enabled("t") is False

    def test_disabled_tools_is_a_snapshot(self):
        r = Registry()
        r.set_tool_enabled("t", False)
        view = r.disabled_tools
        r.set_tool_enabled("t", True)
        assert view == frozenset({"t"})
        assert r.disabled_tools == frozenset()


class TestClear:
    def test_clear_resets_everything(self):
        r = Registry()
        r.register_tool(_FakeTool(name="t"), origin=ToolOrigin("runtime"))
        r.set_tool_enabled("t", False)
        r.register_command("c", lambda: 1)
        r.register_subagent("s", object())
        r.clear()
        assert r.list_tools() == []
        assert r.list_commands() == []
        assert r.list_subagents() == []
        assert r.get_origin("t") is None
        assert r.disabled_tools == frozenset()


# NOTE: the module-level global registry + its ``@tool`` / ``@command``
# decorators were dead surface (nothing read them into an agent) and
# were removed (E7).  ``@kohakuterrarium.tool`` — the real adapter — is
# covered in ``tests/unit/core/agent/test_extensions.py``.
