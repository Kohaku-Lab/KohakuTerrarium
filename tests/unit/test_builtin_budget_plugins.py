"""Unit tests for the built-in budget runtime plugins."""

import pytest

from kohakuterrarium.builtins.plugins.budget.alarm import BudgetAlarmPlugin
from kohakuterrarium.builtins.plugins.budget.gate import BudgetGatePlugin
from kohakuterrarium.builtins.plugins.budget.ticker import BudgetTickerPlugin
from kohakuterrarium.core.budget import AlarmState, BudgetAxis, BudgetSet
from kohakuterrarium.modules.plugin.base import PluginBlockError, PluginContext


class _Host:
    def __init__(self, budgets: BudgetSet | None):
        self.budgets = budgets


@pytest.mark.asyncio
async def test_budget_ticker_ticks_turn_walltime_and_tool_axes(monkeypatch):
    ticks = [10.0, 12.5]
    monkeypatch.setattr(
        "kohakuterrarium.builtins.plugins.budget.ticker.time.monotonic",
        lambda: ticks.pop(0) if ticks else 12.5,
    )
    budgets = BudgetSet(
        turn=BudgetAxis(name="turn", soft=1, hard=3),
        walltime=BudgetAxis(name="walltime", soft=1, hard=5),
        tool_call=BudgetAxis(name="tool_call", soft=1, hard=2),
    )
    plugin = BudgetTickerPlugin()
    await plugin.on_load(PluginContext(_host_agent=_Host(budgets)))

    await plugin.pre_llm_call([])
    await plugin.post_llm_call([], "ok", {})
    await plugin.post_tool_execute(object())

    assert budgets.turn is not None and budgets.turn.used == 1
    assert budgets.walltime is not None and budgets.walltime.used == 2.5
    assert budgets.tool_call is not None and budgets.tool_call.used == 1


def test_budget_alarm_prompt_contribution_lists_enabled_axes():
    budgets = BudgetSet(turn=BudgetAxis(name="turn", soft=2, hard=4))
    plugin = BudgetAlarmPlugin()
    prompt = plugin.get_prompt_content(PluginContext(_host_agent=_Host(budgets)))

    assert prompt is not None
    assert "Operating Constraints" in prompt
    assert "`turn`: soft 2; hard 4; crash 6." in prompt


def test_budget_alarm_prompt_contribution_empty_when_no_budget():
    plugin = BudgetAlarmPlugin()
    assert plugin.get_prompt_content(PluginContext(_host_agent=_Host(None))) is None


@pytest.mark.asyncio
async def test_budget_alarm_injects_and_drains_alarms_next_turn():
    budgets = BudgetSet(turn=BudgetAxis(name="turn", soft=1, hard=2))
    plugin = BudgetAlarmPlugin()
    await plugin.on_load(PluginContext(_host_agent=_Host(budgets)))

    budgets.tick(turns=1)
    await plugin.post_llm_call([], "", {})
    messages = await plugin.pre_llm_call([{"role": "user", "content": "next"}])

    assert messages is not None
    assert messages[0]["role"] == "user"
    assert "[budget soft]" in messages[0]["content"]
    assert await plugin.pre_llm_call([{"role": "user", "content": "again"}]) is None


@pytest.mark.asyncio
async def test_budget_gate_blocks_tool_and_subagent_after_hard_wall():
    budgets = BudgetSet(turn=BudgetAxis(name="turn", soft=1, hard=2))
    budgets.tick(turns=2)
    plugin = BudgetGatePlugin()
    await plugin.on_load(PluginContext(_host_agent=_Host(budgets)))

    with pytest.raises(PluginBlockError, match="Budget exhausted"):
        await plugin.pre_tool_execute({}, tool_name="bash")
    with pytest.raises(PluginBlockError, match="dispatch disabled"):
        await plugin.pre_subagent_run("task", name="explore")


@pytest.mark.asyncio
async def test_budget_alarm_tracks_soft_hard_crash_sequence():
    budgets = BudgetSet(turn=BudgetAxis(name="turn", soft=1, hard=2))
    plugin = BudgetAlarmPlugin()
    await plugin.on_load(PluginContext(_host_agent=_Host(budgets)))

    budgets.tick(turns=1)
    await plugin.post_llm_call([], "", {})
    budgets.tick(turns=1)
    await plugin.post_llm_call([], "", {})
    budgets.tick(turns=1)
    await plugin.post_llm_call([], "", {})

    messages = await plugin.pre_llm_call([])
    assert messages is not None
    content = "\n".join(message["content"] for message in messages)
    assert AlarmState.SOFT.value in content
    assert AlarmState.HARD.value in content
    assert AlarmState.CRASH.value in content
