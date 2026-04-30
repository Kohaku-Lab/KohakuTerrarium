"""Unit tests for the built-in unified budget runtime plugin."""

import pytest

from kohakuterrarium.builtins.plugins.budget import BudgetPlugin
from kohakuterrarium.core.budget import AlarmState
from kohakuterrarium.modules.plugin.base import PluginBlockError, PluginContext


@pytest.mark.asyncio
async def test_budget_plugin_ticks_turn_walltime_and_tool_axes(monkeypatch):
    ticks = [10.0, 12.5]
    monkeypatch.setattr(
        "kohakuterrarium.builtins.plugins.budget.plugin.time.monotonic",
        lambda: ticks.pop(0) if ticks else 12.5,
    )
    plugin = BudgetPlugin(
        options={
            "turn_budget": [1, 3],
            "walltime_budget": [1, 5],
            "tool_call_budget": [1, 2],
        }
    )
    await plugin.on_load(PluginContext())

    await plugin.pre_llm_call([])
    await plugin.post_llm_call([], "ok", {})
    await plugin.post_tool_execute(object())

    assert plugin.budgets is not None
    assert plugin.budgets.turn is not None and plugin.budgets.turn.used == 1
    assert plugin.budgets.walltime is not None and plugin.budgets.walltime.used == 2.5
    assert plugin.budgets.tool_call is not None and plugin.budgets.tool_call.used == 1


def test_budget_prompt_contribution_lists_enabled_axes():
    plugin = BudgetPlugin(options={"turn_budget": [2, 4]})
    prompt = plugin.get_prompt_content(PluginContext())

    assert prompt is not None
    assert "Operating Constraints" in prompt
    assert "`turn`: soft 2; hard 4; crash 6." in prompt


def test_budget_prompt_contribution_empty_when_no_options():
    plugin = BudgetPlugin()
    assert plugin.get_prompt_content(PluginContext()) is None


@pytest.mark.asyncio
async def test_budget_alarm_injects_and_drains_alarms_next_turn():
    plugin = BudgetPlugin(options={"turn_budget": [1, 2]})
    await plugin.on_load(PluginContext())

    await plugin.pre_llm_call([])
    await plugin.post_llm_call([], "", {})
    messages = await plugin.pre_llm_call([{"role": "user", "content": "next"}])

    assert messages is not None
    assert messages[0]["role"] == "user"
    assert "[budget soft]" in messages[0]["content"]
    assert await plugin.pre_llm_call([{"role": "user", "content": "again"}]) is None


@pytest.mark.asyncio
async def test_budget_gate_blocks_tool_and_subagent_after_hard_wall():
    plugin = BudgetPlugin(options={"turn_budget": [1, 2]})
    await plugin.on_load(PluginContext())
    plugin.budgets.tick(turns=2)

    with pytest.raises(PluginBlockError, match="Budget exhausted"):
        await plugin.pre_tool_execute({}, tool_name="bash")
    with pytest.raises(PluginBlockError, match="dispatch disabled"):
        await plugin.pre_subagent_run("task", name="explore")


@pytest.mark.asyncio
async def test_budget_alarm_tracks_soft_hard_crash_sequence():
    """Drain across all three severities by manipulating the BudgetSet
    directly, then assert ``pre_llm_call`` exposes them in one batch.
    """
    plugin = BudgetPlugin(options={"turn_budget": [1, 2]})
    await plugin.on_load(PluginContext())

    # Push the budget through SOFT → HARD → CRASH and let post_llm_call
    # collect the transitions into ``_pending`` after each step.
    plugin.budgets.tick(turns=1)  # SOFT
    await plugin.post_llm_call([], "", {})
    plugin.budgets.tick(turns=1)  # HARD
    await plugin.post_llm_call([], "", {})
    plugin.budgets.tick(turns=1)  # CRASH
    await plugin.post_llm_call([], "", {})

    messages = await plugin.pre_llm_call([])
    assert messages is not None
    content = "\n".join(message["content"] for message in messages)
    assert AlarmState.SOFT.value in content
    assert AlarmState.HARD.value in content
    assert AlarmState.CRASH.value in content


def test_budget_plugin_accepts_flat_kwargs():
    """Loader path passes options as ``cls(**options)``."""
    plugin = BudgetPlugin(turn_budget=[5, 10], tool_call_budget=[20, 40])
    assert plugin.budgets is not None
    assert plugin.budgets.turn.hard == 10
    assert plugin.budgets.tool_call.hard == 40


def test_budget_plugin_no_options_is_inert():
    plugin = BudgetPlugin()
    assert plugin.budgets is None
