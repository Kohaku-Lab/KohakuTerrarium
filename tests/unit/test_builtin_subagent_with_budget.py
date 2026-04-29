"""Integration-ish coverage for builtin sub-agent runtime defaults."""

import pytest

from kohakuterrarium.builtins.subagent_catalog import (
    BUILTIN_SUBAGENTS,
    get_builtin_subagent_config,
)
from kohakuterrarium.core.registry import Registry
from kohakuterrarium.modules.subagent.manager import SubAgentManager
from kohakuterrarium.testing.llm import ScriptedLLM


class _SwitchableScriptedLLM(ScriptedLLM):
    def with_model(self, name: str):
        return self


@pytest.mark.asyncio
async def test_spawn_each_builtin_loads_default_runtime_plugins_and_budgets():
    for name in BUILTIN_SUBAGENTS:
        config = get_builtin_subagent_config(name)
        assert config is not None
        manager = SubAgentManager(
            parent_registry=Registry(), llm=_SwitchableScriptedLLM(["done"])
        )
        manager.register(config)

        job_id = await manager.spawn(name, "finish quickly", background=False)
        job = manager._jobs[job_id]
        subagent = job.subagent

        plugin_names = {plugin.name for plugin in subagent.plugins._plugins}
        assert {
            "budget.ticker",
            "budget.alarm",
            "budget.gate",
            "compact.auto",
        }.issubset(plugin_names)
        assert subagent.budgets is not None
        assert subagent.budgets.turn is not None
        assert subagent.budgets.turn.hard == config.turn_budget[1]
        assert subagent.budgets.walltime is None
        assert subagent.budgets.tool_call is not None
        assert subagent.budgets.tool_call.hard == config.tool_call_budget[1]
        prompt = subagent.conversation.to_messages()[0]["content"]
        assert "Operating Constraints" in prompt
