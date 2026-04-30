"""Regression: sub-agent plugin chain must NOT bleed onto parent tool calls.

Background bug: ``Agent._apply_plugin_hooks`` and the sub-agent's
``load_and_wrap_plugins`` both did ``tool.execute = pm.wrap_method(...)``.
Since sub-agents reuse the parent's tool instances via
``parent_registry.get_tool(...)``, the sub-agent's wrap clobbered the
parent's wrap on the same shared object. After a sub-agent exhausted
its budget, its ``pre_tool_execute`` would block subsequent **parent**
tool calls — the budget plugin's ``Budget exhausted (tool_call). Tools
are no longer available...`` text would surface on the parent's bash.

The fix: plugin hooks fire at the call site, never by mutating
``tool.execute``. Each agent's own plugin manager wraps the call
locally — parent's executor wraps with parent's plugins, sub-agent's
``_execute_tools`` wraps with sub-agent's plugins.
"""

from pathlib import Path
from typing import Any

import pytest

from kohakuterrarium.core.executor import Executor
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.plugin.base import (
    BasePlugin,
    PluginBlockError,
    PluginContext,
)
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.modules.subagent.base import SubAgent
from kohakuterrarium.modules.subagent.config import SubAgentConfig
from kohakuterrarium.modules.subagent.runtime_builders import load_and_wrap_plugins
from kohakuterrarium.modules.tool.base import BaseTool, ExecutionMode, ToolResult
from kohakuterrarium.testing.llm import ScriptedLLM


class _BlockAfterN(BasePlugin):
    """Blocks ``pre_tool_execute`` after ``threshold`` invocations.

    Mirrors the BudgetPlugin's gate behaviour without depending on the
    BudgetPlugin so the test is about isolation, not budget config.
    """

    name = "block-after-n"
    priority = 5

    def __init__(self, threshold: int = 1):
        super().__init__()
        self._threshold = threshold
        self.calls = 0

    async def pre_tool_execute(self, args: dict, **kwargs: Any) -> None:
        self.calls += 1
        if self.calls > self._threshold:
            raise PluginBlockError("blocked by sub-agent plugin")
        return None


class _EchoTool(BaseTool):
    @property
    def tool_name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "echo args"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        return ToolResult(output=str(args.get("v", "")), exit_code=0)


@pytest.mark.asyncio
async def test_subagent_plugin_does_not_block_parent_tool_calls():
    """The actual regression: sub-agent's hook list intercepts only
    sub-agent tool calls, never the parent's.
    """
    parent_registry = Registry()
    parent_registry.register_tool(_EchoTool())

    # Sub-agent uses the same tool instance via parent_registry.
    sub_plugins = PluginManager()
    blocker = _BlockAfterN(threshold=1)
    sub_plugins.register(blocker)
    subagent = SubAgent(
        config=SubAgentConfig(name="sub", tools=["echo"], system_prompt="x"),
        parent_registry=parent_registry,
        llm=ScriptedLLM(["done"]),
        agent_path=Path("."),
        plugin_manager=sub_plugins,
    )
    await load_and_wrap_plugins(sub_plugins, subagent, subagent.llm, Path("."))

    # Use up the sub-agent's threshold via its dispatcher.
    await subagent._execute_tools([_make_tool_call("echo", {"v": 1})])
    sub_results_after_first = await subagent._execute_tools(
        [_make_tool_call("echo", {"v": 2})]
    )
    # Sub-agent's second call should be blocked.
    assert "Error: blocked by sub-agent plugin" in sub_results_after_first

    # Parent's executor invokes the SAME tool instance via its own
    # registry. With the bug, ``tool.execute`` had been rebound by
    # the sub-agent and would now raise PluginBlockError. After the
    # fix, the parent's tool still runs cleanly.
    parent_executor = Executor()
    for tool_name in parent_registry.list_tools():
        parent_executor.register_tool(parent_registry.get_tool(tool_name))

    class _ParentNoPlugins:
        plugins = None  # parent has no plugin manager — no hooks should fire

    parent_executor._agent = _ParentNoPlugins()
    tool = parent_executor.get_tool("echo")
    assert tool is parent_registry.get_tool(
        "echo"
    ), "tool instance must be shared between parent and sub-agent registries"

    # The fix: this call must succeed because the sub-agent's plugin
    # chain is NOT attached to the shared tool instance.
    exec_fn = parent_executor._wrap_tool_execute(tool, {"v": 99}, job_id="parent_job")
    result = await exec_fn({"v": 99}, context=None)
    assert isinstance(result, ToolResult)
    assert result.exit_code == 0
    assert result.output == "99"


@pytest.mark.asyncio
async def test_parent_plugin_isolated_from_subagent_call_path():
    """The mirror case: the parent's plugins must NOT fire when the
    sub-agent runs the same shared tool through ITS dispatcher.
    """
    parent_registry = Registry()
    parent_registry.register_tool(_EchoTool())
    parent_blocker = _BlockAfterN(threshold=0)  # blocks on first call
    parent_plugins = PluginManager()
    parent_plugins.register(parent_blocker)
    await parent_plugins.load_all(PluginContext())

    # Sub-agent has a separate (empty) plugin manager.
    sub_plugins = PluginManager()
    subagent = SubAgent(
        config=SubAgentConfig(name="sub", tools=["echo"], system_prompt="x"),
        parent_registry=parent_registry,
        llm=ScriptedLLM(["done"]),
        agent_path=Path("."),
        plugin_manager=sub_plugins,
    )
    await load_and_wrap_plugins(sub_plugins, subagent, subagent.llm, Path("."))

    # Sub-agent's dispatcher wraps with sub_plugins (empty) — parent's
    # blocker must not fire here even though the tool instance is
    # shared.
    out = await subagent._execute_tools([_make_tool_call("echo", {"v": 7})])
    assert "Error" not in out
    assert "7" in out
    assert (
        parent_blocker.calls == 0
    ), "parent plugin must not fire on sub-agent's tool dispatch path"


def _make_tool_call(name: str, args: dict[str, Any]):
    from kohakuterrarium.parsing import ToolCallEvent

    return ToolCallEvent(name=name, args=args)
