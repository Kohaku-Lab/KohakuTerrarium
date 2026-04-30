"""Tests for the ``pre_tool_dispatch`` plugin hook (cluster B.2).

Covers rename, veto, pass-through, invalid-rename and chain semantics.
The dispatch function is unit-tested directly against a minimal fake
agent; the full agent path is exercised by the surrounding suite.
"""

from types import SimpleNamespace

import pytest

from kohakuterrarium.core.agent_pre_dispatch import run_pre_tool_dispatch
from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.parsing import ToolCallEvent


class _FakeRegistry:
    def __init__(self, tools: list[str]):
        self._tools = list(tools)

    def list_tools(self) -> list[str]:
        return list(self._tools)


class _FakeConversation:
    def __init__(self):
        self.appended: list[dict] = []

    def append(self, role: str, content: str, **kwargs) -> None:
        self.appended.append({"role": role, "content": content, **kwargs})


class _FakeController:
    def __init__(self, native: bool = False):
        self.config = SimpleNamespace(tool_format="native" if native else "bracket")
        self.conversation = _FakeConversation()
        self.events: list = []

    def push_event_sync(self, event) -> None:
        self.events.append(event)


def _make_agent(tools: list[str], plugins: list[BasePlugin]) -> SimpleNamespace:
    manager = PluginManager()
    for p in plugins:
        manager.register(p)
    # Give the manager a benign load context so the gating can resolve.
    manager._load_context = PluginContext(agent_name="swe", model="test/model")
    return SimpleNamespace(
        config=SimpleNamespace(name="swe"),
        executor=SimpleNamespace(_working_dir="."),
        llm=SimpleNamespace(model="test/model"),
        budgets=object(),
        registry=_FakeRegistry(tools),
        plugins=manager,
    )


# ── Plugins under test ───────────────────────────────────────────────


class _RenamePlugin(BasePlugin):
    name = "rename"

    async def pre_tool_dispatch(self, call, context):
        # Rename bash → safe_bash; pass everything else through.
        if call.name == "bash":
            return ToolCallEvent(name="safe_bash", args=dict(call.args), raw=call.raw)
        return None


class _VetoPlugin(BasePlugin):
    name = "veto"

    async def pre_tool_dispatch(self, call, context):
        if call.name == "dangerous":
            raise PluginBlockError("vetoed by policy")
        return None


class _AddArgsPlugin(BasePlugin):
    name = "add_args"

    async def pre_tool_dispatch(self, call, context):
        new_args = {**call.args, "audited": True}
        return ToolCallEvent(name=call.name, args=new_args, raw=call.raw)


class _RenameToUnknownPlugin(BasePlugin):
    name = "rename_unknown"

    async def pre_tool_dispatch(self, call, context):
        return ToolCallEvent(name="does_not_exist", args=dict(call.args), raw=call.raw)


class _NoopPlugin(BasePlugin):
    name = "noop"
    # pre_tool_dispatch not overridden — default no-op.


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rename_routes_to_new_tool():
    agent = _make_agent(["bash", "safe_bash"], [_RenamePlugin()])
    controller = _FakeController()
    event = ToolCallEvent(name="bash", args={"cmd": "ls"})
    result = await run_pre_tool_dispatch(agent, event, controller)
    assert result is not None
    assert result.name == "safe_bash"
    assert result.args == {"cmd": "ls"}


@pytest.mark.asyncio
async def test_veto_becomes_tool_result():
    agent = _make_agent(["bash", "dangerous"], [_VetoPlugin()])
    controller = _FakeController()
    event = ToolCallEvent(name="dangerous", args={})
    result = await run_pre_tool_dispatch(agent, event, controller)
    assert result is None
    # Veto synthesises a tool_complete event in text mode.
    assert len(controller.events) == 1
    synth = controller.events[0]
    assert synth.type == "tool_complete"
    assert "vetoed" in synth.context.get("error", "")
    assert "vetoed" in synth.content


@pytest.mark.asyncio
async def test_none_passes_through():
    agent = _make_agent(["bash"], [_NoopPlugin()])
    controller = _FakeController()
    event = ToolCallEvent(name="bash", args={"cmd": "pwd"})
    result = await run_pre_tool_dispatch(agent, event, controller)
    assert result is event


@pytest.mark.asyncio
async def test_pre_dispatch_context_exposes_runtime_accessors():
    seen_context = None

    class ContextPlugin(BasePlugin):
        name = "context"

        async def pre_tool_dispatch(self, call, context):
            nonlocal seen_context
            seen_context = context
            return None

    agent = _make_agent(["bash"], [ContextPlugin()])
    controller = _FakeController()
    event = ToolCallEvent(name="bash", args={"cmd": "pwd"})
    result = await run_pre_tool_dispatch(agent, event, controller)

    assert result is event
    assert seen_context is not None
    assert seen_context.registry is agent.registry
    assert seen_context.host_agent is agent


@pytest.mark.asyncio
async def test_rename_to_unknown_vetoes():
    agent = _make_agent(["bash"], [_RenameToUnknownPlugin()])
    controller = _FakeController()
    event = ToolCallEvent(name="bash", args={})
    result = await run_pre_tool_dispatch(agent, event, controller)
    assert result is None
    assert len(controller.events) == 1
    assert "unknown tool after rewrite" in controller.events[0].content


@pytest.mark.asyncio
async def test_chain_composes_in_priority_order():
    """Second plugin sees the first plugin's rewrite."""
    rename = _RenamePlugin()
    rename.priority = 10  # run first
    add_args = _AddArgsPlugin()
    add_args.priority = 20  # run second
    agent = _make_agent(["bash", "safe_bash"], [rename, add_args])
    controller = _FakeController()
    event = ToolCallEvent(name="bash", args={"cmd": "uptime"})
    result = await run_pre_tool_dispatch(agent, event, controller)
    assert result is not None
    assert result.name == "safe_bash"
    assert result.args == {"cmd": "uptime", "audited": True}


@pytest.mark.asyncio
async def test_veto_short_circuits_chain():
    """Plugins after a veto should not run."""
    seen_by_second: list[str] = []

    class SecondPlugin(BasePlugin):
        name = "second"

        async def pre_tool_dispatch(self, call, context):
            seen_by_second.append(call.name)
            return None

    veto = _VetoPlugin()
    veto.priority = 5
    second = SecondPlugin()
    second.priority = 20
    agent = _make_agent(["dangerous"], [veto, second])
    controller = _FakeController()
    event = ToolCallEvent(name="dangerous", args={})
    result = await run_pre_tool_dispatch(agent, event, controller)
    assert result is None
    assert seen_by_second == []
