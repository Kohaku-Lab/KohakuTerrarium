"""Integration-ish coverage for builtin sub-agent runtime defaults."""

import pytest

from kohakuterrarium.builtins.plugins.budget import BudgetPlugin
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
async def test_spawn_each_builtin_attaches_budget_plugin_with_options():
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

        plugin_by_name = {plugin.name: plugin for plugin in subagent.plugins._plugins}
        assert "budget" in plugin_by_name
        assert "compact.auto" in plugin_by_name

        budget_plugin = plugin_by_name["budget"]
        assert isinstance(budget_plugin, BudgetPlugin)
        assert budget_plugin.budgets is not None
        assert budget_plugin.budgets.turn is not None
        assert budget_plugin.budgets.turn.hard == 60
        assert budget_plugin.budgets.walltime is None
        assert budget_plugin.budgets.tool_call is not None
        assert budget_plugin.budgets.tool_call.hard == 100

        # Sub-agent core no longer carries a ``budgets`` attribute —
        # budget state is fully owned by the plugin instance above.
        assert not hasattr(subagent, "budgets")

        prompt = subagent.conversation.to_messages()[0]["content"]
        assert "Operating Constraints" in prompt
