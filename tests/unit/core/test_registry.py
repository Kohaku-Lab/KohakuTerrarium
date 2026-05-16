"""Unit tests for :mod:`kohakuterrarium.core.registry`."""

import pytest

from kohakuterrarium.core import registry as reg_mod
from kohakuterrarium.core.registry import (
    Registry,
    command,
    get_registry,
    register_command,
    register_tool,
    tool,
)
from kohakuterrarium.modules.tool.base import ExecutionMode, ToolResult

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


@pytest.fixture(autouse=True)
def reset_global_registry():
    """Avoid leaking the global Registry between tests."""
    snap = reg_mod._global_registry
    reg_mod._global_registry = None
    yield
    reg_mod._global_registry = snap


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


class TestClear:
    def test_clear_resets_everything(self):
        r = Registry()
        r.register_tool(_FakeTool(name="t"))
        r.register_command("c", lambda: 1)
        r.register_subagent("s", object())
        r.clear()
        assert r.list_tools() == []
        assert r.list_commands() == []
        assert r.list_subagents() == []


# ── global registry ──────────────────────────────────────────────


class TestGlobalRegistry:
    def test_lazy_singleton(self):
        a = get_registry()
        b = get_registry()
        assert a is b

    def test_register_tool_module_fn(self):
        register_tool(_FakeTool(name="globt"))
        assert get_registry().get_tool("globt").tool_name == "globt"

    def test_register_command_module_fn(self):
        def h():
            return "x"

        register_command("doit", h)
        assert get_registry().get_command("doit") is h


# ── decorators ───────────────────────────────────────────────────


class TestToolDecorator:
    def test_registers_via_decorator(self):
        @tool()
        class MyDec:
            @property
            def tool_name(self):
                return "deco"

            @property
            def description(self):
                return "from decorator"

            @property
            def execution_mode(self):
                return ExecutionMode.DIRECT

            async def execute(self, args, context=None):
                return ToolResult(output="ok")

        assert get_registry().get_tool("deco") is not None
        # Decorator returns the class unchanged.
        assert MyDec.__name__ == "MyDec"

    def test_class_without_tool_name_attr_not_registered(self):
        # Defensive — decorator silently skips registration when the
        # class lacks tool_name (so it can be safely applied to base
        # classes / shells without crashing).
        before = set(get_registry().list_tools())

        @tool()
        class NotATool:
            pass

        after = set(get_registry().list_tools())
        assert after == before


class TestCommandDecorator:
    def test_registers_via_decorator(self):
        @command("greet")
        def handler(**kwargs):
            return "hi"

        assert get_registry().get_command("greet") is handler

    def test_decorator_returns_function_unchanged(self):
        def hello():
            return "h"

        wrapped = command("h")(hello)
        assert wrapped is hello
