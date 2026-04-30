"""Sub-agent PluginManager wiring tests."""

from pathlib import Path
from typing import Any

import pytest

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


class _RecordingPlugin(BasePlugin):
    name = "recorder"

    def __init__(self):
        super().__init__()
        self.context: PluginContext | None = None
        self.pre_llm = 0
        self.post_llm = 0
        self.pre_tool = 0
        self.post_tool = 0

    async def on_load(self, context: PluginContext) -> None:
        self.context = context

    async def pre_llm_call(self, messages, **kwargs):
        self.pre_llm += 1
        return messages + [{"role": "user", "content": "plugin-injected"}]

    async def post_llm_call(self, messages, response, usage, **kwargs):
        self.post_llm += 1
        return None

    async def pre_tool_execute(self, args, **kwargs):
        self.pre_tool += 1
        return {**args, "tagged": True}

    async def post_tool_execute(self, result, **kwargs):
        self.post_tool += 1
        if isinstance(result, ToolResult):
            return ToolResult(
                output=result.output + " post", exit_code=result.exit_code
            )
        return None


class _BlockingPlugin(BasePlugin):
    name = "blocker"

    async def pre_tool_execute(self, args, **kwargs):
        raise PluginBlockError("blocked by sub-agent plugin")


class _EchoTool(BaseTool):
    @property
    def tool_name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "echo arguments"

    @property
    def execution_mode(self) -> ExecutionMode:
        return ExecutionMode.DIRECT

    async def _execute(self, args: dict[str, Any], **kwargs: Any) -> ToolResult:
        return ToolResult(output="tagged=" + str(args.get("tagged")), exit_code=0)


async def _build_subagent(plugin: _RecordingPlugin) -> SubAgent:
    registry = Registry()
    registry.register_tool(_EchoTool())
    manager = PluginManager()
    manager.register(plugin)
    subagent = SubAgent(
        config=SubAgentConfig(
            name="sub",
            tools=["echo"],
            system_prompt="You are a sub-agent.",
        ),
        parent_registry=registry,
        llm=ScriptedLLM(
            [
                "[/echo]value=1[echo/]",
                "done",
            ]
        ),
        agent_path=Path("."),
        plugin_manager=manager,
    )
    await load_and_wrap_plugins(manager, subagent, subagent.llm, Path("."))
    return subagent


@pytest.mark.asyncio
async def test_subagent_plugins_load_with_context():
    plugin = _RecordingPlugin()
    subagent = await _build_subagent(plugin)

    assert plugin.context is not None
    assert plugin.context.host_agent is subagent


@pytest.mark.asyncio
async def test_subagent_pre_post_llm_hooks_fire():
    plugin = _RecordingPlugin()
    subagent = await _build_subagent(plugin)

    result = await subagent.run("use the tool")

    assert result.success is True
    assert plugin.pre_llm >= 1
    assert plugin.post_llm >= 1
    assert "plugin-injected" in subagent.llm.call_log[0][-1]["content"]


@pytest.mark.asyncio
async def test_subagent_tool_execute_hooks_fire_and_transform():
    plugin = _RecordingPlugin()
    subagent = await _build_subagent(plugin)

    result = await subagent.run("use the tool")

    assert result.success is True
    assert plugin.pre_tool == 1
    assert plugin.post_tool == 1
    assert "tagged=True post" in "\n".join(
        message.get("content", "") for message in subagent.conversation.to_messages()
    )


@pytest.mark.asyncio
async def test_subagent_tool_block_becomes_tool_result_error():
    plugin = _BlockingPlugin()
    subagent = await _build_subagent(plugin)  # type: ignore[arg-type]

    result = await subagent.run("use the tool")

    assert result.success is True
    transcript = "\n".join(
        message.get("content", "") for message in subagent.conversation.to_messages()
    )
    assert "[echo] Error: blocked by sub-agent plugin" in transcript
