"""Parent budget gate behavior for sub-agent dispatch."""

from types import SimpleNamespace

import pytest

from kohakuterrarium.builtins.plugins.budget import BudgetPlugin
from kohakuterrarium.core.agent_pre_dispatch import run_pre_subagent_dispatch
from kohakuterrarium.modules.plugin.base import PluginContext
from kohakuterrarium.modules.plugin.manager import PluginManager
from kohakuterrarium.parsing import SubAgentCallEvent


class _FakeController:
    def __init__(self):
        self.events: list = []

    def push_event_sync(self, event) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_parent_budget_gate_blocks_subagent_dispatch_as_feedback_event():
    plugin = BudgetPlugin(options={"turn_budget": [1, 2]})
    plugin.budgets.tick(turns=2)
    manager = PluginManager()
    manager.register(plugin)
    agent = SimpleNamespace(
        config=SimpleNamespace(name="parent"),
        executor=SimpleNamespace(_working_dir="."),
        llm=SimpleNamespace(model="scripted"),
        plugins=manager,
    )
    await manager.load_all(
        PluginContext(agent_name="parent", model="scripted", _host_agent=agent)
    )
    controller = _FakeController()

    result = await run_pre_subagent_dispatch(
        agent,
        SubAgentCallEvent(name="child", args={"task": "work"}),
        controller,
    )

    assert result is None
    assert len(controller.events) == 1
    event = controller.events[0]
    assert event.type == "tool_complete"
    assert "Budget exhausted" in event.content
    assert "sub-agent dispatch disabled" in event.context.get("error", "")
